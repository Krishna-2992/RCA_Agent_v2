"""Steps 6-7: the LLM writes the RCA from the catalogue; code checks every citation."""

from __future__ import annotations

import time

from rca.catalogue import Catalogue
from rca.llm import chat_client, chat_model, with_retry
from rca.schema import LlmAnswer, TraceNode, Understood
from rca.scoring import GateDecision
from rca.tracing import trace


SYSTEM_PROMPT = """You are a senior support engineer for the REMAN COBOL applications (LMS, Inventory,
Reman Index, BOM, and mainframe batch jobs run by AJS/Tidal). Write a root cause analysis for the
reported issue using ONLY the evidence catalogue: pattern cards (known issues), past tickets,
documentation, and the error code reference (what a COBOL file status such as 4102 = status 41 means).

Rules:
1. Decide whether the top pattern card explains the report. Compare the reported symptom and any
   file names, error numbers, abend codes or jobs with the card's signals, and use the card's
   "How to tell apart" notes. When the report itself gives an error number or abend code, a card
   whose signals don't include it does not fit; otherwise judge by the symptom and the files named.
   If no card fits, set matched_pattern to null and write a diagnosis (rule 12).
2. Past tickets show what happened and what fixed it. Documentation shows how the software works
   or how a procedure is done. Neither proves this incident's cause on its own.
3. root_cause states the cause directly in 1-2 sentences, the way the evidence shows it (for example
   "IN0018.MOV is corrupted, so ..."). Uncertainty belongs in judgement and missing_information,
   not in root_cause.
4. Resolution steps come only from: the matched card's steps (source "card"), actions in past
   tickets (source "ticket"), documentation (source "doc": a procedure, or program behaviour that
   shows what to check or what to fix), or the error code reference's actions (source "reference",
   citing its REF- ID). Use only the card steps that apply to this report, in the card's order and
   with its detail; drop steps for situations the report doesn't describe. Every step is a direct
   instruction the engineer can act on: fold a card step that only identifies or classifies into
   the next action (for example "Close all locks on IN0018.MOV, the issue-ticket file"). Never invent
   commands, menu options, tools or file names: when the evidence gives an action but not the exact
   method, state the action at the level of detail the evidence gives.
5. Cite evidence_ids exactly as written inside the square brackets in the catalogue, without the
   brackets (write PC-01, not [PC-01]). Card steps cite the card ID plus any ticket IDs or card
   documents listed in that step's refs.
6. Write it as the support team's own runbook. The engineer never sees the catalogue: never write
   card IDs (PC-..), "pattern card", "card" or "catalogue" in root_cause, steps, preventive_actions
   or missing_information; say "past incidents" or "the documentation". Citations go only in
   evidence_ids. Never say that something is undocumented, unknown, unconfirmed or needs approval,
   and never tell the engineer to ask or confirm with the support team or an SME, unless the
   evidence itself says to escalate to someone.
7. Preventive actions come from the card or the reference, or from recurrence or program behaviour
   the evidence shows. Keep only those that apply to this report.
8. missing_information lists only facts about this incident the engineer can check (messages, logs,
   screens, who is affected).
9. escalation: take it from the matched card unless the evidence shows a program defect (code_change).
   Wrong or garbage content in output a program generates (a report, file or ticket it writes) is
   code_change: support cannot correct program logic, so the steps are the checks that separate bad
   input from a program fault and what to hand the developer, not operator fixes.
   With no card: code_change_if_repeated when the reference or documentation points to a program path
   that a fresh session may clear, code_change when the documentation shows a defect, none when the
   reference's actions resolve it, unknown only when nothing in the evidence explains it.
10. judgement: high when the symptom and identifiers match the card closely, or, with no card, when the
    reference and documentation explain the failure directly; medium when only partly; low when the
    evidence is thin or conflicting.
11. Be brief and concrete. Write for an engineer at a terminal.
13. One-off transaction glitches: when a single ticket, reserve, print or transaction went wrong and
    the user has already worked around it (reprinted, retried, re-entered), the fault may not
    recur. The first step is to check whether it still reproduces; root_cause states the likely
    cause conditionally ("If it reproduces, ..."); include closing it as not reproducible when the
    check shows nothing wrong, and escalation is not_reproducible unless the evidence shows a
    repeatable fault. This does not apply to file errors (invalid file structure, error numbers,
    locks): those keep their fix even when work continues.
12. Diagnosis (no card fits): explain what the program was doing, what the error means (from the
    reference) and the most likely reason, using the documented program behaviour (for example how
    the programs open, close and handle the file named). Commit to a working diagnosis: root_cause
    names the most likely cause and the reasoning that singles it out, such as what the failing items
    share that working ones don't (a common input file, routine or time window), written as "Most
    likely ..., because ...". Name the actual files, sections and fields from the documentation.
    Then say briefly what else it could be and which check tells them apart. Order the steps the way
    an engineer works:
    the safe first action, how to tell a one-off from a repeatable fault, then what to send for a
    fix. Include what not to do when the reference or the evidence says so. The investigation's
    hypotheses are leads only: keep what the evidence supports and drop the rest."""


