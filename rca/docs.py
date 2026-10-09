"""Turns the AWS Transform documentation into chunks for search.

Chunks follow each source's own structure (XML sections, DOCX headings, single
business rules), never fixed-size windows. Duplicate renderings (HTML, PDFs that
mirror the XML/JSON), the second business-rule run and the generic workshop PRD
are not loaded.

Measured choices carried over from POC v1 (BM25 harness, 16 queries):
- keep business rules (dropping them cost 0.10 MRR);
- thin 04RH0442 and 05RHE247 rules to decision-shaped types;
- 3,500-char chunks for prose, 1,200 for rules (packed rules dilute each other).
"""

from __future__ import annotations

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import yaml
from pypdf import PdfReader

from rca.config import DOCS_ROOT, ENTITIES_YAML


MAX_CHUNK_CHARS = 3500
RULE_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 60

RULE_STRATEGY = {"01RMNIDX": "all", "02RMNINVTR": "all", "03RMNLMS": "all",
                 "04RH0442": "filter", "05RHE247": "filter", "06RH0101": "all"}
KEEP_RULE_TYPES = {"Validation Rules", "Decision Rules", "Authorization Rules", "Policy Rules"}

# Trust tiers for documents (cards and tickets rank above all of these).
TIER = {"technical_xml": 3, "program_summary": 3, "overview": 3,
        "general": 4, "business_rules": 4, "catalogue": 5}

GENERAL_DOCS = (
    "Level 1 and Level 2 documents/06RH0101 - Executive Summary - GHC - V0.90.docx",
    "Level 1 and Level 2 documents/06RH0101 - Requirements Document - V0.80.docx",
    "Level 1 and Level 2 documents/REMAN - V0.81.docx",
)
CATALOGUE_PDF = "WorkingTransformOutputs_Prakash'sWorkshop/TransformCoverPage-1000Programs.pdf"

DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADING_RE = re.compile(r"^\d+(?:\.\d+)*\s*[A-Z][A-Za-z]")

# A chunk is a procedure (usable for resolution steps) when it sits in a
# control-flow / screen / rules section and names how a repair is done.
PROCEDURAL_SECTION_MARKERS = ("controlflow", "programstructure", "flowchart", "decisionpoints",
                              "usernotifications", "business_rules", "errorhandling", "recovery")
PROCEDURAL_CONTENT_MARKERS = ("recover1.exe", "-recover", "file recovery", "menu option", "function key",
                              "security level", "option-four", "check-security", "option 34")

# Page furniture repeated on every catalogue page, removed after newlines are collapsed.
CATALOGUE_NOISE_RE = re.compile(
    r"AWS Transform uses generative AI\. Use of AWS Transform is subject to AWS Responsible AI Policy"
    r"|https://aws\.amazon\.com/ai/responsible-ai/policy/"
    r"|Page \d+"
    r"|AWS Transform File Name One sentence summary"
    r"|File Name One sentence summary",
)
CATALOGUE_ENTRY_RE = re.compile(r"(F8RH[A-Z0-9]{3,5})\.(?:CBL|txt)\s+(.{40,400}?)(?=LAN COBOL|$)", re.I)
LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}


@dataclass(frozen=True)
class DocChunk:
    id: str
    source_file: str          # relative to DOCS_ROOT
    program: str | None       # F8RH id
    application: str | None
    section_path: str
    doc_type: str
    trust_tier: int
    role: str                 # mechanism | procedure | background
    text: str

    def search_text(self) -> str:
        header = " - ".join(part for part in (self.application, self.program, self.section_path) if part)
        return f"{header}\n\n{self.text}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _programs() -> dict[str, tuple[str, str]]:
    """legacy name -> (F8RH id, application), from entities.yaml."""
    data = yaml.safe_load(ENTITIES_YAML.read_text())
    return {p["legacy"]: (p["id"], p["application"]) for p in data["programs"] if p.get("legacy")}


