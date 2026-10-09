"""Finds the identifiers an RCA depends on and maps them to canonical names.

Embeddings are poor at codes: IN001.MST, IN0011.MST and IN0001.MST look alike to
them and mean three different things. Everything here is deterministic, so the
same text always yields the same IDs, and every mapping comes from
knowledge/entities.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from rca.config import ENTITIES_YAML


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

AJS_RE = re.compile(
    r"AJS00\d(?P<severity>[EI])\s+"
    r"(?:ABEND=(?P<system>S[0-9A-F]{3})\s+(?P<user>U\d{4})|JOB\s+FAILED\s+RSN=(?P<rsn>\d+))\s+"
    r"(?P<step>[A-Z0-9]{8})\((?P<job_id>JOB\d+)\)\s+JCL=(?P<jcl>[A-Z0-9.]+)",
    re.I,
)

TICKET_RE = re.compile(r"(?<![A-Za-z0-9])((?:INC|SCTASK|CHG|PRB|RITM|KB)\d{6,8})(?![0-9])", re.I)
PROGRAM_RE = re.compile(r"(?<![A-Za-z0-9])(F8RH[0-9A-Z]{4})(?![A-Za-z0-9])", re.I)
LEGACY_PROGRAM_RE = re.compile(
    r"(?<![A-Za-z0-9])(0[1-6](?:RMNIDX|RMNINVTR|RMNLMS|RH0442|RHE247|RH0101))(?![A-Za-z0-9])", re.I
)
JCL_RE = re.compile(r"JCL=([A-Z]\.[A-Z0-9]+)", re.I)
RSN_RE = re.compile(r"RSN=(\d+)", re.I)
DATASET_RE = re.compile(
    r"(?<![A-Za-z0-9.])((?:VSAMPROD|WRK|ACT|PROD|TEST)\.[A-Z0-9]+(?:\.[A-Z0-9]+)+)", re.I
)

# Data files: IN0011.MST, IN0011MST, IN001.mst, IN0018 (stem only), INKT0300.ACT.
FILE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<stem>(?:IN|LM|BM|SS)\d{3,4}|INKT\d{4})"
    r"(?:\s*\.\s*|)(?P<ext>MST|MOV|MOR|MOL|CTL|ACT|NDX|LOC|TMP|CRS|ODR|CSO|SPT|SER|REL)?"
    r"(?![A-Za-z0-9])",
    re.I,
)
# F8RH0442 report RPT068 writes MISCRPT068.HTM / .CSV / .TXT; the documentation names it RPT068.
REPORT_RE = re.compile(r"(?<![A-Za-z0-9])(?:MISC)?RPT(\d{3})(?:\.(?:HTML?|CSV|TXT))?(?![A-Za-z0-9])", re.I)
REMAN_NDX_RE = re.compile(r"(?<![A-Za-z0-9])REMAN\.NDX(?![A-Za-z0-9])", re.I)

# Abends as written in alerts: uppercase, system abends with a digit after the S.
ABEND_RE = re.compile(r"(?<![A-Za-z0-9])(S0C[0-9A-F]|S[0-9][0-9A-F]{2}|U\d{4})(?![A-Za-z0-9])")
SOC_TYPO_RE = re.compile(r"(?<![A-Za-z0-9])S[oO0][cC]\s?([0-9A-F])(?![A-Za-z0-9])")
IGNORED_ABENDS = {"S000", "U0000"}

# Error numbers need context ("error # 984", "code 9302", "#9802") unless they are
# one of the codes the entity file knows, which are recognised anywhere in a text
# that talks about errors, codes or locks.
ERROR_CONTEXT_RE = re.compile(
    r"(?:error|err|code|status)\s*(?:#|no\.?|number)?\s*[:#]?\s*-?(?P<code>\d{2,5})(?!\d)", re.I
)
HASH_CODE_RE = re.compile(r"#\s*-?(?P<code>\d{2,5})(?!\d)")
ERROR_TALK_RE = re.compile(r"\b(error|err|code|lock|locked|file)\b", re.I)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Catalogue:
    file_aliases: dict[str, str]          # upper-cased alias or phrase -> canonical file
    file_phrases: dict[str, str]          # lower-case phrase -> canonical file
    known_files: frozenset[str]
    stems: dict[str, list[str]]           # IN0018 -> [IN0018.MOV]
    legacy_programs: dict[str, str]       # 03RMNLMS -> F8RH0093
    known_error_codes: dict[str, str]     # code -> family
    abend_aliases: dict[str, str]
    job_aliases: dict[str, str]
    application_patterns: dict[str, re.Pattern]


def _phrase_pattern(phrases: list[str]) -> re.Pattern:
    alternatives = sorted({p.lower() for p in phrases}, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(p) for p in alternatives) + r")(?![a-z0-9])", re.I)


@lru_cache(maxsize=4)
def load_catalogue(path: Path = ENTITIES_YAML) -> Catalogue:
    data = yaml.safe_load(path.read_text())

    file_aliases: dict[str, str] = {}
    file_phrases: dict[str, str] = {}
    stems: dict[str, list[str]] = {}

    for entry in data.get("files", []):
        canonical = entry["id"].upper()
        file_aliases[canonical] = canonical
        stems.setdefault(canonical.split(".")[0], []).append(canonical)
        for alias in entry.get("aliases", []) or []:
            if " " in alias:
                file_phrases[alias.lower()] = canonical
            else:
                file_aliases[alias.upper()] = canonical

    abend_aliases = {}
    for entry in data.get("abends", []):
        abend_aliases[entry["code"].upper()] = entry["code"].upper()
        for alias in entry.get("aliases", []) or []:
            abend_aliases[alias.upper()] = entry["code"].upper()

    job_aliases = {}
    for entry in data.get("jobs", []):
        job_aliases[entry["id"].upper()] = entry["id"].upper()
        for alias in entry.get("aliases", []) or []:
            job_aliases[alias.upper()] = entry["id"].upper()

    return Catalogue(
        file_aliases=file_aliases,
        file_phrases=file_phrases,
        known_files=frozenset(e["id"].upper() for e in data.get("files", [])),
        stems=stems,
        legacy_programs={
            e["legacy"].upper(): e["id"].upper()
            for e in data.get("programs", []) if e.get("legacy")
        },
        known_error_codes={str(e["code"]): e["family"] for e in data.get("error_codes", [])},
        abend_aliases=abend_aliases,
        job_aliases=job_aliases,
        application_patterns={
            name: _phrase_pattern(phrases)
            for name, phrases in (data.get("applications") or {}).items()
        },
    )


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AjsAlert:
    severity: str          # E = abend, I = job failed
    abend: str | None      # U4005, S0C7, ...
    reason: str | None     # RSN=1
    step: str              # program that failed, e.g. F8RH0430
    job_id: str
    jcl: str               # A.F8RH0410


def parse_ajs_alert(text: str) -> AjsAlert | None:
    match = AJS_RE.search(text or "")
    if not match:
        return None
    system, user = match.group("system"), match.group("user")
    abend = None
    if system:
        abend = system.upper() if system.upper() != "S000" else user.upper()
    return AjsAlert(
        severity=match.group("severity").upper(),
        abend=abend,
        reason=f"RSN={match.group('rsn')}" if match.group("rsn") else None,
        step=match.group("step").upper(),
        job_id=match.group("job_id").upper(),
        jcl=match.group("jcl").upper(),
    )


def _add(bucket: list[str], value: str) -> None:
    if value and value not in bucket:
        bucket.append(value)


@dataclass
class Entities:
    tickets: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    jobs: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    unknown_files: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    error_families: list[str] = field(default_factory=list)
    abends: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    applications: list[str] = field(default_factory=list)
    alert: AjsAlert | None = None

    def ids(self) -> set[str]:
        """Every canonical ID, for matching against a card's signals."""
        return set(
            self.programs + self.jobs + self.datasets + self.files
            + self.error_codes + self.abends + self.reasons + self.reports
        )

    def is_empty(self) -> bool:
        return not self.ids() and not self.applications


