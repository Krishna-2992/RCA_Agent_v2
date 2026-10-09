"""Pattern cards: the reviewed description of each recurring failure.

knowledge/pattern_cards.yaml is the source of truth. The schema forbids unknown
keys so a typo in the YAML fails loudly instead of silently dropping a field.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rca.config import CARDS_YAML


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Step(_Strict):
    text: str
    # ticket = a resolved incident/SR/CR did this; doc = AWS Transform documentation;
    # sme = proposed, not documented anywhere we have - needs the support team.
    source: Literal["ticket", "doc", "sme"]
    refs: list[str] = Field(default_factory=list)
    needs_sme: str | None = None

    @model_validator(mode="after")
    def _sourced(self) -> "Step":
        if self.source in ("ticket", "doc") and not self.refs:
            raise ValueError(f"step sourced from {self.source} needs refs: {self.text!r}")
        if self.source == "sme" and not self.needs_sme:
            raise ValueError(f"sme step must say what the SME has to supply: {self.text!r}")
        return self


class Signals(_Strict):
    error_codes: list[str] = Field(default_factory=list)
    abends: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    jobs: list[str] = Field(default_factory=list)
    programs: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    phrases: list[str] = Field(default_factory=list)

    def ids(self) -> set[str]:
        return set(
            self.error_codes + self.abends + self.reasons + self.files
            + self.jobs + self.programs + self.datasets
        )


class DocRef(_Strict):
    id: str
    file: str            # relative to Reman/AWSTransform_Documentation
    section: str


class Evidence(_Strict):
    incidents: list[str] = Field(default_factory=list)
    service_requests: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    docs: list[DocRef] = Field(default_factory=list)

    def record_ids(self) -> set[str]:
        return set(self.incidents + self.service_requests + self.changes + self.problems)

    def doc_ids(self) -> set[str]:
        return {doc.id for doc in self.docs}


class Confusable(_Strict):
    card: str
    how_to_tell: str


class PatternCard(_Strict):
    id: str = Field(pattern=r"^PC-\d{2}$")
    title: str
    status: Literal["draft", "approved"]
    draft_confidence: Literal["high", "medium", "low"]
    system: Literal["online", "batch", "process", "any"]
    applications: list[str]
    symptoms: list[str]
    signals: Signals
    root_cause: str
    steps: list[Step]
    verify: str
    preventive: list[str]
    escalation: Literal["none", "code_change", "not_reproducible", "unknown"]
    escalate_when: list[str] = Field(default_factory=list)
    dont_confuse: list[Confusable] = Field(default_factory=list)
    evidence: Evidence
    open_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refs_resolve(self) -> "PatternCard":
        doc_ids = self.evidence.doc_ids()
        for step in self.steps:
            for ref in step.refs:
                if ref.startswith("DOC-") and ref not in doc_ids:
                    raise ValueError(f"{self.id}: step cites {ref}, which is not in evidence.docs")
        return self

    def search_text(self) -> str:
        """What the card is indexed under for dense and BM25 search."""
        parts = [self.title, *self.symptoms, self.root_cause, " ".join(self.signals.phrases),
                 " ".join(sorted(self.signals.ids()))]
        return "\n".join(part for part in parts if part)


class CardDeck(_Strict):
    version: int
    cards: list[PatternCard]

    @model_validator(mode="after")
    def _unique(self) -> "CardDeck":
        ids = [card.id for card in self.cards]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate card ids: {sorted(duplicates)}")
        known = set(ids)
        for card in self.cards:
            for other in card.dont_confuse:
                if other.card not in known:
                    raise ValueError(f"{card.id}: dont_confuse points at unknown card {other.card}")
        return self

    def by_id(self) -> dict[str, PatternCard]:
        return {card.id: card for card in self.cards}


def load_cards(path: Path = CARDS_YAML) -> CardDeck:
    return CardDeck.model_validate(yaml.safe_load(path.read_text()))