def _user_prompt(query: str, understood: Understood, catalogue: Catalogue, decision: GateDecision,
                 hypotheses: list[str] | None = None) -> str:
    ids = "; ".join(f"{k}: {', '.join(v)}" for k, v in understood.ids.items()) or "none found"
    leads = ("Investigation hypotheses (leads to test, not evidence):\n"
             + "\n".join(f"- {h}" for h in hypotheses) + "\n") if hypotheses else ""
    return (
        f"Reported issue:\n{query}\n\n"
        f"Identifiers found in the report: {ids}\n"
        f"System: {understood.system}\n"
        f"Search assessment: {' '.join(decision.reasons)}\n"
        f"{leads}\n"
        f"Evidence catalogue:\n\n{catalogue.prompt()}"
    )


def _clean_id(value: str) -> str:
    """The catalogue shows IDs as "[PC-01]"; models sometimes copy the brackets."""
    return value.strip().strip("[]").strip()


def normalise(answer: LlmAnswer) -> LlmAnswer:
    for item in answer.evidence:
        item.evidence_id = _clean_id(item.evidence_id)
    for step in answer.resolution_steps:
        step.evidence_ids = [_clean_id(e) for e in step.evidence_ids]
    if answer.matched_pattern:
        answer.matched_pattern = _clean_id(answer.matched_pattern) or None
    return answer


def check(answer: LlmAnswer, catalogue: Catalogue) -> list[str]:
    """Problems with the answer's citations and sources; empty when it passes."""

    allowed = catalogue.allowed()
    errors = []

    if answer.matched_pattern and answer.matched_pattern not in catalogue.cards:
        errors.append(f"matched_pattern {answer.matched_pattern} is not one of the cards shown")

    for item in answer.evidence:
        if item.evidence_id not in allowed:
            errors.append(f"evidence {item.evidence_id} is not in the catalogue")

    for n, step in enumerate(answer.resolution_steps, 1):
        unknown = [e for e in step.evidence_ids if e not in allowed]
        if unknown:
            errors.append(f"step {n} cites IDs not in the catalogue: {', '.join(unknown)}")
        if not step.evidence_ids:
            errors.append(f"step {n} has source {step.source} but cites no evidence")
        if step.source == "card" and not any(e in catalogue.cards for e in step.evidence_ids):
            errors.append(f"step {n} has source card but does not cite a card ID")
        if step.source == "doc" and not any(e.startswith(("DOC:", "DOC-")) or e in catalogue.cards
                                            for e in step.evidence_ids):
            errors.append(f"step {n} has source doc but cites no documentation")
        if step.source == "reference" and not any(e.startswith("REF-") for e in step.evidence_ids):
            errors.append(f"step {n} has source reference but cites no REF- reference")

    return errors


def write(
    query: str, understood: Understood, catalogue: Catalogue, decision: GateDecision,
    hypotheses: list[str] | None = None,
) -> tuple[LlmAnswer, list[str], list[TraceNode]]:
    """Writes the answer; retries once if the checks fail. Returns (answer, remaining errors, trace)."""

    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(query, understood, catalogue, decision, hypotheses)}]
    nodes: list[TraceNode] = []

    for attempt in (1, 2):
        started = time.monotonic()
        response = with_retry(lambda: chat_client().chat.completions.parse(
            model=chat_model(), messages=messages, response_format=LlmAnswer))
        answer = response.choices[0].message.parsed
        if answer:
            answer = normalise(answer)
        with trace("check_citations", run_type="tool", inputs={"attempt": attempt}) as span:
            errors = check(answer, catalogue) if answer else ["the model returned no parsable answer"]
            span.end(outputs={"passed": not errors, "errors": errors})
        nodes.append(TraceNode(
            name="write_rca" if attempt == 1 else "write_rca_retry",
            ms=int((time.monotonic() - started) * 1000),
            model=response.model,
            tokens=response.usage.total_tokens if response.usage else None,
            detail={"check_errors": errors},
        ))
        if not errors:
            return answer, [], nodes
        messages += [{"role": "assistant", "content": response.choices[0].message.content or ""},
                     {"role": "user", "content": "Your answer failed these checks:\n- " + "\n- ".join(errors)
                      + "\nReturn the corrected answer."}]

    return answer, errors, nodes
