"""Builds the evidence catalogue: everything the answer step may see and cite.

Every item has an ID the LLM must cite exactly (PC-01, INC..., SCTASK..., CHG...,
PRB..., DOC:<n> for documentation sections). The check step rejects any citation outside this set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from rca.cards import PatternCard, load_cards
from rca.references import FileStatus
from rca.search import SearchResult
from rca.store import Store


CARDS_SHOWN = 3
INCIDENTS_SHOWN = 5
REQUESTS_SHOWN = 3
DOCS_SHOWN = 4
DOC_CHARS = 1500
# After an investigation the targeted findings come first, and the rest is trimmed to keep the prompt
# inside the chat deployment's 10k tokens/minute.
INVESTIGATED_DOCS = 6
INVESTIGATED_DOC_CHARS = 1200
INVESTIGATED_INCIDENTS = 3
INVESTIGATED_REQUESTS = 2
# Business-rule extracts and the AI-generated catalogue are generic; technical docs explain behaviour.
LOW_VALUE_DOCS = ("business_rules", "catalogue")

DOC_LABEL = {"technical_xml": "technical documentation", "program_summary": "program summary",
             "overview": "program overview", "business_rules": "business rule",
             "catalogue": "program catalogue (AI-generated, low trust)", "general": "overview document"}


@dataclass(frozen=True)
class Item:
    id: str
    kind: str              # card | incident | service_request | change | problem | doc
    title: str
    excerpt: str           # for the UI
    prompt_text: str       # for the LLM


@dataclass
class Catalogue:
    items: dict[str, Item] = field(default_factory=dict)
    cards: dict[str, PatternCard] = field(default_factory=dict)     # shown cards, best first
    sources: dict[str, str] = field(default_factory=dict)          # short doc citation -> chunk id
    extra_ids: set[str] = field(default_factory=set)               # citable records behind the top card

    @property
    def top_card(self) -> PatternCard | None:
        return next(iter(self.cards.values()), None)

    def allowed(self) -> set[str]:
        return set(self.items) | self.extra_ids

    def add(self, item: Item) -> None:
        self.items.setdefault(item.id, item)

    def prompt(self) -> str:
        return "\n\n".join(item.prompt_text for item in self.items.values() if item.prompt_text)


def _date(value) -> str:
    return value.date().isoformat() if value else "unknown date"


# The cards are also the SME review copy, so they carry review notes ("needs SME input", draft
# status, open questions). Those stay out of the prompt: the answer reads as the team's runbook.
_SME_NOTE = re.compile(r"\s*\(needs SME input\)|;\s*needs SME input(?=\))", re.I)
_CARD_REF = re.compile(r"\bPC-\d{2}\b")


@lru_cache(maxsize=1)
def _card_names() -> dict[str, str]:
    return {card_id: card.title.split(":")[0] for card_id, card in load_cards().by_id().items()}


def _clean(text: str) -> str:
    """Drops review notes and turns card cross-references into the issue's name."""
    names = _card_names()
    text = _SME_NOTE.sub("", text)
    return _CARD_REF.sub(lambda m: f'"{names.get(m.group(), "the related issue")}"', text)


def _card_text(card: PatternCard, full: bool) -> str:
    signals = card.signals
    ids = ", ".join(sorted(signals.ids())) or "none"
    lines = [f"[{card.id}] Pattern card: {card.title}",
             f"System: {card.system}. Signals: {ids}. Typical words: {', '.join(signals.phrases[:10])}.",
             f"Root cause: {_clean(card.root_cause)}"]
    if full:
        lines.append("Steps:")
        for n, step in enumerate(card.steps, 1):
            refs = f" (refs {', '.join(step.refs)})" if step.refs else ""
            lines.append(f"  {n}. {_clean(step.text)}{refs}")
        lines.append(f"Verify: {_clean(card.verify)}")
        if card.preventive:
            lines.append("Preventive: " + " | ".join(_clean(p) for p in card.preventive))
        lines.append(f"Escalation: {card.escalation}." + (
            " Escalate when: " + " | ".join(_clean(e) for e in card.escalate_when) if card.escalate_when else ""))
        for doc in card.evidence.docs:
            lines.append(f"Card document {doc.id}: {doc.file} - {doc.section}")
    if card.dont_confuse:
        lines.append("How to tell apart: " + " | ".join(f"{c.card}: {c.how_to_tell}" for c in card.dont_confuse))
    return "\n".join(lines)


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{2,}")


