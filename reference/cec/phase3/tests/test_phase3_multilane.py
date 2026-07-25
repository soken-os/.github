"""Multi-lane concurrency proof: many builds through one CEC, at the same time.

The locked model (Option B) is ONE CEC, ONE registry, ONE controller process per
program. Every proof so far has been single-lane — P3 reached COMPLETE alone.
This exercises what no single-lane run can:

  1. concurrent controllers over a shared registry never double-dispatch, and
     never orphan work either;
  2. every lane keeps INDEPENDENT custody — its own lease token, its own
     worktree, its own per-attempt sidecar namespace;
  3. NO CROSS-LANE FATE-SHARING: a lane that fails parks in the human lane while
     every healthy lane still reaches a mechanically-verified COMPLETE —
     including the healthy lane in the *same program* as the failing one;
  4. exactly one worker launch per item even when a second same-program
     controller tries to run (finding M4).

This runs against the real Postgres registry, the real CAS/lease machinery, real
git worktrees, and the real `verify_code_change_claim`. The lane workers are
deterministic scripts rather than models, but the evidence they produce is
verified by the same code path that verified P3.

Scope honesty: controllers run here as threads sharing one repo_root, not as
separate OS processes. That exercises the registry-level concurrency, which is
the genuinely unproven part; cross-process singleton ownership is proven
separately in test_phase3_singleton.py, and the live N-real-worker run on the
Mac remains the final acceptance, exactly as it was for P3.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from reference.cec.contracts import WorkerKind
from reference.cec.phase0.bootstrap import database_url
from reference.cec.phase2.bootstrap import migrate as phase2_migrate
from reference.cec.phase3.controller import BootstrapController, run_scan_once
from reference.cec.phase3.packet import REPO_ROOT, current_ref

pytestmark = pytest.mark.postgres

# Run-scoped id suffix; see _lane_id.
RUN_ID = uuid4().hex[:8]

# Three programs, two lanes each — the shape of "soken + roofing + field capture
# all building at once". Names are harness-scoped so they can never collide with
# a real CEC row.
PROGRAMS = ["mlane-alpha", "mlane-beta", "mlane-gamma"]
LANES_PER_PROGRAM = 2
# One lane is deliberately poisoned; the proof is that it does NOT take the
# others down with it — least of all its own program's sibling.
POISONED_SUFFIX = "mlane-beta-1"
EDIT_FILE = "reference/cec/README.md"


def _poisoned(lanes: list[str]) -> str:
    return next(lane for lane in lanes if lane.startswith(POISONED_SUFFIX))


def _lane_id(program: str, index: int) -> str:
    # Run-scoped: cec.events is append-only (a trigger rejects DELETE), so a
    # previous run's rows can never be cleared. Unique ids per run keep runs
    # from colliding without violating the ledger's immutability.
    return f"{program}-{index}-{RUN_ID}"


def _packet(worktree_root: Path, lane: str, starting_ref: str) -> dict:
    """A SCRIPT packet whose worker produces real, verifiable evidence."""
    # Artifacts live inside the lane's own worktree; the verifier excludes them
    # from the changed-path set, so the only change is the tracked edit.
    worktree = worktree_root / lane
    artifact = worktree / "runtime" / "test-output.txt"
    diff_artifact = worktree / "runtime" / "change.diff"
    mode = "needs_input" if lane.startswith(POISONED_SUFFIX) else "done"
    return {
        "task_class": "CIRCUIT_BUILD",
        "objective": f"multi-lane concurrency proof lane {lane}",
        "starting_ref": starting_ref,
        "allowed_paths": [EDIT_FILE],
        "forbidden_paths": [],
        "new_files_allowed": False,
        "artifact_path": str(artifact),
        "diff_artifact_path": str(diff_artifact),
        "estimated_duration_seconds": 600,
        "priority_class": 60,
        "authority_class": "ROUTINE",
        "acceptance": {"tests": "lane worker evidence verifies"},
        "argv": [
            # By absolute path, NOT `-m`: the worker runs with cwd set to the
            # lane's worktree, which is checked out at starting_ref and therefore
            # does not contain this harness's own worker module.
            sys.executable,
            str(REPO_ROOT / "reference" / "cec" / "phase3" / "multilane_worker.py"),
            "--edit-file",
            EDIT_FILE,
            "--artifact",
            str(artifact),
            "--diff-artifact",
            str(diff_artifact),
            "--starting-ref",
            starting_ref,
            "--mode",
            mode,
            "--marker",
            lane,
        ],
    }


def _seed(lane: str, program: str, packet: dict) -> None:
    now = datetime.now(UTC)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """INSERT INTO cec.work_items
            (id,program,title,task_class,priority_class,estimated_duration_seconds,
             stage,wait_reason,custodian_type,custodian_id,lease_token,lease_epoch,
             lease_expires_at,next_signal_type,next_signal_key,next_signal_deadline,
             recovery_action,work_packet,authority_class)
            VALUES (%s,%s,%s,'CIRCUIT_BUILD',60,600,
                    'READY','NONE','CONTROLLER','phase3-bootstrap-controller',%s,0,%s,
                    'DISPATCH_READY','multilane-seed',%s,%s::jsonb,%s::jsonb,
                    'ROUTINE')""",
            (
                lane,
                program,
                f"multi-lane proof {lane}",
                uuid4(),
                now + timedelta(minutes=20),
                now + timedelta(minutes=5),
                json.dumps({"action": "LAUNCH_LANE"}),
                json.dumps(packet),
            ),
        )


def _cancel_residue() -> None:
    """Retire non-terminal rows left by earlier harness runs.

    The event ledger is append-only, so failed runs leave their work_items
    behind. Those rows are still non-terminal, so a controller for that program
    legitimately claims them — which would pollute this run's assertions. They
    are CANCELLED (exempt terminal stage), never deleted.
    """
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """UPDATE cec.work_items SET stage='CANCELLED', wait_reason='NONE',
               custodian_type=NULL, custodian_id=NULL, next_signal_type=NULL,
               next_signal_deadline=NULL, recovery_action=NULL
               WHERE program LIKE 'mlane-%' AND stage NOT IN ('COMPLETE','CANCELLED')"""
        )


def _rows(lanes: list[str]) -> dict[str, dict]:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        found = conn.execute(
            "SELECT * FROM cec.work_items WHERE id = ANY(%s)", (lanes,)
        ).fetchall()
    return {str(row["id"]): dict(row) for row in found}


def _dispatch_events(lane: str) -> int:
    """How many times this item was actually dispatched to a worker."""
    with psycopg.connect(database_url()) as conn:
        return int(
            conn.execute(
                """SELECT count(*) FROM cec.events
                WHERE work_item_id=%s AND event_type='WORKER_DISPATCHED'""",
                (lane,),
            ).fetchone()[0]
        )


def _cleanup(lanes: list[str], worktree_root: Path) -> None:
    for lane in lanes:
        path = worktree_root / lane
        if path.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(path)],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
            )
    subprocess.run(
        ["git", "worktree", "prune"], cwd=REPO_ROOT, check=False, capture_output=True
    )
    # cec.events is append-only and work_items is referenced by it, so harness
    # rows are CANCELLED (an exempt terminal stage) rather than deleted. Nothing
    # is left dispatchable, and the ledger keeps its full history.
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute(
            """UPDATE cec.work_items SET stage='CANCELLED', wait_reason='NONE',
               custodian_type=NULL, custodian_id=NULL, next_signal_type=NULL,
               next_signal_deadline=NULL, recovery_action=NULL
               WHERE id = ANY(%s) AND stage <> 'COMPLETE'""",
            (lanes,),
        )


def _drive_until_terminal(controllers: dict, lanes: list[str], claimed: dict, errors: list):
    """Run every program's controller concurrently until its lanes settle."""

    def drive(program: str) -> None:
        deadline = time.time() + 120
        try:
            while time.time() < deadline:
                for lane_id, _outcome in run_scan_once(controllers[program]):
                    # Scoped to this run: residue from other runs is not evidence.
                    if lane_id in lanes and lane_id not in claimed[program]:
                        claimed[program].append(lane_id)
                rows = _rows(lanes)
                mine = [r for r in rows.values() if r["program"] == program]
                if mine and all(
                    r["stage"] in {"COMPLETE", "PARKED", "CANCELLED"} for r in mine
                ):
                    return
                time.sleep(0.3)
        except Exception as exc:  # surfaced as a failure, never swallowed
            errors.append(f"{program}: {type(exc).__name__}: {exc}")

    threads = [
        threading.Thread(target=drive, args=(program,), daemon=True)
        for program in controllers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=150)


