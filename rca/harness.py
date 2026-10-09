"""Builds the regression and evaluation sets from the harness workbook.

The harness holds 114 incidents: the 69 that are also the knowledge base, and
45 held out. The 69 are the regression suite; the 45 are scored at milestones
and never used to tune anything.

A case's query is only what a support engineer would see (short description and
description). The resolution notes travel with the case as the reference answer
for the judge and must never reach the pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from rca.cards import CardDeck
from rca.events import Event, event_of
from rca.servicenow import Incident


Split = Literal["regression", "evaluation"]


@dataclass(frozen=True)
class EvalCase:
    id: str
    split: Split
    query: str
    service: str
    channel: str
    opened_at: str | None
    expected_cards: list[str]          # empty = no card should match ("no close match")
    acceptable_cards: list[str]        # also counted as correct
    coverage: str                      # full | partial | none
    label_source: str                  # card_evidence | answer_key
    reference_root_cause: str | None
    reference_action: str | None
    reference_notes: str
    note: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def held_out_numbers(samples: list[Incident], harness: list[Incident]) -> set[str]:
    """Incidents that appear only in the harness - the 45 evaluation cases."""
    return {i.number for i in harness} - {i.number for i in samples}


def expected_cards_from_evidence(deck: CardDeck, events: list[Event]) -> dict[str, list[str]]:
    """Regression labels: an incident maps to every card whose evidence cites its event."""

    owner = event_of(events)
    members_of = {event.id: event.members for event in events}
    labels: dict[str, list[str]] = {}

    for card in deck.cards:
        for number in card.evidence.incidents:
            for member in members_of.get(owner.get(number, number), (number,)):
                labels.setdefault(member, [])
                if card.id not in labels[member]:
                    labels[member].append(card.id)
    return labels


@dataclass(frozen=True)
class Label:
    expected: list[str] = field(default_factory=list)
    acceptable: list[str] = field(default_factory=list)
    coverage: str = "none"
    note: str = ""


def load_answer_key(path: Path) -> dict[str, Label]:
    data = yaml.safe_load(path.read_text()) or {}
    key = {}
    for number, entry in (data.get("labels") or {}).items():
        key[number] = Label(
            expected=list(entry.get("expected") or []),
            acceptable=list(entry.get("acceptable") or []),
            coverage=entry.get("coverage", "none"),
            note=entry.get("note", ""),
        )
    return key


def build_cases(
    samples: list[Incident],
    harness: list[Incident],
    deck: CardDeck,
    events: list[Event],
    answer_key: dict[str, Label],
) -> list[EvalCase]:
    sample_numbers = {i.number for i in samples}
    regression_labels = expected_cards_from_evidence(deck, events)

    cases = []
    for incident in harness:
        split: Split = "regression" if incident.number in sample_numbers else "evaluation"

        if split == "regression":
            expected = regression_labels.get(incident.number, [])
            label = Label(expected=expected, coverage="full" if expected else "none")
            source = "card_evidence"
        else:
            label = answer_key.get(incident.number, Label(note="not labelled yet"))
            source = "answer_key"

        cases.append(EvalCase(
            id=incident.number,
            split=split,
            query=incident.symptom_text,
            service=incident.service,
            channel=incident.channel,
            opened_at=incident.opened_at.isoformat() if incident.opened_at else None,
            expected_cards=label.expected,
            acceptable_cards=label.acceptable,
            coverage=label.coverage,
            label_source=source,
            reference_root_cause=incident.root_cause,
            reference_action=incident.action_taken,
            reference_notes=incident.resolution_notes,
            note=label.note,
        ))

    return cases
