"""Supabase Postgres access.

The network on the demo machine drops connections now and then (DNS failures,
"can't assign requested address"). `Database` reconnects and retries, so a
dropped connection costs a second, not the investigation.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

import psycopg

from rca.config import require_env


CONNECT_OPTIONS = dict(connect_timeout=20, keepalives=1, keepalives_idle=30,
                       keepalives_interval=10, keepalives_count=3)
ATTEMPTS = 4


@contextmanager
def connect(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    with psycopg.connect(require_env("SUPABASE_DB_URL"), autocommit=autocommit, **CONNECT_OPTIONS) as conn:
        yield conn


class Database:
    """One autocommit connection that reconnects and retries on network errors.

    Use it for reads and idempotent writes; a retried statement may run twice.
    """

    def __init__(self):
        self._conn: psycopg.Connection | None = None

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(require_env("SUPABASE_DB_URL"), autocommit=True, **CONNECT_OPTIONS)
        return self._conn

    def _drop(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def execute(self, sql: str, params=None) -> psycopg.Cursor:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return self._connection().execute(sql, params)
            except psycopg.OperationalError:
                self._drop()
                if attempt == ATTEMPTS:
                    raise
                time.sleep(2 * attempt)
        raise RuntimeError("unreachable")

    def close(self) -> None:
        self._drop()


def to_vector(values: list[float]) -> str:
    """pgvector text form: [0.1,0.2,...]"""
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def to_sparsevec(weights: dict[int, float], dimensions: int) -> str:
    """pgvector sparsevec text form: {index:value,...}/dimensions, 1-based indices."""
    body = ",".join(f"{index + 1}:{value:.6f}" for index, value in sorted(weights.items()) if value)
    return "{" + body + "}/" + str(dimensions)
