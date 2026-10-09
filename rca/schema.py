"""The RCA response: what the pipeline returns and the UI renders.

Two layers:
- `LlmAnswer` is the only part the LLM writes (step 6). It is small on purpose,
  and every claim in it must cite evidence IDs from the catalogue it was given.
- `RcaResponse` is the full response. Code fills everything that can be exact:
  the matched card, recurrence, confidence and the trace.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Source = Literal["ticket", "doc", "card", "reference", "sme"]     # "sme" survives only in older saved runs
LlmSource = Literal["ticket", "doc", "card", "reference"]
Level = Literal["high", "medium", "low"]
Escalation = Literal["none", "code_change", "code_change_if_repeated", "not_reproducible", "unknown"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Written by the LLM
# ---------------------------------------------------------------------------


class LlmStep(_Model):
    text: str = Field(description="One action an engineer performs, in plain words.")
    source: LlmSource = Field(description="ticket = a past fix; doc = documentation (a procedure, or "
                                          "program behaviour that shows what to check or fix); card = the "
                                          "pattern card; reference = the error code reference.")
    evidence_ids: list[str] = Field(description="Evidence IDs from the catalogue that support this step.")


class LlmEvidence(_Model):
    evidence_id: str = Field(description="An ID exactly as it appears in the catalogue.")
    why: str = Field(description="One sentence: what this item shows about this incident.")


class LlmAnswer(_Model):
    matched_pattern: str | None = Field(description="Card ID (e.g. PC-01) that explains this incident, or null if none does.")
    root_cause: str = Field(description="The cause in 1-2 plain sentences, stated directly. Doubts go in "
                                        "missing_information and judgement, not here.")
    resolution_steps: list[LlmStep]
    preventive_actions: list[str]
    evidence: list[LlmEvidence]
    escalation: Escalation
    missing_information: list[str] = Field(description="Facts about this incident the engineer can check to "
                                                       "confirm the cause (messages, logs, who is affected).")
    judgement: Level = Field(description="How well the evidence fits this incident.")


# ---------------------------------------------------------------------------
# Returned to the UI
# ---------------------------------------------------------------------------


class Understood(_Model):
    system: Literal["online", "batch", "unknown"]
    applications: list[str] = []
    symptom: str
    ids: dict[str, list[str]] = {}       # files, error_codes, abends, jobs, programs, datasets, reasons


class Pattern(_Model):
    id: str
    title: str
    status: Literal["draft", "approved"]


class Confidence(_Model):
    score: float                          # 0..1
    level: Level
    reasons: list[str]


class Step(_Model):
    text: str
    source: Source
    evidence_ids: list[str] = []


class EvidenceItem(_Model):
    id: str
    kind: Literal["incident", "service_request", "change", "problem", "doc", "card", "reference"]
    title: str
    excerpt: str
    why: str = ""


class Recurrence(_Model):
    events: int
    tickets: int
    first_seen: str | None = None
    last_seen: str | None = None
    problems: list[str] = []


class Alternative(_Model):
    pattern_id: str
    title: str
    score: float


class TraceNode(_Model):
    name: str
    ms: int
    model: str | None = None
    tokens: int | None = None
    detail: dict = {}


class RcaResponse(_Model):
    query: str
    status: Literal["answered", "diagnosed", "no_match", "needs_input"]   # diagnosed = worked out, no past match
    understood: Understood
    clarifying_questions: list[str] = []
    pattern: Pattern | None = None
    root_cause: str = ""
    confidence: Confidence
    resolution_steps: list[Step] = []
    preventive_actions: list[str] = []
    escalation: Escalation = "unknown"
    missing_information: list[str] = []
    knowledge_gaps: list[str] = []      # what the documentation lacks; kept for the trace, never shown
    evidence: list[EvidenceItem] = []
    recurrence: Recurrence | None = None
    alternatives: list[Alternative] = []
    trace: list[TraceNode] = []