@pytest.fixture(scope="module")
def lanes_running(tmp_path_factory):
    if not os.environ.get("CEC_RUN_POSTGRES_TESTS"):
        pytest.skip("set CEC_RUN_POSTGRES_TESTS=1 to run the multi-lane proof")
    phase2_migrate()
    _cancel_residue()

    tmp_path = tmp_path_factory.mktemp("multilane")
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    bridge_outbox = tmp_path / "outbox"
    bridge_outbox.mkdir()
    starting_ref = current_ref()

    lanes = [
        _lane_id(program, i) for program in PROGRAMS for i in range(LANES_PER_PROGRAM)
    ]
    for program in PROGRAMS:
        for i in range(LANES_PER_PROGRAM):
            lane = _lane_id(program, i)
            _seed(lane, program, _packet(worktree_root, lane, starting_ref))

    # One controller per program (Option B), all sharing the one registry.
    controllers = {
        program: BootstrapController(
            repo_root=REPO_ROOT,
            worktree_root=worktree_root,
            bridge_outbox=bridge_outbox,
            worker_kind=WorkerKind.SCRIPT,
            program=program,
        )
        for program in PROGRAMS
    }
    claimed: dict[str, list[str]] = {program: [] for program in PROGRAMS}
    errors: list[str] = []
    try:
        _drive_until_terminal(controllers, lanes, claimed, errors)
        yield lanes, claimed, errors, _rows(lanes), worktree_root
    finally:
        _cleanup(lanes, worktree_root)


