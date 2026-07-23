import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from reference.cec.phase0.bootstrap import database_url
from reference.cec.phase1.registry import Registry, TransitionPatch
from reference.cec.phase2.bootstrap import migrate
from reference.cec.phase2.notifications import acknowledge, deliver_pending

pytestmark = pytest.mark.postgres


def test_complete_atomically_enqueues_delivers_and_acknowledges(tmp_path):
    if not os.environ.get("CEC_RUN_POSTGRES_TESTS"):
        pytest.skip("set CEC_RUN_POSTGRES_TESTS=1")
    migrate()
    work_item_id = f"phase2-notify-{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    token = uuid4()
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """INSERT INTO cec.work_items
            (id,program,title,task_class,priority_class,stage,wait_reason,
             custodian_type,custodian_id,lease_token,lease_epoch,lease_expires_at,
             next_signal_type,next_signal_key,next_signal_deadline,recovery_action,
             work_packet,authority_class)
            VALUES (%s,'CEC','notify','PHASE2',1,'ACCEPTING','DEPLOY','CONTROLLER',
                    'test',%s,1,%s,'VERIFY','notify',%s,%s::jsonb,'{}'::jsonb,'ROUTINE')""",
            (
                work_item_id,
                token,
                now + timedelta(minutes=5),
                now + timedelta(minutes=2),
                json.dumps({"action": "VERIFY"}),
            ),
        )
    Registry(database_url()).transition(
        work_item_id=work_item_id,
        expected_version=1,
        source="phase2-test",
        source_event_id=f"{work_item_id}:complete",
        event_type="COMPLETE",
        observed_at=now,
        patch=TransitionPatch(
            "COMPLETE",
            "NONE",
            "CONTROLLER",
            "test",
            str(token),
            0,
            now + timedelta(minutes=5),
            "COMPLETE",
            "notify",
            now + timedelta(minutes=2),
            {"action": "NONE"},
            evidence_state={"completion_verified": True},
            completed_at=now,
        ),
        event_payload={"notification_markdown": "# Complete\n"},
    )
    with psycopg.connect(database_url()) as conn:
        notification_id, status = conn.execute(
            "SELECT id,status FROM cec.notification_outbox WHERE work_item_id=%s",
            (work_item_id,),
        ).fetchone()
    assert status == "PENDING"
    assert notification_id in deliver_pending(tmp_path)
    acknowledge(notification_id, acknowledged_by="test")
    with psycopg.connect(database_url()) as conn:
        assert (
            conn.execute(
                "SELECT status FROM cec.notification_outbox WHERE id=%s",
                (notification_id,),
            ).fetchone()[0]
            == "ACKNOWLEDGED"
        )
