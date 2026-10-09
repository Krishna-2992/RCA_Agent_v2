"""Loads the knowledge base into Supabase.

    python -m scripts.ingest

Full reload in one transaction: every knowledge table is rebuilt from the source
files, so a re-run always matches the files exactly. Embeddings are reused for
any row whose text has not changed (matched by content hash), so a re-run only
pays Azure for new or edited text. `runs` and `feedback` are never touched.
"""

from __future__ import annotations

import hashlib
import sys
import time
from dataclasses import dataclass, field

import yaml
from psycopg.types.json import Jsonb

from rca import config
from rca.bm25 import DIMENSIONS, Bm25Index, tokenize
from rca.cards import load_cards
from rca.db import connect, to_sparsevec, to_vector
from rca.docs import load_doc_chunks
from rca.entities import Entities, extract, load_catalogue
from rca.events import event_of, group_events
from rca.llm import EMBED_BATCH, embed, embedding_model
from rca.servicenow import load_samples


SEARCHABLE = ("pattern_cards", "incidents", "service_requests", "doc_chunks")
LINK_TYPE = {"pattern_cards": "pattern_card", "incidents": "incident",
             "service_requests": "service_request", "doc_chunks": "doc_chunk"}
ENTITY_FIELDS = {"programs": "program", "jobs": "job", "datasets": "dataset", "files": "file",
                 "error_codes": "error_code", "abends": "abend", "reasons": "reason",
                 "reports": "report", "applications": "application"}


@dataclass
class Row:
    table: str
    id: str
    text: str                      # what gets embedded and BM25-indexed
    values: dict                   # table-specific columns
    dense: bool = True             # False = searchable by BM25 and exact ID only
    entities: Entities = field(default_factory=Entities)
    tokens: list[str] = field(default_factory=list)
    embedding: str | None = None
    bm25: str | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def build_rows() -> tuple[list[Row], dict]:
    samples = load_samples(config.SAMPLES_XLSX)
    deck = load_cards()
    events = group_events(samples.incidents)
    owner = event_of(events)

    rows: list[Row] = []

    for card in deck.cards:
        rows.append(Row("pattern_cards", card.id, card.search_text(), {
            "version": deck.version, "status": card.status, "title": card.title,
            "system": card.system, "body": Jsonb(card.model_dump(mode="json")),
        }))

    for i in samples.incidents:
        text = "\n".join(part for part in (
            i.symptom_text,
            f"Root cause: {i.root_cause}" if i.root_cause else "",
            f"Action taken: {i.action_taken}" if i.action_taken else "",
            f"Service: {i.service}",
        ) if part)
        rows.append(Row("incidents", i.number, text, {
            "event_id": owner[i.number], "parent_number": i.parent, "priority": i.priority,
            "service": i.service, "channel": i.channel, "opened_at": i.opened_at, "resolved_at": i.resolved_at,
            "short_description": i.short_description, "description": i.description,
            "root_cause": i.root_cause, "action_taken": i.action_taken,
            "resolution_notes": i.resolution_notes, "resolved_by": i.resolved_by,
        }))

    for s in samples.service_requests:
        text = "\n".join(part for part in (s.symptom_text, s.work_notes) if part)
        rows.append(Row("service_requests", s.number, text, {
            "opened_at": s.opened_at, "closed_at": s.closed_at, "service": s.service,
            "short_description": s.short_description, "description": s.description, "work_notes": s.work_notes,
        }))

    # Business rules (74% of chunks) get BM25 and exact-ID search only: their measured
    # value in POC v1 came from keyword matching, and their templated Given/When/Then
    # text adds little to meaning-based search. This cuts embedding volume ~4x.
    for chunk in load_doc_chunks():
        rows.append(Row("doc_chunks", chunk.id, chunk.search_text(), {
            "source_file": chunk.source_file, "program": chunk.program, "application": chunk.application,
            "section_path": chunk.section_path, "doc_type": chunk.doc_type, "trust_tier": chunk.trust_tier,
            "role": chunk.role, "text": chunk.text,
        }, dense=chunk.doc_type != "business_rules"))

    extras = {"deck": deck, "events": events, "changes": samples.changes}
    return rows, extras


