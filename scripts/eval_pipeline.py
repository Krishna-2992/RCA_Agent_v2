"""Runs the full v2 pipeline on an eval set and reports how it did.

    python -m scripts.eval_pipeline                      # regression set (69)
    python -m scripts.eval_pipeline --limit 10           # quick check
    python -m scripts.eval_pipeline --rewrite            # with the LLM rewrite step

The evaluation set (45 held-out incidents) is run only at milestones:
    python -m scripts.eval_pipeline --split evaluation

For regression cases the incident's own event is hidden from search, so it
can't simply find itself. Results are saved to build/eval/results_<split>_<time>.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import local

from rca import config
from rca import tracing
from rca.pipeline import Pipeline


# Held-out incidents already looked at while building v2 (2026-10-09 session), so the score with them
# removed is the clean one. POC v1's playbooks were built from INC9581851, INC9435163, INC9791495.
SEEN_IN_TUNING = {"INC9439635", "INC9435163", "INC9730755", "INC9791495", "INC9581851"}
_local = local()


FULL_CARDS = 1


def _pipeline(use_rewrite: bool) -> Pipeline:
    if not hasattr(_local, "pipeline"):
        _local.pipeline = Pipeline(use_rewrite=use_rewrite, unclear_full_cards=FULL_CARDS)
    return _local.pipeline


def unclear_cases(cases: list[dict], split: str) -> list[dict]:
    """Cases whose search match is unclear - the only ones the unclear-match settings change."""
    from rca.scoring import gate
    from rca.understand import understand
    pipeline = _pipeline(False)
    keep = []
    for case in cases:
        exclude = frozenset({pipeline.searcher.event_of.get(case["id"], case["id"])}) if split == "regression" else frozenset()
        _, entities = understand(case["query"])
        result = pipeline.searcher.search(case["query"], exclude_events=exclude, entities=entities)
        decision = gate(result, pipeline.store.cards([c.id for c in result.cards[:3]]), pipeline.searcher.event_of)
        if not decision.strong:
            keep.append(case)
    return keep


def run_case(case: dict, split: str, use_rewrite: bool) -> dict:
    pipeline = _pipeline(use_rewrite)
    exclude = frozenset()
    if split == "regression":
        exclude = frozenset({pipeline.searcher.event_of.get(case["id"], case["id"])})

    started = time.monotonic()
    try:
        response = pipeline.run(case["query"], exclude_events=exclude, langsmith_extra={
            "project_name": tracing.eval_project(), "tags": ["eval", split],
            "metadata": {"case": case["id"], "expected": case["expected_cards"], "split": split}})
    except Exception as error:                      # one bad case must not stop the run
        return {"id": case["id"], "error": f"{type(error).__name__}: {error}"}
    elapsed = time.monotonic() - started

    matched = response.pattern.id if response.pattern else None
    expected = set(case["expected_cards"])
    acceptable = expected | set(case["acceptable_cards"])
    correct = (matched in acceptable) if expected else (matched is None or matched in acceptable)

    steps = response.resolution_steps
    llm_nodes = [n for n in response.trace if n.model]
    return {
        "id": case["id"],
        "expected": sorted(expected),
        "matched": matched,
        "correct": correct,
        "status": response.status,
        "confidence": response.confidence.score,
        "level": response.confidence.level,
        "steps": len(steps),
        "sourced_steps": sum(1 for s in steps if s.source != "sme" and s.evidence_ids),
        "sme_steps": sum(1 for s in steps if s.source == "sme"),
        "check_errors": [e for n in response.trace for e in n.detail.get("check_errors", [])],
        "seconds": round(elapsed, 1),
        "llm_calls": len(llm_nodes),
        "models": [n.model for n in llm_nodes],
        "gate_strong": next((n.detail.get("strong") for n in response.trace if n.name == "gate"), None),
        "investigated": any(n.name == "investigate" for n in response.trace),
        "tokens": sum(n.tokens or 0 for n in llm_nodes),
        "escalation": response.escalation,
        "coverage": case.get("coverage"),
        "steps_text": [s.text for s in steps],
        "root_cause": response.root_cause,
        "reference_root_cause": case.get("reference_root_cause"),
        "reference_action": case.get("reference_action"),
    }


def summarise(rows: list[dict]) -> None:
    ok = [r for r in rows if "error" not in r]
    errors = [r for r in rows if "error" in r]
    n = len(ok)
    if not n:
        print("no successful cases")
        return

    accuracy = sum(r["correct"] for r in ok) / n
    print(f"\ncases: {n} ok, {len(errors)} failed")
    print(f"pattern accuracy: {accuracy:.0%} ({sum(r['correct'] for r in ok)}/{n})")
    clean = [r for r in ok if r["id"] not in SEEN_IN_TUNING]
    if len(clean) < n:
        print(f"  without the {n - len(clean)} incidents seen while tuning: "
              f"{sum(r['correct'] for r in clean) / len(clean):.0%} ({sum(r['correct'] for r in clean)}/{len(clean)})")
    for coverage in ("full", "partial", "none"):
        bucket = [r for r in ok if r.get("coverage") == coverage]
        if bucket:
            print(f"  coverage {coverage:7} {sum(r['correct'] for r in bucket)}/{len(bucket)}")
    print(f"status: {dict(Counter(r['status'] for r in ok))}; investigated: {sum(r['investigated'] for r in ok)}/{n}; "
          f"tokens per query: {sum(r['tokens'] for r in ok) // n}")

    print("calibration (correct / cases by confidence level):")
    for level in ("high", "medium", "low"):
        bucket = [r for r in ok if r["level"] == level]
        if bucket:
            print(f"  {level:6} {sum(r['correct'] for r in bucket)}/{len(bucket)}")

    steps = sum(r["steps"] for r in ok)
    print(f"steps with a source: {sum(r['sourced_steps'] for r in ok)}/{steps}; marked SME: {sum(r['sme_steps'] for r in ok)}")
    print(f"answers that failed citation checks after retry: {sum(1 for r in ok if r['check_errors'])}")
    seconds = sorted(r["seconds"] for r in ok)
    print(f"latency: median {seconds[n // 2]}s, p95 {seconds[min(n - 1, int(n * 0.95))]}s")
    print(f"LLM calls per query: {sum(r['llm_calls'] for r in ok) / n:.1f}; "
          f"models: {dict(Counter(m for r in ok for m in r['models']))}")
    print(f"gate strong: {sum(1 for r in ok if r['gate_strong'])}/{n}")

    wrong = [r for r in ok if not r["correct"]]
    if wrong:
        print("\nwrong pattern:")
        for r in wrong:
            print(f"  {r['id']}: expected {r['expected']} got {r['matched']} ({r['level']} {r['confidence']})")
    for r in errors:
        print(f"  FAILED {r['id']}: {r['error'][:200]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="regression", choices=("regression", "evaluation"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--rewrite", action="store_true")
    parser.add_argument("--full-cards", type=int, default=1,
                        help="known issues shown in full when the match is unclear (3 was tried; no gain)")
    parser.add_argument("--only-unclear", action="store_true", help="run only cases with an unclear match")
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel cases; the chat deployment's 10k tokens/min makes more than 1 queue up")
    args = parser.parse_args()

    path = config.BUILD_DIR / "eval" / f"{args.split}.jsonl"
    cases = [json.loads(line) for line in path.open()]
    if args.limit:
        cases = cases[:args.limit]
    global FULL_CARDS
    FULL_CARDS = args.full_cards
    if args.only_unclear:
        cases = unclear_cases(cases, args.split)

    print(f"running {len(cases)} {args.split} cases (rewrite {'on' if args.rewrite else 'off'}, "
          f"{FULL_CARDS} known issues in full when unclear)", flush=True)
    def run_and_report(case: dict) -> dict:
        row = run_case(case, args.split, args.rewrite)
        mark = "ERROR" if "error" in row else ("ok   " if row["correct"] else "WRONG")
        print(f"{mark} {row['id']} {row.get('matched')} {row.get('level', '')} {row.get('seconds', '')}s", flush=True)
        return row

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(run_and_report, cases))

    out = config.BUILD_DIR / "eval" / f"results_{args.split}_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    with out.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"saved {out.relative_to(config.ROOT)}")

    summarise(rows)
    tracing.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
