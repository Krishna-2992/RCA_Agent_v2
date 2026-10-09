"""Step 4 (gate) and step 8 (confidence), both computed from evidence in code.

The LLM's own judgement is one input to confidence, never the whole score.
Thresholds here are starting values, to be tuned on the 69 regression incidents.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rca.cards import PatternCard
from rca.entities import Entities
from rca.schema import Confidence, Level
from rca.search import SearchResult


STRONG_MARGIN = 0.25          # top card leads the runner-up by at least 25% of its score
HIGH, MEDIUM = 0.75, 0.45
MAX_SCORE = 0.95              # an RCA from past evidence is never certain
DIAGNOSIS_MAX = 0.65          # worked out from the error meaning and docs, no past incident: medium at most

# IDs that pin down a failure. Programs and jobs alone are context, not a symptom.
DECISIVE = ("files", "error_codes", "abends", "reasons", "datasets")
# A known issue that doesn't produce the report's error number or abend can't be a strong match.
MUST_EXPLAIN = ("error_codes", "abends")


@dataclass
class GateDecision:
    strong: bool
    top: str | None
    margin: float = 0.0
    id_agreement: float = 0.5               # 1 = query IDs match the card, 0.5 = query has none, 0 = they conflict
    supporting_events: int = 0
    matched_ids: list[str] = field(default_factory=list)
    unexplained: list[str] = field(default_factory=list)   # error codes / abends the card doesn't produce
    reasons: list[str] = field(default_factory=list)


def _decisive_ids(entities: Entities) -> set[str]:
    return {value for name in DECISIVE for value in getattr(entities, name)}


def gate(result: SearchResult, cards: dict[str, PatternCard], event_of: dict[str, str]) -> GateDecision:
    if not result.cards:
        return GateDecision(strong=False, top=None, id_agreement=0.0, reasons=["No known issue matched."])

    top = result.cards[0]
    second = result.cards[1].score if len(result.cards) > 1 else 0.0
    margin = (top.score - second) / top.score if top.score > 0 else 0.0

    # Same identifier agreement that search used to rank the cards (rca/search.py), from [-1, 1] to [0, 1].
    query_ids = _decisive_ids(result.entities)
    matched = top.matched_ids
    agreement = 0.5 + 0.5 * top.agreement

    card = cards.get(top.id)
    produced = set(card.signals.error_codes) | set(card.signals.abends) if card else set()
    unexplained = sorted(v for name in MUST_EXPLAIN for v in getattr(result.entities, name) if v not in produced)

    events = {event_of.get(item, item) for item in top.support if item.startswith("INC")}

    strong = (margin >= STRONG_MARGIN and not unexplained
              and (agreement >= 0.75 or (agreement >= 0.5 and len(events) >= 1)))

    # Reasons are shown to support staff, so they talk about known issues, never card IDs.
    reasons = [f"The best match is {margin:.0%} ahead of the next possible cause."]
    if matched:
        reasons.append(f"The report's identifiers match this known issue: {', '.join(matched)}.")
    if unexplained:
        reasons.append(f"This known issue does not produce {', '.join(unexplained)} from the report.")
    if top.conflicting_ids:
        reasons.append(f"Some identifiers in the report differ from this known issue: {', '.join(top.conflicting_ids)}.")
    elif query_ids and not matched:
        reasons.append(f"The report's identifiers ({', '.join(sorted(query_ids))}) have not been seen with this issue before.")
    if events:
        reasons.append(f"{len(events)} similar past incident(s) had the same cause.")

    return GateDecision(strong=strong, top=top.id, margin=margin, id_agreement=agreement,
                        supporting_events=len(events), matched_ids=matched, unexplained=unexplained,
                        reasons=reasons)


JUDGEMENT = {"high": 1.0, "medium": 0.6, "low": 0.2}


def confidence(decision: GateDecision, judgement: Level, matched: bool, checks_failed: bool, *,
               reference_cited: bool = False, docs_cited: int = 0, has_steps: bool = False) -> Confidence:
    if not matched and has_steps and (reference_cited or docs_cited):
        return _diagnosis_confidence(judgement, checks_failed, reference_cited, docs_cited)

    margin = min(1.0, decision.margin / 0.5)
    support = min(decision.supporting_events, 3) / 3
    score = 0.30 * margin + 0.25 * decision.id_agreement + 0.20 * support + 0.25 * JUDGEMENT[judgement]

    reasons = list(decision.reasons)
    reasons.append(f"The analysis rates the fit as {judgement}.")
    if not matched:
        score = min(score, 0.35)
        reasons.append("No known issue explains this report, so the cause is not established.")
    if checks_failed:
        score = min(score, 0.40)
        reasons.append("Part of the answer could not be traced to the evidence, so treat it as a lead.")

    score = min(score, MAX_SCORE)
    level: Level = "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"
    return Confidence(score=round(score, 2), level=level, reasons=reasons)


def _diagnosis_confidence(judgement: Level, checks_failed: bool, reference_cited: bool, docs_cited: int) -> Confidence:
    """No past incident matches, but the error meaning and/or the documentation explain the failure."""
    # Docs alone reach medium only with a high judgement; an explained error number adds the most.
    score = 0.25 + 0.15 * reference_cited + 0.10 * min(docs_cited, 2) / 2 + 0.10 * JUDGEMENT[judgement]
    reasons = ["No past incident matches this report; the analysis is worked out from the evidence below."]
    if reference_cited:
        reasons.append("The error number is explained by the COBOL file status reference.")
    if docs_cited:
        reasons.append(f"{docs_cited} documentation section(s) describe the program behaviour involved.")
    reasons.append(f"The analysis rates the fit as {judgement}.")
    if checks_failed:
        score = min(score, 0.40)
        reasons.append("Part of the answer could not be traced to the evidence, so treat it as a lead.")
    score = min(score, DIAGNOSIS_MAX)
    level: Level = "medium" if score >= MEDIUM else "low"
    return Confidence(score=round(score, 2), level=level, reasons=reasons)