def index_rows(rows: list[Row]) -> Bm25Index:
    catalogue = load_catalogue()
    for row in rows:
        row.entities = extract(row.text, catalogue)
        # Canonical IDs are appended so "IN001.MST" in a ticket still matches "IN0011.MST".
        row.tokens = tokenize(row.text + "\n" + " ".join(sorted(row.entities.ids())))

    index = Bm25Index.build([row.tokens for row in rows])
    for table in SEARCHABLE:
        members = [r for r in rows if r.table == table]
        avg = sum(len(r.tokens) for r in members) / max(len(members), 1)
        for row in members:
            row.bm25 = to_sparsevec(index.document_vector(row.tokens, avg), DIMENSIONS)
    return index


def attach_embeddings(rows: list[Row]) -> tuple[int, int]:
    """Fills row.embedding from embedding_cache, embedding only text not seen before.

    Each finished batch is committed to the cache immediately, so an interrupted
    run resumes where it stopped.
    """

    model = embedding_model()
    with connect(autocommit=True) as cache:
        cached: dict[str, str] = dict(cache.execute(
            "select content_hash, embedding::text from embedding_cache where model = %s", (model,)
        ).fetchall())

        pending = {row.content_hash: row.text for row in rows if row.dense and row.content_hash not in cached}
        hashes = list(pending)
        batch_count = (len(hashes) + EMBED_BATCH - 1) // EMBED_BATCH
        done = 0

        def save(index: int, vectors: list[list[float]]) -> None:
            nonlocal done
            batch_hashes = hashes[index * EMBED_BATCH:(index + 1) * EMBED_BATCH]
            with cache.cursor().copy("copy embedding_cache (content_hash, model, embedding) from stdin") as copy:
                for content_hash, vector in zip(batch_hashes, vectors):
                    text = to_vector(vector)
                    copy.write_row((content_hash, model, text))
                    cached[content_hash] = text
            done += 1
            if done % 10 == 0 or done == batch_count:
                print(f"  embedded {done}/{batch_count} batches", flush=True)

        if hashes:
            embed([pending[h] for h in hashes], on_batch=save)

    dense_rows = [row for row in rows if row.dense]
    for row in dense_rows:
        row.embedding = cached[row.content_hash]
    return len(dense_rows) - len(pending), len(pending)