def canonical_file(raw: str, catalogue: Catalogue | None = None) -> str | None:
    """Canonical name for a file mention, or None if it can't be pinned down."""

    catalogue = catalogue or load_catalogue()
    key = raw.upper().replace(" ", "")
    if key in catalogue.file_aliases:
        return catalogue.file_aliases[key]
    if "." not in key:
        candidates = catalogue.stems.get(key, [])
        return candidates[0] if len(candidates) == 1 else None
    return key


def extract(text: str, catalogue: Catalogue | None = None) -> Entities:
    catalogue = catalogue or load_catalogue()
    text = text or ""
    found = Entities()

    alert = parse_ajs_alert(text)
    if alert:
        found.alert = alert
        _add(found.jobs, alert.jcl)
        _add(found.programs, alert.step)
        if alert.abend:
            _add(found.abends, alert.abend)
        if alert.reason:
            _add(found.reasons, alert.reason)

    for match in TICKET_RE.finditer(text):
        _add(found.tickets, match.group(1).upper())

    for match in PROGRAM_RE.finditer(text):
        _add(found.programs, match.group(1).upper())
    for match in LEGACY_PROGRAM_RE.finditer(text):
        _add(found.programs, catalogue.legacy_programs.get(match.group(1).upper(), match.group(1).upper()))

    for match in JCL_RE.finditer(text):
        _add(found.jobs, match.group(1).upper())
    for alias, job in catalogue.job_aliases.items():
        if re.search(rf"(?<![A-Za-z0-9.]){re.escape(alias)}(?![A-Za-z0-9])", text, re.I):
            _add(found.jobs, job)

    for match in RSN_RE.finditer(text):
        _add(found.reasons, f"RSN={match.group(1)}")

    for match in REPORT_RE.finditer(text):
        _add(found.reports, f"RPT{match.group(1)}")

    for match in DATASET_RE.finditer(text):
        _add(found.datasets, match.group(1).upper())

    for match in FILE_RE.finditer(text):
        raw = match.group("stem") + (f".{match.group('ext')}" if match.group("ext") else "")
        canonical = canonical_file(raw, catalogue)
        if canonical:
            _add(found.files, canonical)
        else:
            _add(found.unknown_files, raw.upper())
    if REMAN_NDX_RE.search(text):
        _add(found.files, "REMAN.NDX")
    lowered = text.lower()
    for phrase, canonical in catalogue.file_phrases.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", lowered):
            _add(found.files, canonical)

    for match in ABEND_RE.finditer(text):
        code = match.group(1).upper()
        if code not in IGNORED_ABENDS:
            _add(found.abends, catalogue.abend_aliases.get(code, code))
    for match in SOC_TYPO_RE.finditer(text):
        _add(found.abends, f"S0C{match.group(1).upper()}")

    for pattern in (ERROR_CONTEXT_RE, HASH_CODE_RE):
        for match in pattern.finditer(text):
            _add(found.error_codes, match.group("code"))
    if ERROR_TALK_RE.search(text):
        for code in catalogue.known_error_codes:
            if re.search(rf"(?<!\d)-?{code}(?!\d)", text):
                _add(found.error_codes, code)
    for code in found.error_codes:
        family = catalogue.known_error_codes.get(code)
        if family:
            _add(found.error_families, family)

    for name, pattern in catalogue.application_patterns.items():
        if pattern.search(text):
            _add(found.applications, name)

    return found