def test_no_lane_is_dispatched_by_two_controllers(lanes_running):
    lanes, claimed, errors, rows, _worktree_root = lanes_running
    assert not errors, f"controller thread(s) raised: {errors}"
    assert set(rows) == set(lanes), "a seeded lane vanished from the registry"

    every_claim = [lane for lanes_ in claimed.values() for lane in lanes_]
    assert len(every_claim) == len(set(every_claim)), (
        f"a lane was claimed by two controllers: {every_claim}"
    )
    # ...and nothing was orphaned either: scoping must not strand work.
    assert set(every_claim) == set(lanes), (
        f"lanes orphaned or mis-claimed: {sorted(set(lanes) - set(every_claim))}"
    )
    for program, lanes_ in claimed.items():
        assert all(rows[lane]["program"] == program for lane in lanes_), (
            f"{program}'s controller claimed another program's lane"
        )


def test_exactly_one_worker_launch_per_lane(lanes_running):
    """M4 under real concurrency: no lane was ever dispatched twice."""
    lanes, _claimed, errors, _rows_, _worktree_root = lanes_running
    assert not errors, f"controller thread(s) raised: {errors}"
    for lane in lanes:
        dispatches = _dispatch_events(lane)
        assert dispatches <= 1, (
            f"{lane} was dispatched {dispatches} times — duplicate worker launch"
        )


def test_each_lane_keeps_independent_custody(lanes_running):
    lanes, _claimed, errors, rows, worktree_root = lanes_running
    assert not errors, f"controller thread(s) raised: {errors}"

    tokens = [str(rows[lane]["lease_token"]) for lane in lanes]
    assert len(set(tokens)) == len(tokens), "two lanes shared a lease token"
    for lane in lanes:
        assert (worktree_root / lane).exists(), f"{lane} had no worktree of its own"


def test_a_failing_lane_does_not_stop_the_others(lanes_running):
    """The load-bearing assertion: no cross-lane fate-sharing."""
    lanes, _claimed, errors, rows, _worktree_root = lanes_running
    assert not errors, f"controller thread(s) raised: {errors}"

    assert rows[_poisoned(lanes)]["stage"] == "PARKED", (
        f"poisoned lane should park, got {rows[_poisoned(lanes)]['stage']}"
    )
    assert rows[_poisoned(lanes)]["custodian_type"] == "HUMAN"

    healthy = [lane for lane in lanes if lane != _poisoned(lanes)]
    for lane in healthy:
        assert rows[lane]["stage"] == "COMPLETE", (
            f"{lane} did not complete while a sibling lane failed: "
            f"{rows[lane]['stage']}/{rows[lane]['wait_reason']}"
        )
        assert rows[lane]["evidence_state"].get("completion_verified") is True, (
            f"{lane} completed without verified evidence"
        )

    # Fate-sharing would show up first in the poisoned lane's OWN program.
    same_program = [
        lane
        for lane in healthy
        if rows[lane]["program"] == rows[_poisoned(lanes)]["program"]
    ]
    assert same_program, "proof needs a healthy lane beside the poisoned one"
    for lane in same_program:
        assert rows[lane]["stage"] == "COMPLETE"
