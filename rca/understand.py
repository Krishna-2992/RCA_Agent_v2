"""Steps 1-2: read the query, decide whether it can be searched, optionally rewrite it.

Everything that can be exact is done in code: AJS alerts are parsed with a regex
and identifiers come from the entity catalogue. The LLM rewrite is optional and
only adds wording for search; identifiers always come from the user's own text.
"""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, ConfigDict, Field

from rca.bm25 import tokenize
from rca.entities import Entities, extract
from rca.llm import chat_client, chat_model
from rca.schema import TraceNode, Understood


ONLINE_APPS = {"LMS", "Inventory", "Reman Index", "BOM", "Reporting"}

# Words that say nothing about what is wrong on their own.
GENERIC = {"issue", "problem", "help", "error", "please", "urgent", "need", "check", "lms", "inventory",
           "system", "application", "app", "not", "work", "working", "users", "user", "today"}


def understand(query: str) -> tuple[Understood, Entities]:
    entities = extract(query)

    if entities.alert or entities.abends or entities.reasons or "Batch" in entities.applications:
        system = "batch"
    elif entities.files or entities.error_codes or ONLINE_APPS & set(entities.applications):
        system = "online"
    else:
        system = "unknown"

    first_line = next((line.strip() for line in query.splitlines() if line.strip()), query.strip())
    ids = {name: values for name, values in (
        ("files", entities.files), ("error_codes", entities.error_codes), ("abends", entities.abends),
        ("jobs", entities.jobs), ("programs", entities.programs), ("datasets", entities.datasets),
        ("reasons", entities.reasons), ("reports", entities.reports),
    ) if values}

    return Understood(system=system, applications=entities.applications, symptom=first_line[:240], ids=ids), entities


def clarifying_questions(query: str, entities: Entities) -> list[str]:
    """Empty when the query can be searched; otherwise what to ask the user."""

    if entities.alert or entities.ids():
        return []
    content = [t for t in tokenize(query) if t not in GENERIC and not t.isdigit()]
    if entities.applications and len(content) >= 2:
        return []
    if len(content) >= 5:
        return []

    questions = ["What exact error number or message is on the screen (for example 984 or 9802)?"]
    if not entities.applications:
        questions.append("Which application or job is affected: LMS, Inventory, Reman Index, or a batch job?")
    questions.append("Does the error name a file (such as IN0011.MST) or a program (F8RH....)?")
    return questions


# ---------------------------------------------------------------------------
# Optional LLM rewrite (step 2)
# ---------------------------------------------------------------------------


class Rewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symptom: str = Field(description="What is failing, in one sentence, using the system's own terms.")
    search_text: str = Field(description="Short text to search past tickets and documentation with. "
                                         "Never add file names, error numbers, jobs or programs that the report does not contain.")


REWRITE_PROMPT = """You help search the ticket history of the REMAN COBOL applications
(LMS = Location Management System, Inventory, Reman Index security, BOM, and mainframe batch jobs run by AJS/Tidal).
Restate the user's report as a symptom and a short search text that uses the words support tickets use
(for example: "file lock", "corrupted file", "tickets not printing", "job failed", "abend", "reserve", "password reset").
Do not guess identifiers: never add a file name, error number, job or program that is not in the report."""


def rewrite(query: str) -> tuple[Rewrite, TraceNode]:
    started = time.monotonic()
    response = chat_client().chat.completions.parse(
        model=chat_model(),
        messages=[{"role": "system", "content": REWRITE_PROMPT}, {"role": "user", "content": query}],
        response_format=Rewrite,
    )
    parsed = response.choices[0].message.parsed or Rewrite(symptom=query[:200], search_text=query)
    node = TraceNode(name="rewrite", ms=int((time.monotonic() - started) * 1000), model=response.model,
                     tokens=response.usage.total_tokens if response.usage else None,
                     detail=json.loads(parsed.model_dump_json()))
    return parsed, node
