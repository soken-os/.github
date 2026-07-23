"""Durable notification delivery to an idempotent bridge markdown file."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from ..phase0.bootstrap import database_url
from ..phase1.gate import require_pass


def deliver_pending(bridge_outbox: Path) -> list[int]:
    require_pass()
    bridge_outbox.mkdir(parents=True, exist_ok=True)
    delivered: list[int] = []
    with psycopg.connect(database_url(), autocommit=True) as conn:
        rows = conn.execute(
            """SELECT id,work_item_id,body_markdown FROM cec.notification_outbox
            WHERE status='PENDING' ORDER BY id"""
        ).fetchall()
        for notification_id, work_item_id, body in rows:
            target = bridge_outbox / f"{work_item_id}-complete-{notification_id}.md"
            temporary = target.with_suffix(".md.tmp")
            temporary.write_text(body.rstrip() + "\n", encoding="utf-8")
            os.replace(temporary, target)
            conn.execute(
                """UPDATE cec.notification_outbox SET status='DELIVERED',
                delivery_attempts=delivery_attempts+1,delivered_path=%s,
                delivered_at=clock_timestamp() WHERE id=%s AND status='PENDING'""",
                (str(target), notification_id),
            )
            delivered.append(notification_id)
    return delivered


def acknowledge(notification_id: int, *, acknowledged_by: str) -> None:
    require_pass()
    with psycopg.connect(database_url(), autocommit=True) as conn:
        updated = conn.execute(
            """UPDATE cec.notification_outbox SET status='ACKNOWLEDGED',
            acknowledged_at=%s,acknowledged_by=%s
            WHERE id=%s AND status='DELIVERED' RETURNING id""",
            (datetime.now(UTC), acknowledged_by, notification_id),
        ).fetchone()
    if updated is None:
        raise RuntimeError("notification is not awaiting acknowledgement")
