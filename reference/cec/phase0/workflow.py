"""DBOS crash-recoverable Phase-0 proof workflow."""

from __future__ import annotations

import json
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from dbos import DBOS

from .bootstrap import database_url
from .fixture import TASK_151_ID, TASK_151_LEASE

BOUNDARIES = (
    "REGISTERED",
    "DISPATCHED",
    "EXIT_OBSERVED",
    "RECOVERY_PLANNED",
    "EVIDENCE_VERIFIED",
)


def _continuation(stage: str, wait: str, signal_name: str, recovery: str) -> tuple:
    now = datetime.now(UTC)
    return (
        stage,
        wait,
        "CONTROLLER",
        "phase0-controller",
        TASK_151_LEASE,
        now + timedelta(minutes=5),
        signal_name,
        now + timedelta(minutes=2),
        json.dumps({"action": recovery}),
    )


@DBOS.step(retries_allowed=True, max_attempts=3)
def register_task() -> None:
    values = _continuation("READY", "NONE", "dispatch", "RETRY_DISPATCH")
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """INSERT INTO cec.work_items
            (id,program,title,task_class,priority_class,stage,wait_reason,custodian_type,custodian_id,
             lease_token,lease_epoch,lease_expires_at,next_signal_type,next_signal_deadline,recovery_action,
             work_packet,authority_class)
            VALUES (%s,'SOKEN','Task 151 premature termination replay','PHASE0',50,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,
                    '{"objective":"echo"}'::jsonb,'ROUTINE') ON CONFLICT (id) DO NOTHING""",
            (TASK_151_ID, *values),
        )


@DBOS.step(retries_allowed=True, max_attempts=3)
def transition(
    stage: str, wait: str, signal_name: str, recovery: str, evidence: bool = False
) -> None:
    values = _continuation(stage, wait, signal_name, recovery)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """UPDATE cec.work_items SET stage=%s,wait_reason=%s,custodian_type=%s,custodian_id=%s,
            lease_token=%s,lease_expires_at=%s,next_signal_type=%s,next_signal_deadline=%s,
            recovery_action=%s::jsonb,evidence_state=CASE WHEN %s THEN '{"completion_verified":true}'::jsonb ELSE evidence_state END,
            updated_at=clock_timestamp(),version=version+1 WHERE id=%s""",
            (*values, evidence, TASK_151_ID),
        )


@DBOS.step(retries_allowed=True, max_attempts=3)
def finish() -> None:
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """UPDATE cec.work_items SET stage='COMPLETE',wait_reason='NONE',completed_at=clock_timestamp(),
            evidence_state='{"completion_verified":true}'::jsonb,updated_at=clock_timestamp(),version=version+1
            WHERE id=%s""",
            (TASK_151_ID,),
        )


def _kill_after(boundary: str) -> None:
    marker = os.environ.get("CEC_PHASE0_KILL_AFTER")
    sentinel = Path(
        os.environ.get("CEC_PHASE0_KILL_SENTINEL", "/tmp/cec-phase0-killed")
    )
    if marker == boundary and not sentinel.exists():
        sentinel.write_text(boundary, encoding="utf-8")
        os.kill(os.getpid(), signal.SIGKILL)


@DBOS.workflow(max_recovery_attempts=20)
def task_151_replay() -> None:
    register_task()
    _kill_after("REGISTERED")
    transition("EXECUTING", "WORKER", "worker_exit", "OBSERVE_WORKER")
    _kill_after("DISPATCHED")
    # Exact failure: executor exited successfully and supplied no final result.
    transition("VERIFYING", "CI", "ci_terminal", "RECONCILE_EXTERNAL_STATE")
    _kill_after("EXIT_OBSERVED")
    # Mechanical controller owns the wait; no model is launched to poll CI.
    transition("VERIFYING", "MERGE", "pr_merged", "ENABLE_AUTO_MERGE")
    _kill_after("RECOVERY_PLANNED")
    transition("ACCEPTING", "DEPLOY", "exact_sha_all_hosts", "VERIFY_DEPLOYMENT", True)
    _kill_after("EVIDENCE_VERIFIED")
    finish()
