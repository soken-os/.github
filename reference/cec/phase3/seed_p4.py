"""Seed the approved P4 packet (worktree-scoped worker Bash) into the registry."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg

from ..phase0.bootstrap import database_url
from ..phase2.bootstrap import migrate as phase2_migrate
from .packet import p4_packet


def seed(work_item_id: str = "phase3-p4-bash-scoping") -> str:
    phase2_migrate()
    now = datetime.now(UTC)
    packet = p4_packet()
    with psycopg.connect(database_url(), autocommit=True) as conn:
        existing = conn.execute(
            "SELECT id FROM cec.work_items WHERE id=%s", (work_item_id,)
        ).fetchone()
        if existing:
            return work_item_id
        conn.execute(
            """INSERT INTO cec.work_items
            (id,program,title,task_class,priority_class,estimated_duration_seconds,
             stage,wait_reason,custodian_type,custodian_id,lease_token,lease_epoch,
             lease_expires_at,next_signal_type,next_signal_key,next_signal_deadline,
             recovery_action,work_packet,authority_class)
            VALUES (%s,'CEC','P4: worktree-scoped worker Bash','CIRCUIT_BUILD',60,600,
                    'READY','NONE','CONTROLLER','phase3-bootstrap-controller',%s,0,%s,
                    'DISPATCH_READY','phase3-p4-seed',%s,%s::jsonb,%s::jsonb,
                    'ROUTINE')""",
            (
                work_item_id,
                uuid4(),
                now + timedelta(minutes=20),
                now + timedelta(minutes=2),
                json.dumps({"action": "LAUNCH_P4_PACKET"}),
                json.dumps(packet),
            ),
        )
    return work_item_id


def main() -> int:
    print(seed())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
