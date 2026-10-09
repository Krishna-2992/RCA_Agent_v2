"""Writes the regression and evaluation sets to build/eval/*.jsonl.

    python -m scripts.build_eval_sets
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from rca import config
from rca.cards import load_cards
from rca.events import group_events
from rca.harness import build_cases, load_answer_key
from rca.servicenow import load_incidents, load_samples


def main() -> int:
    samples = load_samples(config.SAMPLES_XLSX)
    harness = load_incidents(config.HARNESS_XLSX)
    deck = load_cards()
    events = group_events(samples.incidents)
    key = load_answer_key(config.EVAL_LABELS_YAML)

    cases = build_cases(samples.incidents, harness, deck, events, key)

    out_dir = config.BUILD_DIR / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)

    for split in ("regression", "evaluation"):
        rows = [c for c in cases if c.split == split]
        path = out_dir / f"{split}.jsonl"
        with path.open("w") as handle:
            for case in rows:
                handle.write(json.dumps(case.to_json(), ensure_ascii=False) + "\n")

        coverage = Counter(c.coverage for c in rows)
        by_card = Counter(card for c in rows for card in (c.expected_cards or ["(no match)"]))
        print(f"{split}: {len(rows)} cases -> {path.relative_to(config.ROOT)}")
        print(f"  coverage: {dict(coverage)}")
        print(f"  expected cards: {dict(sorted(by_card.items()))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
