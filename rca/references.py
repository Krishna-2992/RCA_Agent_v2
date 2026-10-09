"""COBOL file status reference (knowledge/file_status.yaml).

Explains error numbers no past ticket covers. The Reman programs print a file error as
"<status><extended code>", so 4102 is status 41 and 984 is status 98 (see the YAML header).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from rca.config import KNOWLEDGE_DIR


FILE_STATUS_YAML = KNOWLEDGE_DIR / "file_status.yaml"


class FileStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    name: str
    messages: list[str] = []
    meaning: str
    category: Literal["data", "environment", "file_damage", "program_state", "lock"]
    causes: list[str]
    actions: list[str]
    avoid: list[str] = []
    seen_in: list[str] = []
    escalation: Literal["none", "code_change_if_repeated"]

    @property
    def ref_id(self) -> str:
        return f"REF-FS-{self.status}"

    def prompt_text(self, codes: list[str]) -> str:
        reported = f" Reported as error {', '.join(codes)}." if codes else ""
        lines = [f"[{self.ref_id}] Error code reference: file status {self.status} - {self.name}.{reported}",
                 f"Meaning: {self.meaning}",
                 "Usual causes: " + " | ".join(self.causes),
                 "First actions: " + " | ".join(self.actions)]
        if self.avoid:
            lines.append("Avoid: " + " | ".join(self.avoid))
        if self.seen_in:
            lines.append(f"Seen in past tickets: {', '.join(self.seen_in)}")
        if self.escalation == "code_change_if_repeated":
            lines.append("Escalation: a program fix if it repeats from a fresh session.")
        return "\n".join(lines)


@lru_cache(maxsize=1)
def load() -> dict[str, FileStatus]:
    data = yaml.safe_load(FILE_STATUS_YAML.read_text())
    return {entry["status"]: FileStatus(**entry) for entry in data["codes"]}


def status_of(code: str) -> str | None:
    """'4102' -> '41', '9802' -> '98', '984' -> '98', '46' -> '46'; None if it isn't a file status."""
    digits = code.lstrip("-#").strip()
    if not digits.isdigit() or len(digits) not in (2, 3, 4):
        return None
    status = digits[:2]
    return status if status in load() else None


def lookup(error_codes: list[str], text: str = "") -> list[tuple[FileStatus, list[str]]]:
    """References for the report's error codes, plus any whose message the report quotes
    (e.g. "Duplicate Open" with no number). Returns (reference, codes as reported)."""
    found: dict[str, list[str]] = {}
    for code in error_codes:
        status = status_of(code)
        if status:
            found.setdefault(status, []).append(code)
    lowered = text.lower()
    for status, ref in load().items():
        if status not in found and any(re.search(rf"\b{re.escape(m)}\b", lowered) for m in ref.messages
                                       if len(m.split()) > 1):
            found[status] = []
    reference = load()
    return [(reference[status], codes) for status, codes in found.items()]
