"""Read-only executor-rollup metrics, derived from the existing ledger only.

Scott's requirement: track everything entering CEC and measure performance
across every program. These prove the metrics module computes correctly from
work_items/events without writing to either -- it is a reporting layer beside
the control plane, not a change to it.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from reference.cec.phase0.bootstrap import database_url
from reference.cec.phase2.bootstrap import migrate as phase2_migrate
from reference.cec.phase3.metrics import (
    all_programs,
    executor_rollup,
    program_metrics,
    stage_dwell_seconds,
)

pytestmark = pytest.mark.postgres

PROGRAM = "metrics-probe"


def _seed_completed(
    item_id: str,
    *,
    created_offset_seconds: float,
    lead_time_seconds: float,
    estimated_duration_seconds: int,
    recovery_attempts: int,
    lease_epoch: int,
) -> None:
    now = datetime.now(UTC)
    created_at = now - timedelta(seconds=created_offset_seconds)
    completed_at = created_at + timedelta(seconds=lead_time_seconds)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """INSERT INTO cec.work_items
            (id,program,title,task_class,priority_class,estimated_duration_seconds,
             stage,wait_reason,recovery_attempts,lease_epoch,work_packet,
             authority_class,created_at,completed_at,evidence_state)
            VALUES (%s,%s,'metrics probe','CIRCUIT_BUILD',60,%s,
                    'COMPLETE','NONE',%s,%s,'{}','ROUTINE',%s,%s,
                    '{"completion_verified": true}'::jsonb)""",
            (
                item_id,
                PROGRAM,
                estimated_duration_seconds,
                recovery_attempts,
                lease_epoch,
                created_at,
                completed_at,
            ),
        )


def _seed_parked(item_id: str) -> None:
    now = datetime.now(UTC)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """INSERT INTO cec.work_items
            (id,program,title,task_class,priority_class,estimated_duration_seconds,
             stage,wait_reason,custodian_type,custodian_id,lease_token,lease_epoch,
             lease_expires_at,next_signal_type,next_signal_key,next_signal_deadline,
             recovery_action,work_packet,authority_class)
            VALUES (%s,%s,'metrics probe parked','CIRCUIT_BUILD',60,600,
                    'PARKED','HUMAN_DECISION','HUMAN','scott',%s,1,%s,
                    'HUMAN_REVIEW','metrics',%s,'{}'::jsonb,'{}'::jsonb,'ROUTINE')""",
            (item_id, PROGRAM, uuid4(), now + timedelta(hours=1), now + timedelta(hours=1)),
        )


def _cleanup(*ids: str) -> None:
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            "UPDATE cec.work_items SET stage='CANCELLED' WHERE id = ANY(%s) AND stage <> 'COMPLETE'",
            (list(ids),),
        )
        # Rows this test itself inserted directly as COMPLETE are fine to remove
        # (no ledger events reference them); avoids leaking probe rows forever.
        conn.execute("DELETE FROM cec.work_items WHERE id = ANY(%s)", (list(ids),))


@pytest.fixture()
def pg(tmp_path):
    if not os.environ.get("CEC_RUN_POSTGRES_TESTS"):
        pytest.skip("set CEC_RUN_POSTGRES_TESTS=1 for the metrics module")
    phase2_migrate()


def test_program_metrics_computes_lead_time_and_first_pass_yield(pg):
    ids = ["metrics-a", "metrics-b", "metrics-c"]
    try:
        # Two clean completions, one that needed a recovery attempt.
        _seed_completed(ids[0], created_offset_seconds=1000, lead_time_seconds=100,
                         estimated_duration_seconds=600, recovery_attempts=0, lease_epoch=0)
        _seed_completed(ids[1], created_offset_seconds=1000, lead_time_seconds=200,
                         estimated_duration_seconds=600, recovery_attempts=0, lease_epoch=0)
        _seed_completed(ids[2], created_offset_seconds=1000, lead_time_seconds=900,
                         estimated_duration_seconds=600, recovery_attempts=2, lease_epoch=3)

        metrics = program_metrics(PROGRAM)

        assert metrics.completed_count == 3
        assert metrics.first_pass_yield == pytest.approx(2 / 3)
        # median of [100, 200, 900] -> 200
        assert metrics.median_lead_time_seconds == pytest.approx(200, rel=0.01)
        # mean estimate ratio: (100/600 + 200/600 + 900/600) / 3
        assert metrics.mean_estimate_ratio == pytest.approx(
            (100 / 600 + 200 / 600 + 900 / 600) / 3, rel=0.01
        )
        assert metrics.mean_lease_epoch_at_completion == pytest.approx(1.0, rel=0.01)
    finally:
        _cleanup(*ids)


def test_program_metrics_counts_parked_and_in_flight(pg):
    ids = ["metrics-parked-1"]
    try:
        _seed_parked(ids[0])
        metrics = program_metrics(PROGRAM)
        assert metrics.parked_count >= 1
        assert metrics.in_flight_count >= 1  # PARKED is non-terminal
    finally:
        _cleanup(*ids)


def test_program_with_no_items_reports_none_not_a_crash(pg):
    metrics = program_metrics("metrics-program-that-has-never-existed")
    assert metrics.completed_count == 0
    assert metrics.first_pass_yield is None
    assert metrics.median_lead_time_seconds is None


def test_all_programs_and_rollup_include_a_freshly_seeded_program(pg):
    ids = ["metrics-rollup-1"]
    try:
        _seed_completed(ids[0], created_offset_seconds=500, lead_time_seconds=50,
                         estimated_duration_seconds=300, recovery_attempts=0, lease_epoch=0)

        assert PROGRAM in all_programs()

        rollup = executor_rollup()
        assert PROGRAM in rollup["programs"]
        assert rollup["programs"][PROGRAM]["completed_count"] >= 1
    finally:
        _cleanup(*ids)


def test_metrics_module_writes_nothing(pg):
    """The reporting layer must be strictly read-only."""
    ids = ["metrics-readonly-1"]
    try:
        _seed_completed(ids[0], created_offset_seconds=100, lead_time_seconds=10,
                         estimated_duration_seconds=60, recovery_attempts=0, lease_epoch=0)
        with psycopg.connect(database_url()) as conn:
            before = conn.execute(
                "SELECT count(*) FROM cec.events"
            ).fetchone()[0]

        program_metrics(PROGRAM)
        executor_rollup()
        stage_dwell_seconds(ids[0])

        with psycopg.connect(database_url()) as conn:
            after = conn.execute("SELECT count(*) FROM cec.events").fetchone()[0]
        assert after == before, "metrics reporting must never write to the event ledger"
    finally:
        _cleanup(*ids)


def test_stage_dwell_uses_the_real_p3_event_history():
    """Ground truth: compute dwell for the actual P3/F1 COMPLETE row (Appendix H)
    if it is present in this registry, rather than only a synthetic seed."""
    if not os.environ.get("CEC_RUN_POSTGRES_TESTS"):
        pytest.skip("set CEC_RUN_POSTGRES_TESTS=1 for the metrics module")
    with psycopg.connect(database_url()) as conn:
        exists = conn.execute(
            "SELECT 1 FROM cec.work_items WHERE id = 'phase3-p3-notification-tick-v2'"
        ).fetchone()
    if not exists:
        pytest.skip("Appendix H's P3 row is not present in this registry")
    dwell = stage_dwell_seconds("phase3-p3-notification-tick-v2")
    assert dwell, "the real P3 completion has no computable stage dwell"
    assert all(seconds >= 0 for seconds in dwell.values())