def _clean(text: str) -> str:
    text = (text or "").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pack(pieces: list[str], max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Groups atomic pieces (paragraphs, rules) into chunks without splitting one."""

    chunks, buffer, size = [], [], 0
    for piece in (p.strip() for p in pieces):
        if not piece:
            continue
        if buffer and size + len(piece) > max_chars:
            chunks.append(_clean("\n".join(buffer)))
            buffer, size = [], 0
        buffer.append(piece)
        size += len(piece) + 1
    if buffer:
        chunks.append(_clean("\n".join(buffer)))
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


def _role(doc_type: str, section: str, text: str) -> str:
    if doc_type in ("catalogue", "general", "overview"):
        return "background"
    section_key = section.lower().replace(" ", "").replace("_", "")
    content = text.lower()
    if any(m.replace("_", "") in section_key for m in PROCEDURAL_SECTION_MARKERS) and \
            any(m in content for m in PROCEDURAL_CONTENT_MARKERS):
        return "procedure"
    return "mechanism"


def _docx_paragraphs(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for node in root.iter(DOCX_NS + "p"):
        text = "".join(run.text or "" for run in node.iter(DOCX_NS + "t")).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _by_heading(paragraphs: list[str]) -> list[tuple[str, list[str]]]:
    sections, title, body = [], "Introduction", []
    for paragraph in paragraphs:
        if HEADING_RE.match(paragraph) and len(paragraph) < 110:
            sections.append((title, body))
            title, body = paragraph, []
        else:
            body.append(paragraph)
    sections.append((title, body))
    return [(t, b) for t, b in sections if b]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class _Builder:
    def __init__(self):
        self.chunks: list[DocChunk] = []
        self._seen: dict[str, int] = {}

    def add(self, source_file, program, application, section, doc_type, texts):
        for text in texts:
            base = f"{doc_type}:{program or 'general'}:{re.sub(r'[^A-Za-z0-9]+', '-', section)[:60].strip('-')}"
            index = self._seen.get(base, 0)
            self._seen[base] = index + 1
            self.chunks.append(DocChunk(
                id=f"{base}:{index}",
                source_file=source_file,
                program=program,
                application=application,
                section_path=section,
                doc_type=doc_type,
                trust_tier=TIER[doc_type],
                role=_role(doc_type, section, text),
                text=text,
            ))


def _technical_xml(builder: _Builder, legacy: str, program: str, application: str) -> None:
    rel = f"TechnicalDocuments/{legacy}-cbl-0.4.4.xml"
    root = ET.parse(DOCS_ROOT / rel).getroot()
    for section in root:
        for sub in list(section) or [section]:
            body = _clean("".join(sub.itertext()))
            builder.add(rel, program, application, f"{section.tag}/{sub.tag}", "technical_xml",
                        _pack(body.split("\n")))


def _program_summary(builder: _Builder, legacy: str, program: str, application: str) -> None:
    matches = sorted((DOCS_ROOT / "Level 1 and Level 2 documents" / "YashFormat-ProgramSummary").glob(f"{legacy}*.docx"))
    if not matches:
        return
    rel = str(matches[0].relative_to(DOCS_ROOT))
    for title, body in _by_heading(_docx_paragraphs(matches[0])):
        builder.add(rel, program, application, title, "program_summary", _pack(body))


def _overview(builder: _Builder, legacy: str, program: str, application: str) -> None:
    rel = f"Summary/TechnicalDetails/{legacy}-cbl.json"
    path = DOCS_ROOT / rel
    if not path.exists():
        return
    overview = json.loads(path.read_text()).get("documentation", {}).get("high_level_overview", {})
    lines = [overview.get("overview", "")]
    lines += [f"- {f.get('feature')}: {f.get('description')}" for f in overview.get("key_features", [])]
    builder.add(rel, program, application, "overview", "overview", _pack(lines))


def _business_rules(builder: _Builder, legacy: str, program: str, application: str) -> None:
    rel = f"BusinessDocuments/{legacy}-cbl.json"
    path = DOCS_ROOT / rel
    if not path.exists():
        return
    rules = json.loads(path.read_text()).get("all_rules") or []
    if RULE_STRATEGY[legacy] == "filter":
        rules = [r for r in rules if r.get("Rule_Type") in KEEP_RULE_TYPES]
    lines = []
    for rule in rules:
        criteria = rule.get("Acceptance_Criteria") or {}
        line = f"[{rule.get('Rule_Id')}] {rule.get('Rule_Name')} ({rule.get('Rule_Type')}): {rule.get('Rule_Description')}"
        if criteria:
            line += f" GIVEN {criteria.get('Given')} WHEN {criteria.get('When')} THEN {criteria.get('Then')}"
        lines.append(line)
    builder.add(rel, program, application, "business_rules", "business_rules", _pack(lines, RULE_CHUNK_CHARS))


def _catalogue(builder: _Builder) -> None:
    reader = PdfReader(DOCS_ROOT / CATALOGUE_PDF)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    text = text.split("\nAttention\n")[0]            # the list of undocumented files follows
    for ligature, plain in LIGATURES.items():
        text = text.replace(ligature, plain)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", CATALOGUE_NOISE_RE.sub(" ", text))
    seen = set()
    for match in CATALOGUE_ENTRY_RE.finditer(text):
        program = match.group(1).upper()
        summary = _clean(match.group(2))
        if (program, summary) in seen:
            continue
        seen.add((program, summary))
        builder.add(CATALOGUE_PDF, program, None, "program catalogue (AI-generated one-liner)", "catalogue",
                    [f"Program {program}: {summary}"])


def _general(builder: _Builder) -> None:
    for rel in GENERAL_DOCS:
        path = DOCS_ROOT / rel
        if not path.exists():
            continue
        name = Path(rel).stem
        for title, body in _by_heading(_docx_paragraphs(path)):
            builder.add(rel, None, None, f"{name} / {title}", "general", _pack(body))


def load_doc_chunks() -> list[DocChunk]:
    if not DOCS_ROOT.is_dir():
        raise RuntimeError(f"Documentation folder not found: {DOCS_ROOT}")

    builder = _Builder()
    for legacy, (program, application) in _programs().items():
        _technical_xml(builder, legacy, program, application)
        _program_summary(builder, legacy, program, application)
        _overview(builder, legacy, program, application)
        _business_rules(builder, legacy, program, application)
    _catalogue(builder)
    _general(builder)
    return builder.chunks
