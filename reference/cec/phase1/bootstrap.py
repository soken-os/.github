"""Apply Phase-1 migration only after the current Phase-0 proof passes."""

from pathlib import Path

import psycopg

from ..phase0.bootstrap import database_url
from ..phase0.gate import require_pass


def migrate() -> None:
    require_pass()
    migration = Path(__file__).parents[1] / "migrations" / "002_events_and_sentinel.sql"
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(migration.read_text(encoding="utf-8"))


if __name__ == "__main__":
    migrate()
    print("CEC Phase 1 shadow registry ready")
