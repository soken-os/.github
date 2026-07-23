import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from reference.cec.phase0.bootstrap import database_url, migrate as migrate_phase0
from reference.cec.phase1.registry import (
    EventContentMismatch,
    Registry,
    StaleTransition,
    TransitionPatch,
)

pytestmark = pytest.mark.postgres


def enabled():
    if not os.environ.get("CEC_RUN_POSTGRES_TESTS"):
        pytest.skip("set CEC_RUN_POSTGRES_TESTS=1 after docker compose up")


def migrate_all():
    enabled()
    migrate_phase0()
    migration = Path("reference/cec/migrations/002_events_and_sentinel.sql")
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(migration.read_text(encoding="utf-8"))


def seed(work_item_id):
    now = datetime.now(UTC)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute("DELETE FROM cec.work_items WHERE id=%s", (work_item_id,))
        conn.execute(
            """INSERT INTO cec.work_items
            (id,program,title,task_class,priority_class,stage,wait_reason,custodian_type,custodian_id,
             lease_token,lease_epoch,lease_expires_at,next_signal_type,next_signal_key,next_signal_deadline,
             recovery_action,recovery_attempts,max_recovery_attempts,work_packet,authority_class)
            VALUES (%s,'SOKEN','Phase 1 fixture','PHASE1',50,'EXECUTING','WORKER','WORKER','echo-1',
                    %s,1,%s,'WORKER_EXIT','echo-1',%s,%s::jsonb,0,3,'{}'::jsonb,'ROUTINE')""",
            (
                work_item_id,
                uuid4(),
                now + timedelta(minutes=5),
                now + timedelta(minutes=2),
                json.dumps({"action": "OBSERVE_WORKER"}),
            ),
        )


def patch(delta=1):
    now = datetime.now(UTC)
    return TransitionPatch(
        "EXECUTING",
        "WORKER",
        "CONTROLLER",
        "cec-controller",
        str(uuid4()),
        1,
        now + timedelta(minutes=5),
        "RETRY_OBSERVER",
        "retry-1",
        now + timedelta(minutes=1),
        {"action": "ESCALATE_RECOVERY_AT_CEILING"},
        delta,
    )


def test_cas_and_event_commit_atomically_and_duplicate_is_noop():
    migrate_all()
    work_item_id = f"phase1-{uuid4().hex[:8]}"
    seed(work_item_id)
    registry = Registry(database_url())
    source_event_id = f"{work_item_id}:same-event"
    first = registry.transition(
        work_item_id=work_item_id,
        expected_version=1,
        source="test",
        source_event_id=source_event_id,
        event_type="HOLD_UNOBSERVABLE",
        observed_at=datetime.now(UTC),
        patch=patch(),
        event_payload={"increment_recovery_attempts": True},
    )
    duplicate = registry.transition(
        work_item_id=work_item_id,
        expected_version=1,
        source="test",
        source_event_id=source_event_id,
        event_type="HOLD_UNOBSERVABLE",
        observed_at=datetime.now(UTC),
        patch=patch(),
        event_payload={"increment_recovery_attempts": True},
    )
    assert first.applied and duplicate.duplicate
    with psycopg.connect(database_url()) as conn:
        row = conn.execute(
            "SELECT version,recovery_attempts FROM cec.work_items WHERE id=%s",
            (work_item_id,),
        ).fetchone()
        count = conn.execute(
            "SELECT count(*) FROM cec.events WHERE work_item_id=%s", (work_item_id,)
        ).fetchone()[0]
    assert row == (2, 1)
    assert count == 1


def test_stale_cas_rolls_back_reserved_event():
    migrate_all()
    work_item_id = f"phase1-{uuid4().hex[:8]}"
    seed(work_item_id)
    registry = Registry(database_url())
    source_event_id = f"{work_item_id}:stale-event"
    with pytest.raises(StaleTransition):
        registry.transition(
            work_item_id=work_item_id,
            expected_version=99,
            source="test",
            source_event_id=source_event_id,
            event_type="HOLD_UNOBSERVABLE",
            observed_at=datetime.now(UTC),
            patch=patch(),
            event_payload={},
        )
    with psycopg.connect(database_url()) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM cec.events WHERE source_event_id=%s",
                (source_event_id,),
            ).fetchone()[0]
            == 0
        )


def test_duplicate_source_event_rejects_content_mismatch():
    migrate_all()
    work_item_id = f"phase1-{uuid4().hex[:8]}"
    seed(work_item_id)
    registry = Registry(database_url())
    source_event_id = f"{work_item_id}:content-addressed"
    registry.transition(
        work_item_id=work_item_id,
        expected_version=1,
        source="test",
        source_event_id=source_event_id,
        event_type="HOLD_UNOBSERVABLE",
        observed_at=datetime.now(UTC),
        patch=patch(),
        event_payload={"value": "original"},
    )
    with pytest.raises(EventContentMismatch):
        registry.transition(
            work_item_id=work_item_id,
            expected_version=1,
            source="test",
            source_event_id=source_event_id,
            event_type="HOLD_UNOBSERVABLE",
            observed_at=datetime.now(UTC),
            patch=patch(),
            event_payload={"value": "different"},
        )
    with psycopg.connect(database_url()) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM cec.events WHERE source_event_id=%s",
                (source_event_id,),
            ).fetchone()[0]
            == 1
        )


def test_events_are_append_only():
    migrate_all()
    work_item_id = f"phase1-{uuid4().hex[:8]}"
    seed(work_item_id)
    registry = Registry(database_url())
    result = registry.transition(
        work_item_id=work_item_id,
        expected_version=1,
        source="test",
        source_event_id=f"{work_item_id}:immutable-event",
        event_type="RENEW_LEASE",
        observed_at=datetime.now(UTC),
        patch=patch(0),
        event_payload={},
    )
    with psycopg.connect(database_url(), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM cec.events WHERE id=%s", (result.event_id,))


def test_sentinel_view_reports_overdue_nonterminal_item():
    migrate_all()
    work_item_id = f"phase1-{uuid4().hex[:8]}"
    seed(work_item_id)
    old_update = datetime.now(UTC) - timedelta(minutes=10)
    old_deadline = datetime.now(UTC) - timedelta(minutes=5)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """UPDATE cec.work_items
            SET updated_at=%s,lease_expires_at=%s,next_signal_deadline=%s WHERE id=%s""",
            (old_update, old_deadline, old_deadline, work_item_id),
        )
        row = conn.execute(
            "SELECT freshness_violation FROM cec.stale_nonterminal_items WHERE id=%s",
            (work_item_id,),
        ).fetchone()
    assert row == ("LEASE_OVERDUE",)
