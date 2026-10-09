"""Paths and settings. Secrets come from .env in the project root."""

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


SAMPLES_XLSX = ROOT / "Reman_samples.xlsx"
HARNESS_XLSX = ROOT / "Reman_incidents_harness.xlsx"
DOCS_ROOT = ROOT / "Reman" / "AWSTransform_Documentation"

KNOWLEDGE_DIR = ROOT / "knowledge"
CARDS_YAML = KNOWLEDGE_DIR / "pattern_cards.yaml"
ENTITIES_YAML = KNOWLEDGE_DIR / "entities.yaml"
EVAL_LABELS_YAML = ROOT / "evals" / "labels_evaluation.yaml"

BUILD_DIR = ROOT / "build"


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def require_env(name: str) -> str:
    value = env(name)
    if value is None:
        raise RuntimeError(f"Missing setting {name}. Add it to .env (see .env.example).")
    return value
