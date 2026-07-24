"""Deterministic re-dispatch of a reclaimed (PARKED) work item.

When a worker is reclaimed to the human recovery lane (G5 — NEEDS_INPUT, a
runtime-budget kill, or rejected evidence), the item sits `PARKED /
HUMAN_DECISION` awaiting a `RECOVERY_DECISION`. This is the "retry it" decision,
realized as a deterministic transition back to `READY` with a fresh lease — the
first sliver of the Phase 4 decision lane. It goes through `Registry.transition`
(CAS + append-only event), never raw SQL, so custody and history stay intact.

The work_packet (and its `starting_ref`) is intentionally left unchanged: a
transition cannot rewrite the immutable packet, and it should not — a retry runs
the SAME contract. To retry against newer code, seed a fresh item instead.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from ..phase0.bootstrap import database_url
from ..phase1.registry import Registry, TransitionPatch

CONTROLLER_ID = "phase3-bootstrap-controller"
_REQUEUABLE_STAGES = ("PARKED",)


def requeue_patch(item: dict, now: datetime) -> TransitionPatch:
    """Pure: build the READY patch that re-arms a reclaimed item for dispatch.

    Fresh lease token + epoch bump fences any stale worker; deadlines are set in
    the future so `continuation_deadlines_valid` holds. recovery_attempts is left
    as-is (a human retry is not an automatic recovery attempt).
    """
    return TransitionPatch(
        stage="READY",
        wait_reason="NONE",
        custodian_type="CONTROLLER",
        custodian_id=CONTROLLER_ID,
        lease_token=_fresh_token(),
        lease_epoch_delta=1,
        lease_expires_at=now + timedelta(minutes=20),
        next_signal_type="DISPATCH_READY",
        next_signal_key=f"{item['id']}-requeue-{item['version']}",
        next_signal_deadline=now + timedelta(minutes=2),
        recovery_action={"action": "LAUNCH_REQUEUED_PACKET"},
    )


def _fresh_token() -> str:
    from uuid import uuid4

    return str(uuid4())


def requeue(work_item_id: str = "phase3-p3-notification-tick") -> str:
    now = datetime.now(UTC)
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        item = conn.execute(
            "SELECT * FROM cec.work_items WHERE id=%s", (work_item_id,)
        ).fetchone()
    if item is None:
        raise KeyError(work_item_id)
    if item["stage"] not in _REQUEUABLE_STAGES:
        raise RuntimeError(
            f"{work_item_id} is {item['stage']}, not requeueable "
            f"(only {'/'.join(_REQUEUABLE_STAGES)})"
        )
    registry = Registry(database_url())
    result = registry.transition(
        work_item_id=work_item_id,
        expected_version=int(item["version"]),
        source="phase3-requeue",
        source_event_id=f"{work_item_id}:REQUEUE:{item['version']}",
        event_type="RECOVERY_REQUEUE",
        observed_at=now,
        patch=requeue_patch(item, now),
        event_payload={"decision": "RETRY", "from_stage": item["stage"]},
    )
    return f"REQUEUED v{result.version}"


def main() -> int:
    work_item_id = sys.argv[1] if len(sys.argv) > 1 else "phase3-p3-notification-tick"
    print(requeue(work_item_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
