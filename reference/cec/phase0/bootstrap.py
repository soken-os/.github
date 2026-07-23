"""Initialize the dedicated local registry; never points at product Postgres."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

DEFAULT_URL = "postgresql://cec:cec-phase0-local-only@127.0.0.1:55432/cec_phase0"


def database_url() -> str:
    url = os.environ.get("CEC_PHASE0_DATABASE_URL", DEFAULT_URL)
    if "railway" in url.lower() or "sokenweb" in url.lower():
        raise RuntimeError("Phase 0 refuses product/Railway databases")
    return url


def migrate() -> None:
    migration = Path(__file__).parents[1] / "migrations" / "001_work_items.sql"
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(migration.read_text(encoding="utf-8"))


if __name__ == "__main__":
    migrate()
    print("CEC Phase 0 registry ready")