def write(conn, rows: list[Row], index: Bm25Index, extras: dict) -> None:
    catalogue_yaml = yaml.safe_load(config.ENTITIES_YAML.read_text())

    with conn.transaction():
        conn.execute(
            "delete from card_evidence; delete from entity_links; delete from entity_aliases; "
            "delete from entities; delete from pattern_cards; delete from incidents; delete from events; "
            "delete from service_requests; delete from changes; delete from doc_chunks; "
            "delete from bm25_vocab; delete from sources;"
        )
        cur = conn.cursor()

        with cur.copy("copy events (id, kind, ticket_count, first_seen, last_seen) from stdin") as copy:
            for e in extras["events"]:
                copy.write_row((e.id, e.kind, e.ticket_count, e.first_seen, e.last_seen))

        with cur.copy("copy changes (number, created_at, closed_at, short_description, change_type, risk, category, programs) from stdin") as copy:
            for c in extras["changes"]:
                copy.write_row((c.number, c.created_at, c.closed_at, c.short_description,
                                c.change_type, c.risk, c.category, list(c.programs)))

        key_column = {"pattern_cards": "id", "incidents": "number", "service_requests": "number", "doc_chunks": "id"}
        for table in SEARCHABLE:
            members = [r for r in rows if r.table == table]
            value_columns = list(members[0].values)
            columns = [key_column[table], *value_columns, "search_text", "embedding", "bm25", "content_hash"]
            with cur.copy(f"copy {table} ({', '.join(columns)}) from stdin") as copy:
                for r in members:
                    copy.write_row((r.id, *[r.values[c] for c in value_columns],
                                    r.text, r.embedding, r.bm25, r.content_hash))

        with cur.copy("copy card_evidence (card_id, item_type, item_id) from stdin") as copy:
            for card in extras["deck"].cards:
                ev = card.evidence
                for kind, ids in (("incident", ev.incidents), ("service_request", ev.service_requests),
                                  ("change", ev.changes), ("problem", ev.problems),
                                  ("doc", [d.id for d in ev.docs])):
                    for item in dict.fromkeys(ids):
                        copy.write_row((card.id, kind, item))

        # Entities: the curated catalogue first (with descriptions), then every ID found in the text.
        described: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        for p in catalogue_yaml.get("programs", []):
            described[("program", p["id"].upper())] = (p.get("what"), p.get("source"))
        for f in catalogue_yaml.get("files", []):
            described[("file", f["id"].upper())] = (f.get("what"), f.get("source"))
        for e in catalogue_yaml.get("error_codes", []):
            described[("error_code", str(e["code"]))] = (e.get("family"), e.get("source"))
        for a in catalogue_yaml.get("abends", []):
            described[("abend", a["code"].upper())] = (a.get("what"), a.get("source"))
        for j in catalogue_yaml.get("jobs", []):
            described[("job", j["id"].upper())] = (", ".join(j.get("steps", [])), j.get("source"))
        for d in catalogue_yaml.get("datasets", []):
            described[("dataset", d["id"].upper())] = (d.get("what"), d.get("source"))

        found: set[tuple[str, str]] = set(described)
        for r in rows:
            for attr, kind in ENTITY_FIELDS.items():
                for value in getattr(r.entities, attr):
                    found.add((kind, value))

        entity_id = {key: number for number, key in enumerate(sorted(found), start=1)}
        with cur.copy("copy entities (id, type, canonical, description, source) from stdin") as copy:
            for (kind, canonical), number in entity_id.items():
                what, source = described.get((kind, canonical), (None, "extracted"))
                copy.write_row((number, kind, canonical, what, source))
        conn.execute("select setval(pg_get_serial_sequence('entities', 'id'), %s)", (len(entity_id),))

        aliases: set[tuple[str, int]] = set()
        for f in catalogue_yaml.get("files", []):
            for alias in f.get("aliases") or []:
                aliases.add((alias.upper() if " " not in alias else alias.lower(), entity_id[("file", f["id"].upper())]))
        for a in catalogue_yaml.get("abends", []):
            for alias in a.get("aliases") or []:
                aliases.add((alias.upper(), entity_id[("abend", a["code"].upper())]))
        for p in catalogue_yaml.get("programs", []):
            if p.get("legacy"):
                aliases.add((p["legacy"].upper(), entity_id[("program", p["id"].upper())]))
        with cur.copy("copy entity_aliases (alias, entity_id) from stdin") as copy:
            for alias, eid in sorted(aliases):
                copy.write_row((alias, eid))

        links: set[tuple[int, str, str]] = set()
        for r in rows:
            for attr, kind in ENTITY_FIELDS.items():
                for value in getattr(r.entities, attr):
                    links.add((entity_id[(kind, value)], LINK_TYPE[r.table], r.id))
        with cur.copy("copy entity_links (entity_id, item_type, item_id) from stdin") as copy:
            for link in sorted(links):
                copy.write_row(link)

        with cur.copy("copy bm25_vocab (token, idx, df) from stdin") as copy:
            for token, idx in index.vocab.items():
                copy.write_row((token, idx, index.df[token]))

        for source_id, kind, path in (
            ("samples", "servicenow_export", config.SAMPLES_XLSX),
            ("cards", "pattern_cards", config.CARDS_YAML),
            ("entities", "entity_catalogue", config.ENTITIES_YAML),
        ):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            conn.execute("insert into sources (id, kind, path, content_hash) values (%s, %s, %s, %s)",
                         (source_id, kind, str(path.relative_to(config.ROOT)), digest))
        doc_digest = hashlib.sha256("".join(sorted(r.content_hash for r in rows if r.table == "doc_chunks")).encode()).hexdigest()
        conn.execute("insert into sources (id, kind, path, content_hash) values ('docs', 'aws_transform_docs', %s, %s)",
                     (str(config.DOCS_ROOT.relative_to(config.ROOT)), doc_digest))


def main() -> int:
    started = time.time()
    rows, extras = build_rows()
    print(f"rows: " + ", ".join(f"{t}={sum(r.table == t for r in rows)}" for t in SEARCHABLE))

    index = index_rows(rows)
    print(f"BM25 vocabulary: {len(index.vocab)} terms over {index.n_docs} rows")

    reused, embedded = attach_embeddings(rows)
    print(f"embeddings: {reused} reused, {embedded} new")

    with connect() as conn:
        write(conn, rows, index, extras)

        counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0]
                  for t in (*SEARCHABLE, "events", "changes", "card_evidence", "entities", "entity_aliases", "entity_links", "bm25_vocab")}
    print("loaded: " + ", ".join(f"{t}={n}" for t, n in counts.items()))
    print(f"done in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