def focus(text: str, terms: set[str], size: int) -> str:
    """The `size`-character window of `text` that mentions the most distinct `terms`, so a long
    chunk contributes the passage that matters rather than its first paragraphs."""
    if len(text) <= size or not terms:
        return text[:size]
    hits = [(m.start(), m.group().lower()) for m in _WORD.finditer(text) if m.group().lower() in terms]
    if not hits:
        return text[:size]
    best_start, best = 0, -1
    for i, (start, _) in enumerate(hits):
        distinct = {term for position, term in hits[i:] if position < start + size}
        if len(distinct) > best:
            best_start, best = start, len(distinct)
    start = max(0, best_start - size // 5)
    boundary = text.rfind(". ", 0, start)
    if boundary != -1 and start - boundary < 200:
        start = boundary + 2
    end = min(len(text), start + size)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")


def build(result: SearchResult, store: Store, card_order: list[str] | None = None, *,
          references: list[tuple[FileStatus, list[str]]] = (), investigation=None,
          focus_terms: set[str] = frozenset(), full_cards: int = 1) -> Catalogue:
    """`full_cards`: how many of the shown cards carry their steps and notes in full (the pipeline
    passes Pipeline.unclear_full_cards when the match is unclear; see there for the measured result)."""
    catalogue = Catalogue()
    investigated = investigation is not None
    terms = set(focus_terms) | (investigation.focus_terms if investigated else set())

    # Cards: the ranked list from search (or a caller-supplied order).
    order = card_order or [c.id for c in result.cards]
    shown = order[:CARDS_SHOWN]
    fetched = store.cards(shown)
    for index, card_id in enumerate(shown):
        card = fetched.get(card_id)
        if not card:
            continue
        catalogue.cards[card_id] = card
        catalogue.add(Item(card_id, "card", card.title, card.root_cause, _card_text(card, full=index < full_cards)))

    top = catalogue.top_card
    if top:
        catalogue.extra_ids |= top.evidence.record_ids()
        # The card's own document references (DOC-RMNIDX-RECOVERY, ...) are listed in its
        # text, so they are citable; prompt_text is empty because the card already shows them.
        for doc in top.evidence.docs:
            catalogue.add(Item(doc.id, "doc", f"{doc.file.rsplit('/', 1)[-1]} - {doc.section}", doc.section, ""))

    # Error code reference: what the reported status means, for errors no ticket covers.
    for ref, codes in references:
        catalogue.add(Item(ref.ref_id, "reference", f"File status {ref.status}: {ref.name}", ref.meaning,
                           ref.prompt_text(codes)))

    # Incidents: best hit per event, as search already collapsed them, then the investigation's finds.
    incident_ids = [h.id for h in result.incidents[:INVESTIGATED_INCIDENTS if investigated else INCIDENTS_SHOWN]]
    if investigated:
        incident_ids += [i for i in investigation.incident_ids if i not in incident_ids]
    records = store.incidents(incident_ids)
    for number in incident_ids:
        r = records.get(number)
        if not r:
            continue
        reported = r.short_description if not r.description else f"{r.short_description} / {r.description}"
        prompt = "\n".join(line for line in (
            f"[{r.number}] Incident opened {_date(r.opened_at)}, service {r.service}"
            + (f", resolved by {r.resolved_by}" if r.resolved_by else ""),
            f"Reported: {reported[:600]}",
            f"Root cause: {r.root_cause}" if r.root_cause else "",
            f"Action taken: {r.action_taken[:600]}" if r.action_taken else "",
        ) if line)
        catalogue.add(Item(r.number, "incident", r.short_description[:120],
                           (r.root_cause or r.action_taken or "")[:300], prompt))

    request_ids = [h.id for h in result.service_requests[:INVESTIGATED_REQUESTS if investigated else REQUESTS_SHOWN]]
    for number, r in store.requests(request_ids).items():
        prompt = f"[{r.number}] Service request opened {_date(r.opened_at)}\nReported: {(r.description or r.short_description)[:500]}"
        if r.work_notes:
            prompt += f"\nWork notes: {r.work_notes[:400]}"
        catalogue.add(Item(r.number, "service_request", r.short_description[:120], r.work_notes[:300], prompt))

    # Docs: the investigation's targeted finds first, then search's: procedures before mechanisms,
    # generic extracts last. Each shows the excerpt centred on the report's terms.
    doc_hits = [h.id for h in result.docs]
    targeted = list(investigation.doc_ids) if investigated else []
    docs = store.docs(targeted + doc_hits)
    ordered = [docs[d] for d in targeted if d in docs] + sorted(
        (docs[d] for d in doc_hits if d in docs and d not in targeted),
        key=lambda d: (d.doc_type in LOW_VALUE_DOCS, d.role != "procedure", doc_hits.index(d.id)))
    chars = INVESTIGATED_DOC_CHARS if investigated else DOC_CHARS
    # Short citation IDs: models mistype long chunk IDs, which fails the citation check.
    for number, d in enumerate(ordered[:INVESTIGATED_DOCS if investigated else DOCS_SHOWN], 1):
        label = DOC_LABEL.get(d.doc_type, d.doc_type)
        title = f"{d.program or 'REMAN'} {label} - {d.section_path}"
        excerpt = focus(d.text, terms, chars)
        cite = f"DOC:{number}"
        catalogue.sources[cite] = d.id
        prompt = f"[{cite}] Documentation, {d.role} ({title})\n{excerpt}"
        catalogue.add(Item(cite, "doc", title, excerpt[:300], prompt))

    # Changes touching the programs in play.
    programs = sorted(set(result.entities.programs) | (set(top.signals.programs) if top else set()))
    if programs:
        for change in store.changes_for(programs):
            catalogue.add(Item(change.number, "change", change.short_description,
                               f"closed {_date(change.closed_at)}",
                               f"[{change.number}] Change closed {_date(change.closed_at)}: {change.short_description}"))

    if top:
        for problem in top.evidence.problems:
            catalogue.add(Item(problem, "problem", f"Problem record {problem}", "linked from the pattern card",
                               f"[{problem}] Problem record linked to {top.id}"))

    return catalogue
