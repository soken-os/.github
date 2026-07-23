"""Apply additive Phase-2 tables only after both prior proof receipts pass."""

from pathlib import Path

import psycopg

from ..phase0.bootstrap import database_url
from ..phase1.gate import require_pass


def migrate() -> None:
    require_pass()
    migration = Path(__file__).parents[1] / "migrations" / "003_notification_outbox.sql"
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(migration.read_text(encoding="utf-8"))


if __name__ == "__main__":
    migrate()
    print("CEC Phase 2 one-task registry ready")
