"""Step 5: investigate when no known issue clearly fits.

Search finds what resembles the report. When nothing clearly fits (a weak match, or an
error number the best match doesn't produce), the model plans a few targeted searches -
what the error means for the named file, how the programs handle that file, which program
and screen does the user's action - and code runs them over the documentation and the
incident history. What they find goes into the catalogue for the write step.

One small LLM call; the searches themselves cost no tokens.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from rca.llm import chat_client, chat_model, with_retry
from rca.references import FileStatus
from rca.schema import TraceNode, Understood
from rca.scoring import GateDecision
from rca.search import Searcher, SearchResult


MAX_SEARCHES = 4          # planned by the model
DOCS_PER_SEARCH = 2
MAX_DOCS = 6
MAX_INCIDENTS = 2

DOCUMENTED_PROGRAMS = """\
- 01RMNIDX / F8RH0071 Reman Index: security, configuration and master data in REMAN.NDX; file recovery (Option 34)
- 02RMNINVTR / F8RH0030 Inventory: receipts, issues, rollovers (updates hot parts), file recovery
- 03RMNLMS / F8RH0093 LMS: locations, tickets, reserves, hot parts updates
- 04RH0442 / F8RH0442 Reports: includes the hot parts and security reports
- 05RHE247 / F8RHE247 Inventory inquiry
- 06RH0101 / F8RH0101 Bill of materials"""

# File names as the technical documentation's COBOL FDs call them.
FD_NAMES = {"REMAN.NDX": "REMAN-INDEX", "IN0056.MST": "HOT-FILE", "IN0011.MST": "LOCATION-FILE",
            "IN0001.MST": "INV-MASTER"}

PLAN_PROMPT = f"""You are investigating a production issue in the REMAN COBOL applications that does not
clearly match any known issue. Plan searches of the program documentation (AWS Transform technical
documents for the programs below) and of the incident history to find what explains it.

Documented programs:
{DOCUMENTED_PROGRAMS}

The documentation describes COBOL sections and their file handling, for example: "9001-OPEN-INDEX
SECTION: Opens REMAN-INDEX for I-O by closing REMAN-INDEX, opening I-O, handling errors via
8910-FILE-ERROR". Write each search the way the documentation would describe the behaviour: section
style wording, file and FD names, and the operation (OPEN, CLOSE, READ, WRITE, error handling).

