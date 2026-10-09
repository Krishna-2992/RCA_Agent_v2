"""Applies db/migrations/*.sql in name order, each at most once.

    python -m scripts.migrate
"""

from __future__ import annotations

import sys

from rca.config import ROOT
from rca.db import connect


def main() -> int:
    migrations = sorted((ROOT / "db" / "migrations").glob("*.sql"))

    with connect() as conn:
        conn.execute(
            "create table if not exists schema_migrations ("
            " name text primary key, applied_at timestamptz not null default now())"
        )
        applied = {row[0] for row in conn.execute("select name from schema_migrations")}

        for path in migrations:
            if path.name in applied:
                print(f"skip   {path.name}")
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            print(f"apply  {path.name}")

        version = conn.execute("select extversion from pg_extension where extname = 'vector'").fetchone()
        print(f"pgvector {version[0] if version else 'not installed'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
