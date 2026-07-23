"""Receipt proving the accepted Phase-1 implementation passed on the Mac."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from ..phase0.gate import require_pass as require_phase0_pass

PHASE1_DIR = Path(__file__).resolve().parent
RECEIPT = PHASE1_DIR / ".phase1-proof.json"
BOUND_FILES = (
    PHASE1_DIR.parent / "migrations" / "002_events_and_sentinel.sql",
    PHASE1_DIR / "registry.py",
    PHASE1_DIR / "actions.py",
)


def implementation_sha256() -> str:
    digest = hashlib.sha256()
    for path in BOUND_FILES:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def record_pass() -> None:
    require_phase0_pass()
    RECEIPT.write_text(
        json.dumps(
            {
                "status": "PASS",
                "implementation_sha256": implementation_sha256(),
                "recorded_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def require_pass() -> None:
    require_phase0_pass()
    try:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Phase 2 is gated: run reference/cec/phase1/run-shadow-tests.sh first"
        ) from exc
    if (
        receipt.get("status") != "PASS"
        or receipt.get("implementation_sha256") != implementation_sha256()
    ):
        raise RuntimeError("Phase 2 is gated: the Phase-1 proof receipt is stale")


if __name__ == "__main__":
    record_pass()
