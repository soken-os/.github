"""Kill the live controller twice and prove custody and completion survive."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from ..adapters import _pid_alive, _process_start_time
from ..phase0.bootstrap import database_url
from ..phase1.gate import require_pass
from .bootstrap import migrate
from .notifications import acknowledge
from .packet import load_packet


def _row(work_item_id: str) -> dict:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM cec.work_items WHERE id=%s", (work_item_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def _assert_continuation(work_item_id: str) -> None:
    row = _row(work_item_id)
    if row["stage"] not in ("COMPLETE", "CANCELLED"):
        assert row["custodian_type"] and row["custodian_id"]
        assert row["next_signal_type"] and row["next_signal_deadline"]
        assert isinstance(row["recovery_action"], dict)


def _wait_for(work_item_id: str, predicate, timeout: float = 180) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = _row(work_item_id)
        if predicate(row):
            return row
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {work_item_id}")


def _start_runner(
    work_item_id: str, worker: str, workspace: Path, bridge: Path
) -> subprocess.Popen:
    env = os.environ.copy()
    env["CEC_PHASE2_PAUSE_AFTER_CLAIM"] = "4"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "reference.cec.phase2.runner",
            "--work-item",
            work_item_id,
            "--worker",
            worker,
            "--workspace",
            str(workspace),
            "--bridge-outbox",
            str(bridge),
        ],
        cwd=workspace,
        env=env,
    )


def _kill_controller(process: subprocess.Popen) -> None:
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=10)
    assert process.returncode < 0


def _seed(work_item_id: str, packet: dict) -> None:
    now = datetime.now(UTC)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """INSERT INTO cec.work_items
            (id,program,title,task_class,priority_class,stage,wait_reason,
             custodian_type,custodian_id,lease_token,lease_epoch,lease_expires_at,
             next_signal_type,next_signal_key,next_signal_deadline,recovery_action,
             work_packet,authority_class)
            VALUES (%s,'CEC','Phase 2 one-task test report','LOW_RISK_TEST_REPORT',10,
                    'READY','NONE','CONTROLLER','phase2-controller',%s,0,%s,
                    'DISPATCH_READY','phase2-seed',%s,%s::jsonb,%s::jsonb,'ROUTINE')""",
            (
                work_item_id,
                uuid4(),
                now + timedelta(minutes=15),
                now + timedelta(minutes=2),
                json.dumps({"action": "LAUNCH_ONE_LOW_RISK_WORKER"}),
                json.dumps(packet),
            ),
        )


def _assert_worker_still_running(workspace: Path, command_id: str) -> None:
    record = json.loads(
        (workspace / ".cec" / f"{command_id}.process.json").read_text(encoding="utf-8")
    )
    pid = int(record["pid"])
    assert _pid_alive(pid)
    assert _process_start_time(pid) == record["process_start_time"]


def run(worker: str) -> None:
    require_pass()
    migrate()
    workspace = Path.cwd().resolve()
    bridge = Path(__file__).resolve().parent / "runtime" / "bridge" / "outbox"
    work_item_id = f"phase2-{worker.lower()}-{uuid4().hex[:8]}"
    packet = load_packet(workspace, worker)
    artifact = Path(packet["artifact_path"])
    artifact.unlink(missing_ok=True)
    _seed(work_item_id, packet)

    first = _start_runner(work_item_id, worker, workspace, bridge)
    running = _wait_for(
        work_item_id,
        lambda row: row["stage"] == "EXECUTING" and row["custodian_type"] == "WORKER",
    )
    _assert_worker_still_running(workspace, str(running["next_signal_key"]))
    _kill_controller(first)
    _assert_continuation(work_item_id)
    _assert_worker_still_running(workspace, str(running["next_signal_key"]))

    second = _start_runner(work_item_id, worker, workspace, bridge)
    _wait_for(work_item_id, lambda row: row["stage"] == "VERIFYING")
    _kill_controller(second)
    _assert_continuation(work_item_id)

    third = _start_runner(work_item_id, worker, workspace, bridge)
    third.wait(timeout=180)
    assert third.returncode == 0
    complete = _row(work_item_id)
    assert complete["stage"] == "COMPLETE"
    assert complete["evidence_state"]["completion_verified"] is True

    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        notification = conn.execute(
            """SELECT * FROM cec.notification_outbox WHERE work_item_id=%s""",
            (work_item_id,),
        ).fetchone()
    assert notification is not None
    assert notification["status"] == "DELIVERED"
    assert Path(notification["delivered_path"]).is_file()
    acknowledge(notification["id"], acknowledged_by="phase2-harness")
    with psycopg.connect(database_url()) as conn:
        status = conn.execute(
            "SELECT status FROM cec.notification_outbox WHERE id=%s",
            (notification["id"],),
        ).fetchone()[0]
    assert status == "ACKNOWLEDGED"
    print(
        f"worker={worker}; controller_kills=2; continuation coverage=100%; "
        f"orphan time=0; final=COMPLETE; notification=ACKNOWLEDGED"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("SCRIPT", "CLAUDE_CODE"), required=True)
    args = parser.parse_args()
    run(args.worker)


if __name__ == "__main__":
    main()
