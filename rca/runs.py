"""Saves each query's result and the engineer's verdict (the feedback loop)."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from rca.db import Database
from rca.schema import RcaResponse


APP_VERSION = "v2.0-dev"


def save_run(conn: Database, response: RcaResponse, latency_ms: int) -> str:
    row = conn.execute(
        "insert into runs (query, result, pattern_id, confidence, confidence_level, latency_ms, app_version) "
        "values (%s, %s, %s, %s, %s, %s, %s) returning id",
        (response.query, Jsonb(response.model_dump(mode="json")),
         response.pattern.id if response.pattern else None,
         response.confidence.score, response.confidence.level, latency_ms, APP_VERSION),
    ).fetchone()
    return str(row[0])


def save_feedback(conn: Database, run_id: str, verdict: str, actual_fix: str = "", comment: str = "") -> None:
    conn.execute(
        "insert into feedback (run_id, verdict, actual_fix, comment) values (%s, %s, %s, %s)",
        (run_id, verdict, actual_fix or None, comment or None),
    )
