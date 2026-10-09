"""Measures how often search ranks the right pattern card first / in the top 3.

    python -m scripts.eval_retrieval              # regression set (69)
    python -m scripts.eval_retrieval --ablation   # also each channel on its own

Runs on the regression set only. Each case's own event is hidden from the
incident results, so an incident can't simply find itself; cards were written
from these incidents, so the numbers are still optimistic - the 45 held-out
incidents give the honest score later.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from rca import config
from rca.search import CHANNELS, Searcher


def load_cases(split: str) -> list[dict]:
    path = config.BUILD_DIR / "eval" / f"{split}.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} not found - run: python -m scripts.build_eval_sets")
    return [json.loads(line) for line in path.open()]


def evaluate(searcher: Searcher, cases: list[dict], channels: tuple[str, ...]) -> dict:
    hits1 = hits3 = 0
    reciprocal = 0.0
    misses = []
    per_card = defaultdict(lambda: [0, 0])          # card -> [hit@3, total]

    for case in cases:
        expected = set(case["expected_cards"]) | set(case["acceptable_cards"])
        event = searcher.event_of.get(case["id"], case["id"])
        result = searcher.search(case["query"], channels=channels, exclude_events=frozenset({event}))
        ranking = [card.id for card in result.cards]

        rank = next((i + 1 for i, card in enumerate(ranking) if card in expected), None)
        hits1 += rank == 1
        hits3 += rank is not None and rank <= 3
        reciprocal += 1 / rank if rank else 0
        for card in case["expected_cards"]:
            per_card[card][1] += 1
            per_card[card][0] += rank is not None and rank <= 3
        if rank != 1:
            misses.append((case["id"], sorted(expected), ranking[:3], rank, case["query"][:80].replace("\n", " ")))

    n = len(cases)
    return {"n": n, "hit@1": hits1 / n, "hit@3": hits3 / n, "mrr": reciprocal / n,
            "misses": misses, "per_card": dict(per_card)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ablation", action="store_true")
    args = parser.parse_args()

    cases = [c for c in load_cases("regression") if c["expected_cards"]]
    searcher = Searcher()
    try:
        fused = evaluate(searcher, cases, CHANNELS)
        reports = [(CHANNELS, fused)]
        if args.ablation:
            reports += [((channel,), evaluate(searcher, cases, (channel,))) for channel in CHANNELS]
    finally:
        searcher.close()

    for channels, report in reports:
        print(f"{'+'.join(channels):18} n={report['n']}  hit@1={report['hit@1']:.0%}  "
              f"hit@3={report['hit@3']:.0%}  MRR={report['mrr']:.2f}")

    print("\nPer card, fused (hit@3 / cases):")
    for card, (hit, total) in sorted(fused["per_card"].items()):
        print(f"  {card}: {hit}/{total}")

    print("\nNot ranked first, fused:")
    for case_id, expected, top3, rank, query in fused["misses"]:
        print(f"  {case_id} expected {expected} got {top3} (rank {rank}) | {query}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