Cover, as the report needs: what the error means for the file named, how the programs open, close
and handle that file, and which program and screen performs the user's action. When the report names
specific reports, screens or jobs, search for each one's own section: what it reads, what it writes
and how it builds its output. When several named items fail together, what they share (a common input
file or routine) is the strongest lead, so search for it. Each search is one
plain phrase of at most 15 words; no boolean operators, quotes or instructions. Hypotheses are short
candidate explanations to test against what the searches find, most likely first; describe issues
by what happens, never by card or ticket IDs."""


class PlannedSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(description="One plain phrase of at most 15 words, worded like the documentation.")
    looking_for: str = Field(description="What this search should establish.")


class InvestigationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypotheses: list[str] = Field(description="Up to 3 candidate explanations, most likely first.")
    searches: list[PlannedSearch] = Field(description=f"2 to {MAX_SEARCHES} targeted searches.")


@dataclass
class Investigation:
    plan: InvestigationPlan
    doc_ids: list[str] = field(default_factory=list)
    incident_ids: list[str] = field(default_factory=list)
    focus_terms: set[str] = field(default_factory=set)
    node: TraceNode | None = None


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{2,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "when", "into", "file", "files", "which",
         "program", "programs", "section", "error", "errors", "user", "screen", "does", "how", "what"}


def focus_terms(*texts: str) -> set[str]:
    """Words worth centring a document excerpt on."""
    return {w.lower() for text in texts for w in _WORD.findall(text) if w.lower() not in _STOP}


def _plan_input(query: str, understood: Understood, result: SearchResult, decision: GateDecision,
                references: list[tuple[FileStatus, list[str]]]) -> str:
    ids = "; ".join(f"{k}: {', '.join(v)}" for k, v in understood.ids.items()) or "none found"
    files = [f for f in understood.ids.get("files", []) if f in FD_NAMES]
    lines = [f"Reported issue:\n{query}", "", f"Identifiers: {ids}"]
    if files:
        lines.append("Documentation names for these files: "
                     + ", ".join(f"{f} = {FD_NAMES[f]}" for f in files))
    reports = understood.ids.get("reports", [])
    if reports:
        lines.append("The documentation names these reports " + ", ".join(reports)
                     + " (their output files are MISCRPT<nnn>.HTM, .CSV and .TXT).")
    for ref, codes in references:
        lines.append(f"Error {', '.join(codes) or ref.status}: file status {ref.status} ({ref.name}) - {ref.meaning}")
    lines.append("Why no known issue fits: " + " ".join(decision.reasons))
    return "\n".join(lines)


def seed_searches(understood: Understood) -> list[PlannedSearch]:
    """Searches every investigation runs for the items the report names, whatever the model plans:
    the documentation sections that say what those reports read and how those files are handled."""
    seeds = []
    reports = understood.ids.get("reports", [])
    if reports:
        seeds.append(PlannedSearch(query=f"{' '.join(reports)} source input file read output",
                                   looking_for="what the named reports read and what they share"))
    for name in understood.ids.get("files", [])[:2]:
        fd = FD_NAMES.get(name, name)
        seeds.append(PlannedSearch(query=f"{fd} {name} OPEN CLOSE READ WRITE error handling",
                                   looking_for=f"how the programs open, close and handle {name}"))
    return seeds


def _short(query: str, words: int = 20) -> str:
    """Long queries dilute keyword search; keep the planner's phrase short."""
    return " ".join(query.split()[:words])


def investigate(
    query: str, understood: Understood, result: SearchResult, decision: GateDecision,
    references: list[tuple[FileStatus, list[str]]], searcher: Searcher,
    exclude_events: frozenset[str] = frozenset(),
) -> Investigation:
    started = time.monotonic()
    response = with_retry(lambda: chat_client().chat.completions.parse(
        model=chat_model(), response_format=InvestigationPlan,
        messages=[{"role": "system", "content": PLAN_PROMPT},
                  {"role": "user", "content": _plan_input(query, understood, result, decision, references)}]))
    plan = response.choices[0].message.parsed or InvestigationPlan(hypotheses=[], searches=[])
    # Models occasionally leak list syntax into a string; keep only real sentences.
    plan.hypotheses = [h.strip().rstrip("],\"' ") for h in plan.hypotheses if len(h.split()) >= 4][:3]
    searches = seed_searches(understood) + plan.searches[:MAX_SEARCHES]
    for search in searches:
        search.query = _short(search.query)

    shown_incidents = {h.id for h in result.incidents[:5]}
    doc_ids: list[str] = []
    incident_ids: list[str] = []
    found = []
    for search, (docs, incidents) in zip(searches, searcher.lookup_many(
            [s.query for s in searches], k=4, exclude_events=exclude_events)):
        new_docs = [h.id for h in docs if h.id not in doc_ids][:DOCS_PER_SEARCH]
        doc_ids += new_docs[:max(0, MAX_DOCS - len(doc_ids))]
        new_incident = next((h.id for h in incidents
                             if h.id not in shown_incidents and h.id not in incident_ids), None)
        if new_incident and len(incident_ids) < MAX_INCIDENTS:
            incident_ids.append(new_incident)
        found.append({"query": search.query, "looking_for": search.looking_for,
                      "docs": new_docs, "incident": new_incident})

    node = TraceNode(name="investigate", ms=int((time.monotonic() - started) * 1000), model=response.model,
                     tokens=response.usage.total_tokens if response.usage else None,
                     detail={"hypotheses": plan.hypotheses, "searches": found})
    return Investigation(plan=plan, doc_ids=doc_ids, incident_ids=incident_ids,
                         focus_terms=focus_terms(query, *(s.query for s in searches)), node=node)
