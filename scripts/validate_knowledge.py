"""Checks the pattern cards, entity file and answer key against the data they come from.

    python -m scripts.validate_knowledge

Errors fail the run (exit code 1); warnings are printed for review.
"""

from __future__ import annotations

import sys

from rca import config
from rca.cards import load_cards
from rca.entities import extract, load_catalogue
from rca.events import event_of, group_events
from rca.harness import held_out_numbers, load_answer_key
from rca.servicenow import load_incidents, load_samples


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    samples = load_samples(config.SAMPLES_XLSX)
    harness = load_incidents(config.HARNESS_XLSX)
    deck = load_cards()
    catalogue = load_catalogue()
    held_out = held_out_numbers(samples.incidents, harness)

    incidents = {i.number: i for i in samples.incidents}
    requests = {s.number: s for s in samples.service_requests}
    changes = {c.number: c for c in samples.changes}
    problems = {p for i in samples.incidents for p in i.problem_refs}
    record_ids = set(incidents) | set(requests) | set(changes) | problems

    events = group_events(samples.incidents)
    owner = event_of(events)

    known_ids = {
        "files": set(catalogue.known_files),
        "error_codes": set(catalogue.known_error_codes),
        "abends": set(catalogue.abend_aliases.values()),
        "jobs": set(catalogue.job_aliases.values()),
    }

    covered_events: dict[str, list[str]] = {}

    for card in deck.cards:
        ev = card.evidence

        # Evidence must exist in the samples and must never be a held-out incident.
        for number in ev.incidents:
            if number in held_out:
                errors.append(f"{card.id}: evidence {number} is a held-out evaluation incident")
            elif number not in incidents:
                errors.append(f"{card.id}: evidence incident {number} is not in the samples")
            else:
                covered_events.setdefault(owner[number], []).append(card.id)
        for number in ev.service_requests:
            if number not in requests:
                errors.append(f"{card.id}: service request {number} is not in the samples")
        for number in ev.changes:
            if number not in changes:
                errors.append(f"{card.id}: change {number} is not in the samples")
        for number in ev.problems:
            if number not in problems:
                errors.append(f"{card.id}: problem {number} is not referenced by any sample incident")
        for doc in ev.docs:
            if not (config.DOCS_ROOT / doc.file).exists():
                errors.append(f"{card.id}: {doc.id} points at a missing file: {doc.file}")

        # Step references resolve.
        for step in card.steps:
            for ref in step.refs:
                if ref.startswith("DOC-"):
                    continue
                if ref in held_out:
                    errors.append(f"{card.id}: step cites held-out incident {ref}")
                elif ref not in record_ids:
                    errors.append(f"{card.id}: step cites unknown record {ref}")

        # Signals use canonical IDs.
        for kind, known in known_ids.items():
            for value in getattr(card.signals, kind):
                if value.upper() not in known:
                    errors.append(f"{card.id}: signal {kind}={value} is not in entities.yaml")

        # Signals should be visible in the evidence (catches a wrong code or file).
        evidence_text = "\n".join(
            [incidents[n].symptom_text + "\n" + incidents[n].resolution_notes for n in ev.incidents if n in incidents]
            + [requests[n].symptom_text + "\n" + requests[n].work_notes for n in ev.service_requests if n in requests]
        )
        seen = extract(evidence_text, catalogue)
        seen_ids = seen.ids()
        for kind in ("error_codes", "abends", "files", "jobs", "datasets"):
            for value in getattr(card.signals, kind):
                if value.upper() not in seen_ids:
                    warnings.append(f"{card.id}: signal {kind}={value} does not appear in its evidence text")

    # Coverage of the knowledge base, by event.
    uncovered = [e for e in events if e.id not in covered_events and not any(m in covered_events for m in e.members)]
    multi = {event: cards for event, cards in covered_events.items() if len(set(cards)) > 1}

    # Error code reference: its ticket citations must be sample incidents, never held-out ones.
    from rca.references import load as load_file_status
    for ref in load_file_status().values():
        for number in ref.seen_in:
            if number in held_out:
                errors.append(f"file status {ref.status}: seen_in {number} is a held-out evaluation incident")
            elif number not in incidents:
                errors.append(f"file status {ref.status}: seen_in {number} is not in the samples")

    # Answer key for the held-out set.
    key = load_answer_key(config.EVAL_LABELS_YAML)
    card_ids = set(deck.by_id())
    missing = sorted(held_out - set(key))
    extra = sorted(set(key) - held_out)
    if missing:
        errors.append(f"answer key is missing {len(missing)} held-out incidents: {missing}")
    if extra:
        errors.append(f"answer key labels incidents that are not held out: {extra}")
    for number, label in key.items():
        for card_id in label.expected + label.acceptable:
            if card_id not in card_ids:
                errors.append(f"answer key {number}: unknown card {card_id}")

    # Report
    print(f"Cards: {len(deck.cards)}  |  sample incidents: {len(incidents)} in {len(events)} events"
          f"  |  held-out incidents: {len(held_out)}")
    print(f"Events covered by a card: {len(events) - len(uncovered)} of {len(events)}")
    for event in uncovered:
        first = incidents[event.members[0]]
        print(f"  not covered: {event.id} ({event.ticket_count} ticket(s)) - {first.short_description[:90]}")
    for event, cards in sorted(multi.items()):
        warnings.append(f"event {event} is evidence for several cards: {sorted(set(cards))}")

    for warning in warnings:
        print(f"WARN  {warning}")
    for error in errors:
        print(f"ERROR {error}")
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
