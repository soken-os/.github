"""Multi-lane concurrency proof: many builds through one CEC, at the same time.

The locked execution model (Option B) is ONE CEC, ONE registry, ONE controller
process per program. Every proof so far has been single-lane -- P3 reached
COMPLETE alone. This exercises the property no single-lane run can:

  1. concurrent controllers over a shared registry never double-dispatch, and
     never orphan work either;
  2. every lane keeps INDEPENDENT custody -- its own lease token, its own
     worktree, its own per-attempt command/sidecar namespace;
  3. NO CROSS-LANE FATE-SHARING: a lane that fails (returns a non-completion
     claim) parks in the human lane while every healthy lane still reaches a
     mechanically-verified COMPLETE.

This runs against the real Postgres registry, the real CAS/lease machinery, and
the real `verify_code_change_claim` -- the lane workers are deterministic
scripts rather than models, but the evidence they produce is verified by the
same code path that verified P3.

Scope honesty: controllers run here as threads sharing one repo_root, not as
separate OS processes. That exercises the registry-level concurrency (the part
that was genuinely unproven); true process-per-program isolation is a deployment
property, and `BootstrapController.__init__` calls `os.chdir`, which is safe
across these threads only because they share a repo. The live N-real-worker run
on the Mac remains the final acceptance, exactly as it was for P3.
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

# Three programs, two lanes each -- the shape of "soken + roofing + field capture
# all building at once". Program names are harness-scoped so they can never
# collide with a real CEC row.
PROGRAMS = ["mlane-alpha", "mlane-beta", "mlane-gamma"]
LANES_PER_PROGRAM = 2
# One lane is deliberately poisoned; the proof is that it does NOT take the
# others down with it.
POISONED = "mlane-beta-1"
EDIT_FILE = "reference/cec/README.md"


def _lane_id(program: str, index: int) -> str:
    return f"{program}-{index}"


def _packet(worktree_root: Path, lane: str, starting_ref: str) -> dict:
    """A SCRIPT packet whose worker produces real, verifiable evidence."""
    # Artifacts live inside the lane's own worktree; the verifier excludes them
    # from the changed-path set, so the only change is the tracked edit.
    worktree = worktree_root / lane
    artifact = worktree / "runtime" / "test-output.txt"
    diff_artifact = worktree / "runtime" / "change.diff"
    mode = "needs_input" if lane == POISONED else "done"
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
            sys.executable,
            "-m",
            "reference.cec.phase3.multilane_worker",
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
        conn.execute("DELETE FROM cec.events WHERE work_item_id=%s", (lane,))
        conn.execute("DELETE FROM cec.work_items WHERE id=%s", (lane,))
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


def _rows(lanes: list[str]) -> dict[str, dict]:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        found = conn.execute(
            "SELECT * FROM cec.work_items WHERE id = ANY(%s)", (lanes,)
        ).fetchall()
    return {str(row["id"]): dict(row) for row in found}


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
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute("DELETE FROM cec.events WHERE work_item_id = ANY(%s)", (lanes,))
        conn.execute("DELETE FROM cec.work_items WHERE id = ANY(%s)", (lanes,))


def test_lanes_run_concurrently_with_independent_custody(tmp_path):
    if not os.environ.get("CEC_RUN_POSTGRES_TESTS"):
        pytest.skip("set CEC_RUN_POSTGRES_TESTS=1 to run the multi-lane proof")
    phase2_migrate()

    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    bridge_outbox = tmp_path / "outbox"
    bridge_outbox.mkdir()
    starting_ref = current_ref()

    lanes = [
        _lane_id(program, i)
        for program in PROGRAMS
        for i in range(LANES_PER_PROGRAM)
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

    def drive(program: str) -> None:
        deadline = time.time() + 120
        try:
            while time.time() < deadline:
                for lane_id, _outcome in run_scan_once(controllers[program]):
                    if lane_id not in claimed[program]:
                        claimed[program].append(lane_id)
                rows = _rows(lanes)
                mine = [r for lid, r in rows.items() if r["program"] == program]
                if mine and all(
                    r["stage"] in {"COMPLETE", "PARKED", "CANCELLED"} for r in mine
                ):
                    return
                time.sleep(0.5)
        except Exception as exc:  # surfaced as a test failure, never swallowed
            errors.append(f"{program}: {type(exc).__name__}: {exc}")

    try:
        threads = [
            threading.Thread(target=drive, args=(program,), daemon=True)
            for program in PROGRAMS
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=150)

        assert not errors, f"controller thread(s) raised: {errors}"
        rows = _rows(lanes)
        assert set(rows) == set(lanes), "a seeded lane vanished from the registry"

        # (1) No double-dispatch: each lane was claimed by exactly one program's
        #     controller, and no lane was orphaned.
        every_claim = [lane for lanes_ in claimed.values() for lane in lanes_]
        assert len(every_claim) == len(set(every_claim)), (
            f"a lane was claimed by two controllers: {every_claim}"
        )
        assert set(every_claim) == set(lanes), (
            f"lanes orphaned or mis-claimed: {sorted(set(lanes) - set(every_claim))}"
        )
        for program, lanes_ in claimed.items():
            assert all(rows[lane]["program"] == program for lane in lanes_), (
                f"{program}'s controller claimed another program's lane"
            )

        # (2) Independent custody: distinct lease tokens and distinct worktrees.
        tokens = [str(rows[lane]["lease_token"]) for lane in lanes]
        assert len(set(tokens)) == len(tokens), "two lanes shared a lease token"
        for lane in lanes:
            assert (worktree_root / lane).exists(), f"{lane} had no worktree of its own"

        # (3) No cross-lane fate-sharing: the poisoned lane parks for a human
        #     while every healthy lane still reaches a VERIFIED completion.
        assert rows[POISONED]["stage"] == "PARKED", (
            f"poisoned lane should park, got {rows[POISONED]['stage']}"
        )
        assert rows[POISONED]["custodian_type"] == "HUMAN"
        healthy = [lane for lane in lanes if lane != POISONED]
        for lane in healthy:
            assert rows[lane]["stage"] == "COMPLETE", (
                f"{lane} did not complete while a sibling lane failed: "
                f"{rows[lane]['stage']}/{rows[lane]['wait_reason']}"
            )
            assert rows[lane]["evidence_state"].get("completion_verified") is True, (
                f"{lane} completed without verified evidence"
            )
        # ...including lanes in the SAME program as the poisoned one, which is
        # where fate-sharing would show up first.
        same_program = [
            lane
            for lane in healthy
            if rows[lane]["program"] == rows[POISONED]["program"]
        ]
        assert same_program, "proof needs a healthy lane beside the poisoned one"
    finally:
        _cleanup(lanes, worktree_root)
