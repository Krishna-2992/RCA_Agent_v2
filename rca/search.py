"""Hybrid search over the knowledge base.

One SQL statement per query runs three channels over each searchable table:

- dense:  cosine distance on the text-embedding-3-small vectors
- bm25:   inner product on the BM25 sparse vectors (our tokenizer)
- exact:  rows tagged with the query's canonical IDs (entity_links)

Each channel's ranked list is fused with reciprocal rank fusion, so no channel's
score scale can swamp another. Incident hits are collapsed to one per event.
Cards are then ranked on their own hits plus the incidents and service requests
that back them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from rca.bm25 import DIMENSIONS, Bm25Index, tokenize
from rca.db import Database, to_sparsevec, to_vector
from rca.entities import Entities, extract
from rca.llm import embed, embed_one, embedding_model


RRF_K = 60
PER_CHANNEL = 30
CHANNELS = ("dense", "bm25", "exact")

TABLES = {
    # table: (key column, entity_links item_type)
    "pattern_cards": ("id", "pattern_card"),
    "incidents": ("number", "incident"),
    "service_requests": ("number", "service_request"),
    "doc_chunks": ("id", "doc_chunk"),
}

# How much a matching incident or service request counts towards the card it backs.
INCIDENT_SUPPORT = 1.0
REQUEST_SUPPORT = 0.5
SUPPORT_EVENTS = 10
SUPPORT_REQUESTS = 5

# Identifier agreement. When a card names specific values of a kind (abends,
# error codes, files...) and the report has a different value of that kind,
# that is evidence against the card. Weights say how decisive each kind is.
ID_WEIGHT = {"abend": 1.0, "error_code": 1.0, "file": 1.0, "dataset": 0.7, "job": 0.6,
             "reason": 0.4, "program": 0.3}
AGREEMENT_EFFECT = 0.6            # card score x (1 + 0.6 * agreement), agreement in [-1, 1]
CONFLICTING_TICKET = 0.3          # a ticket whose abend/error/file contradicts the report counts 30%
TICKET_CONFLICT_KINDS = ("abend", "error_code", "file")

# REMAN.NDX is the shared security and configuration file every Reman program reads, so naming
# it says little about which failure this is.
GENERIC_FILES = {"REMAN.NDX"}
GENERIC_WEIGHT = 0.2

ENTITY_KIND = {"error_codes": "error_code", "abends": "abend", "files": "file", "reasons": "reason",
               "datasets": "dataset", "jobs": "job", "programs": "program"}


@dataclass
class Hit:
    table: str
    id: str
    score: float
    ranks: dict[str, int] = field(default_factory=dict)      # channel -> 1-based rank


@dataclass
class CardHit:
    id: str
    score: float
    own: float                                               # from the card's own text
    support: dict[str, float] = field(default_factory=dict)  # incident/SR id -> contribution
    agreement: float = 0.0                                   # -1 (IDs contradict) .. 1 (IDs match)
    matched_ids: list[str] = field(default_factory=list)
    conflicting_ids: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    query: str
    entities: Entities
    cards: list[CardHit]
    incidents: list[Hit]
    service_requests: list[Hit]
    docs: list[Hit]


def _channel_sql(table: str, key: str, item_type: str) -> str:
    return f"""
    (select '{table}' as t, 'dense' as ch, id, row_number() over (order by d) as r
       from (select {key} as id, embedding <=> %(qv)s::vector as d
               from {table} where embedding is not null order by d limit %(k)s) x)
    union all
    (select '{table}', 'bm25', id, row_number() over (order by d)
       from (select {key} as id, bm25 <#> %(qs)s::sparsevec as d
               from {table} where bm25 is not null and bm25 <#> %(qs)s::sparsevec < 0
              order by d limit %(k)s) x)
    union all
    (select '{table}', 'exact', id, row_number() over (order by n desc, id)
       from (select l.item_id as id, count(*) as n
               from entity_links l join entities e on e.id = l.entity_id
              where l.item_type = '{item_type}' and e.canonical = any(%(ids)s)
              group by l.item_id order by n desc, l.item_id limit %(k)s) x)
    """


SEARCH_SQL = "\nunion all\n".join(_channel_sql(t, key, kind) for t, (key, kind) in TABLES.items())
LOOKUP_SQL = "\nunion all\n".join(_channel_sql(t, *TABLES[t]) for t in ("doc_chunks", "incidents"))


class Searcher:
    """Holds one connection plus the small lookup tables search needs."""

    def __init__(self, db: Database | None = None):
        self.conn = db or Database()

        vocab, df = {}, {}
        for token, idx, count in self.conn.execute("select token, idx, df from bm25_vocab"):
            vocab[token], df[token] = idx, count
        self.bm25 = Bm25Index(vocab=vocab)
        self.bm25.df.update(df)

        self.card_evidence: dict[str, list[str]] = {}
        for card_id, item_id in self.conn.execute(
            "select card_id, item_id from card_evidence where item_type in ('incident', 'service_request')"
        ):
            self.card_evidence.setdefault(item_id, []).append(card_id)

        self.event_of: dict[str, str] = dict(self.conn.execute("select number, event_id from incidents").fetchall())
        self.model = embedding_model()

        # card -> kind -> values it names; incident -> kind -> values it mentions
        self.card_signals: dict[str, dict[str, set[str]]] = {}
        for card_id, signals in self.conn.execute("select id, body->'signals' from pattern_cards"):
            self.card_signals[card_id] = {kind: set(signals.get(field) or []) for field, kind in ENTITY_KIND.items()}
        self.incident_ids: dict[str, dict[str, set[str]]] = {}
        for number, kind, value in self.conn.execute(
            "select l.item_id, e.type, e.canonical from entity_links l join entities e on e.id = l.entity_id "
            "where l.item_type = 'incident' and e.type = any(%s)", (list(TICKET_CONFLICT_KINDS),)
        ):
            self.incident_ids.setdefault(number, {}).setdefault(kind, set()).add(value)

    def close(self) -> None:
        self.conn.close()

    # -- embeddings, cached like the corpus ---------------------------------

    def _query_vector(self, text: str) -> str:
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        row = self.conn.execute(
            "select embedding::text from embedding_cache where content_hash = %s and model = %s",
            (content_hash, self.model),
        ).fetchone()
        if row:
            return row[0]
        vector = to_vector(embed_one(text))
        self.conn.execute(
            "insert into embedding_cache (content_hash, model, embedding) values (%s, %s, %s) on conflict do nothing",
            (content_hash, self.model, vector),
        )
        return vector

    # -- search ----------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        k: int = 8,
        channels: tuple[str, ...] = CHANNELS,
        exclude_events: frozenset[str] = frozenset(),
        entities: Entities | None = None,
    ) -> SearchResult:
        """`entities` overrides extraction, so IDs can come from the user's own words
        even when `query` carries extra wording from the rewrite step."""
        entities = entities or extract(query)
        ids = sorted(entities.ids())
        sparse = to_sparsevec(self.bm25.query_vector(query + "\n" + " ".join(ids)), DIMENSIONS)

        rows = self.conn.execute(SEARCH_SQL, {
            "qv": self._query_vector(query), "qs": sparse, "ids": ids, "k": PER_CHANNEL,
        }).fetchall()

        fused: dict[tuple[str, str], Hit] = {}
        for table, channel, item_id, rank in rows:
            if channel not in channels:
                continue
            hit = fused.setdefault((table, item_id), Hit(table, item_id, 0.0))
            hit.ranks[channel] = rank
            hit.score += 1.0 / (RRF_K + rank)

        def ranked(table: str) -> list[Hit]:
            return sorted((h for h in fused.values() if h.table == table), key=lambda h: -h.score)

        incidents = []
        seen_events = set()
        for hit in ranked("incidents"):
            event = self.event_of.get(hit.id, hit.id)
            if event in exclude_events or event in seen_events:
                continue
            seen_events.add(event)
            incidents.append(hit)

        requests = ranked("service_requests")
        query_ids = {kind: set(getattr(entities, field)) for field, kind in ENTITY_KIND.items() if getattr(entities, field)}
        # Only the strongest hits vote, so a card with many tickets can't win on volume alone.
        cards = self._rank_cards(ranked("pattern_cards"), incidents[:SUPPORT_EVENTS],
                                 requests[:SUPPORT_REQUESTS], query_ids)

        return SearchResult(
            query=query,
            entities=entities,
            cards=cards,
            incidents=incidents[:k],
            service_requests=requests[:k],
            docs=ranked("doc_chunks")[:k],
        )

    def lookup(self, query: str, *, k: int = 3, exclude_events: frozenset[str] = frozenset()
               ) -> tuple[list[Hit], list[Hit]]:
        """A targeted search over documentation and incidents only, for the investigation step.
        Returns (docs, incidents), incidents collapsed to one per event."""
        ids = sorted(extract(query).ids())
        sparse = to_sparsevec(self.bm25.query_vector(query + "\n" + " ".join(ids)), DIMENSIONS)
        rows = self.conn.execute(LOOKUP_SQL, {
            "qv": self._query_vector(query), "qs": sparse, "ids": ids, "k": PER_CHANNEL,
        }).fetchall()
        fused: dict[tuple[str, str], Hit] = {}
        for table, channel, item_id, rank in rows:
            hit = fused.setdefault((table, item_id), Hit(table, item_id, 0.0))
            hit.ranks[channel] = rank
            hit.score += 1.0 / (RRF_K + rank)
        docs = sorted((h for h in fused.values() if h.table == "doc_chunks"), key=lambda h: -h.score)
        incidents, seen = [], set()
        for hit in sorted((h for h in fused.values() if h.table == "incidents"), key=lambda h: -h.score):
            event = self.event_of.get(hit.id, hit.id)
            if event not in exclude_events and event not in seen:
                seen.add(event)
                incidents.append(hit)
        return docs[:k], incidents[:k]

    def lookup_many(self, queries: list[str], *, k: int = 3, exclude_events: frozenset[str] = frozenset()
                    ) -> list[tuple[list[Hit], list[Hit]]]:
        """`lookup` for several queries, embedding the uncached ones in a single call."""
        hashes = {q: hashlib.sha256(q.encode()).hexdigest() for q in queries}
        cached = {h for (h,) in self.conn.execute(
            "select content_hash from embedding_cache where content_hash = any(%s) and model = %s",
            (list(hashes.values()), self.model))}
        missing = [q for q in dict.fromkeys(queries) if hashes[q] not in cached]
        if missing:
            for text, vector in zip(missing, embed(missing)):
                self.conn.execute(
                    "insert into embedding_cache (content_hash, model, embedding) values (%s, %s, %s) "
                    "on conflict do nothing", (hashes[text], self.model, to_vector(vector)))
        return [self.lookup(q, k=k, exclude_events=exclude_events) for q in queries]

    def _ticket_conflicts(self, number: str, query_ids: dict[str, set[str]]) -> bool:
        mentioned = self.incident_ids.get(number, {})
        return any(query_ids.get(kind) and mentioned.get(kind) and not (query_ids[kind] & mentioned[kind])
                   for kind in TICKET_CONFLICT_KINDS)

    def _agreement(self, card_id: str, query_ids: dict[str, set[str]]) -> tuple[float, list[str], list[str]]:
        signals = self.card_signals.get(card_id, {})
        total = matched_weight = conflict_weight = 0.0
        matched, conflicting = [], []
        for kind, values in query_ids.items():
            for value in values:
                weight = GENERIC_WEIGHT if value in GENERIC_FILES else ID_WEIGHT.get(kind, 0.0)
                total += weight
                if value in signals.get(kind, set()):
                    matched_weight += weight
                    matched.append(value)
                elif signals.get(kind):
                    conflict_weight += weight
                    conflicting.append(value)
        if not total:
            return 0.0, [], []
        return (matched_weight - conflict_weight) / total, sorted(matched), sorted(conflicting)

    def _rank_cards(self, own: list[Hit], incidents: list[Hit], requests: list[Hit],
                    query_ids: dict[str, set[str]]) -> list[CardHit]:
        cards: dict[str, CardHit] = {h.id: CardHit(h.id, h.score, h.score) for h in own}

        for hits, weight in ((incidents, INCIDENT_SUPPORT), (requests, REQUEST_SUPPORT)):
            for hit in hits:
                factor = CONFLICTING_TICKET if self._ticket_conflicts(hit.id, query_ids) else 1.0
                for card_id in self.card_evidence.get(hit.id, []):
                    card = cards.setdefault(card_id, CardHit(card_id, 0.0, 0.0))
                    contribution = weight * factor * hit.score
                    card.support[hit.id] = contribution
                    card.score += contribution

        for card in cards.values():
            card.agreement, card.matched_ids, card.conflicting_ids = self._agreement(card.id, query_ids)
            card.score *= 1 + AGREEMENT_EFFECT * card.agreement

        return sorted(cards.values(), key=lambda c: -c.score)
