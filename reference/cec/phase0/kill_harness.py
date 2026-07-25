"""SIGKILL the DBOS controller after every step and prove zero orphan time."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import psycopg

from .bootstrap import database_url, migrate
from .fixture import TASK_151_ID
from .workflow import BOUNDARIES

CONTINUATION_QUERY = """
SELECT stage, custodian_type, custodian_id, next_signal_type,
       next_signal_deadline, recovery_action
FROM cec.work_items WHERE id=%s
"""


def assert_continuation() -> None:
    with psycopg.connect(database_url()) as conn:
        row = conn.execute(CONTINUATION_QUERY, (TASK_151_ID,)).fetchone()
    assert row is not None
    stage, custodian_type, custodian_id, signal_type, deadline, recovery = row
    if stage not in ("COMPLETE", "CANCELLED"):
        assert all((custodian_type, custodian_id, signal_type, deadline, recovery))


def reset_fixture() -> None:
    migrate()
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute("DELETE FROM cec.work_items WHERE id=%s", (TASK_151_ID,))


def run_boundary(boundary: str, serial: int, run_id: str) -> None:
    reset_fixture()
    with tempfile.TemporaryDirectory(prefix="cec-phase0-") as temp:
        sentinel = Path(temp) / "killed"
        env = os.environ.copy()
        env.update(
            CEC_PHASE0_KILL_AFTER=boundary,
            CEC_PHASE0_KILL_SENTINEL=str(sentinel),
            CEC_PHASE0_WORKFLOW_ID=f"task151-kill-{run_id}-{serial}-{boundary.lower()}",
        )
        first = subprocess.run(
            [sys.executable, "-m", "reference.cec.phase0.runner"],
            env=env,
            check=False,
        )
        assert first.returncode < 0, (boundary, first.returncode)
        assert sentinel.read_text(encoding="utf-8") == boundary
        # The process is dead here. The registry must still expose all four
        # continuation fields: custodian, signal, deadline, recovery action.
        assert_continuation()
        second = subprocess.run(
            [sys.executable, "-m", "reference.cec.phase0.runner"],
            env=env,
            timeout=30,
            check=False,
        )
        assert second.returncode == 0
        assert_continuation()
        with psycopg.connect(database_url()) as conn:
            row = conn.execute(
                "SELECT stage FROM cec.work_items WHERE id=%s", (TASK_151_ID,)
            ).fetchone()
            assert row is not None, "work item vanished"
            stage = row[0]
        assert stage == "COMPLETE"


def main() -> None:
    started = time.monotonic()
    run_id = uuid4().hex[:10]
    for serial, boundary in enumerate(BOUNDARIES):
        run_boundary(boundary, serial, run_id)
    print(
        f"continuation coverage=100%; orphan time=0; boundaries={len(BOUNDARIES)}; elapsed={time.monotonic() - started:.2f}s"
    )


if __name__ == "__main__":
    main()
