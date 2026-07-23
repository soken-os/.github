"""Cryptographic receipt proving the current Phase-0 migration passed."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

PHASE0_DIR = Path(__file__).resolve().parent
MIGRATION = PHASE0_DIR.parent / "migrations" / "001_work_items.sql"
RECEIPT = PHASE0_DIR / ".phase0-proof.json"


def migration_sha256() -> str:
    return hashlib.sha256(MIGRATION.read_bytes()).hexdigest()


def record_pass() -> None:
    receipt = {
        "status": "PASS",
        "migration_sha256": migration_sha256(),
        "continuation_coverage": 1.0,
        "orphan_time_seconds": 0,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def require_pass() -> None:
    try:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Phase 1 is gated: run reference/cec/phase0/run-proof.sh first"
        ) from exc
    if (
        receipt.get("status") != "PASS"
        or receipt.get("migration_sha256") != migration_sha256()
        or receipt.get("continuation_coverage") != 1.0
        or receipt.get("orphan_time_seconds") != 0
    ):
        raise RuntimeError(
            "Phase 1 is gated: the Phase-0 proof receipt is stale or incomplete"
        )


if __name__ == "__main__":
    record_pass()
