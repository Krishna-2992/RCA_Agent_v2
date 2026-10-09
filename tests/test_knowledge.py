"""Checks that need the client workbooks; skipped on machines without them."""

import pytest

from rca import config
from rca.cards import load_cards
from rca.events import group_events
from rca.harness import build_cases, held_out_numbers, load_answer_key
from rca.servicenow import load_incidents, load_samples


needs_data = pytest.mark.skipif(
    not (config.SAMPLES_XLSX.exists() and config.HARNESS_XLSX.exists()),
    reason="client workbooks not present",
)


def test_cards_load_and_are_drafts():
    deck = load_cards()
    assert [c.id for c in deck.cards] == [f"PC-{n:02d}" for n in range(1, 16)]
    assert all(card.status == "draft" for card in deck.cards)


@needs_data
def test_samples_load():
    samples = load_samples(config.SAMPLES_XLSX)
    assert (len(samples.incidents), len(samples.service_requests), len(samples.changes)) == (69, 24, 3)


@needs_data
def test_child_incidents_collapse_into_parent_event():
    samples = load_samples(config.SAMPLES_XLSX)
    events = {e.id: e for e in group_events(samples.incidents)}
    assert events["INC10139580"].ticket_count == 11


@needs_data
def test_harness_split_and_no_leak():
    samples = load_samples(config.SAMPLES_XLSX)
    harness = load_incidents(config.HARNESS_XLSX)
    held_out = held_out_numbers(samples.incidents, harness)
    assert len(harness) == 114 and len(held_out) == 45

    deck = load_cards()
    cited = {n for card in deck.cards for n in card.evidence.incidents}
    assert not cited & held_out, "a card cites a held-out evaluation incident"


@needs_data
def test_eval_cases_hide_resolution_from_query():
    samples = load_samples(config.SAMPLES_XLSX)
    harness = load_incidents(config.HARNESS_XLSX)
    cases = build_cases(samples.incidents, harness, load_cards(), group_events(samples.incidents),
                        load_answer_key(config.EVAL_LABELS_YAML))
    assert sum(c.split == "regression" for c in cases) == 69
    assert sum(c.split == "evaluation" for c in cases) == 45
    for case in cases:
        if case.reference_action and len(case.reference_action) > 30:
            assert case.reference_action not in case.query
