"""Read-only freshness sentinel for the Phase-1 shadow registry."""

from __future__ import annotations

import json

import psycopg

from ..phase0.bootstrap import database_url
from ..phase0.gate import require_pass


def stale_items() -> list[dict]:
    require_pass()
    with psycopg.connect(database_url(), row_factory=psycopg.rows.dict_row) as conn:
        rows = conn.execute(
            "SELECT * FROM cec.stale_nonterminal_items ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def main() -> int:
    rows = stale_items()
    print(json.dumps(rows, default=str, indent=2))
    return 2 if rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
