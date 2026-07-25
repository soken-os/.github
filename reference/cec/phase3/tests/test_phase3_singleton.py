"""Per-program singleton ownership is mechanical (finding M4).

"One controller process per program" was a comment. Two controllers for the same
program can both observe a READY row and launch a worker before either loses the
CAS -- by then the duplicate build is already running. These prove the constraint
is now enforced, not merely described.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from reference.cec.phase3.packet import REPO_ROOT
from reference.cec.phase3.singleton import (
    ProgramAlreadyServed,
    program_singleton,
)


def test_second_holder_of_the_same_program_is_refused(tmp_path):
    with (  # noqa: SIM117
        program_singleton("carlson-roofing", lock_dir=tmp_path),
        pytest.raises(ProgramAlreadyServed, match="carlson-roofing"),
    ):
        with program_singleton("carlson-roofing", lock_dir=tmp_path):
            pass


def test_different_programs_run_concurrently(tmp_path):
    # Scoping must not serialize unrelated programs -- the whole point of Option
    # B is that roofing and soken build at the same time.
    with (
        program_singleton("carlson-roofing", lock_dir=tmp_path),
        program_singleton("soken", lock_dir=tmp_path),
        program_singleton("field-capture", lock_dir=tmp_path),
    ):
        pass


def test_lock_is_released_for_the_next_owner(tmp_path):
    with program_singleton("soken", lock_dir=tmp_path):
        pass
    # A controller that exits cleanly must not strand its own program.
    with program_singleton("soken", lock_dir=tmp_path):
        pass


def test_sanitized_names_that_would_collide_get_distinct_locks(tmp_path):
    """The exact collision pair: both sanitize to `a_b`.

    Without a digest suffix these two distinct programs share one lock file, so
    starting the second controller is refused — an availability/orphan bug where
    a real program silently never gets served.
    """
    with (
        program_singleton("a/b", lock_dir=tmp_path),
        program_singleton("a?b", lock_dir=tmp_path),
    ):
        pass
    locks = sorted(p.name for p in tmp_path.glob("controller-*.lock"))
    assert len(locks) == 2, f"distinct programs shared a lock file: {locks}"


def test_lock_is_held_against_a_separate_os_process(tmp_path):
    """The real shape: two OS processes, not two context managers in one.

    fcntl locks are per-open-file-description, so an in-process test alone could
    pass while cross-process ownership silently failed.
    """
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(REPO_ROOT)!r})
        from pathlib import Path
        from reference.cec.phase3.singleton import (
            ProgramAlreadyServed, program_singleton,
        )
        try:
            with program_singleton("soken", lock_dir=Path({str(tmp_path)!r})):
                print("ACQUIRED")
        except ProgramAlreadyServed:
            print("REFUSED")
        """
    )
    with program_singleton("soken", lock_dir=tmp_path):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            check=False,
        )
    assert "REFUSED" in completed.stdout, (
        f"a second OS process acquired the same program lock: "
        f"{completed.stdout!r} {completed.stderr!r}"
    )
