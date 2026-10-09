"""Evidence rows with a hover card showing where each item comes from (styles from POC v1's theme)."""

from __future__ import annotations

from html import escape

from rca.schema import EvidenceItem, Step


KIND_LABEL = {"incident": "Past incident", "service_request": "Service request", "change": "Change",
              "problem": "Problem record", "doc": "Documentation", "card": "Pattern card",
              "reference": "Error code reference"}

SOURCE_LABEL = {"ticket": "past fix", "doc": "documentation", "card": "known fix", "sme": "known fix",
                "reference": "error code reference"}

INTERNAL = ("PC-", "REF-")          # pattern-card and reference IDs are internal and never shown
DOCS = ("DOC:", "DOC-")             # document chunk IDs are unreadable; show the document title instead


def _label(item: EvidenceItem) -> str:
    return item.title if item.kind in ("doc", "reference") else item.id


def _source_card(item: EvidenceItem) -> str:
    excerpt = f"<div>{escape(item.excerpt)}</div>" if item.excerpt else ""
    return (
        "<div class='source-card'>"
        f"<div class='source-card-title'>{escape(KIND_LABEL.get(item.kind, item.kind))}"
        f"{'' if item.kind in ('doc', 'reference') else ' · ' + escape(item.id)}</div>"
        f"<div><strong>{escape(item.title)}</strong></div>"
        f"{excerpt}"
        "</div>"
    )


def render_evidence_item(item: EvidenceItem) -> str:
    return (
        "<div class='evidence-item'>"
        f"<div class='evidence-text'><b>{escape(_label(item))}</b> — {escape(item.why or item.title)}</div>"
        "<div class='evidence-control'>"
        "<div class='evidence-trigger' tabindex='0'>i</div>"
        f"<div class='evidence-popover'>{_source_card(item)}</div>"
        "</div>"
        "</div>"
    )


def render_step(number: int, step: Step) -> str:
    shown = [e for e in step.evidence_ids if not e.startswith(INTERNAL + DOCS)]
    if step.source != "doc" and any(e.startswith(DOCS) for e in step.evidence_ids):
        shown.append("documentation")
    if step.source != "reference" and any(e.startswith("REF-") for e in step.evidence_ids):
        shown.append("error code reference")
    cites = f" <span style='opacity:.7'>({escape(', '.join(shown))})</span>" if shown else ""
    label = SOURCE_LABEL.get(step.source, step.source)
    return f"**{number}.** {escape(step.text)}  \n<small>source: {label}{cites}</small>"
