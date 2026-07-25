"""Per-program singleton ownership — mechanical, not a convention (finding M4).

Option B is "one controller process per program". Until now that was a comment:
nothing stopped two controllers for the same program from running at once, and
two of them can both observe a READY row and launch a worker before either loses
the CAS. The losing controller's worker is already running by then — a duplicate
build in the same worktree, which is the exact class of double-execution custody
exists to prevent.

An OS advisory lock keyed on the program makes the constraint enforceable: the
second process cannot start, so it never reaches dispatch. The lock is advisory
and process-scoped, which is what we want — it dies with the process, so a
crashed controller does not strand its own program forever.

This is defence in depth, not a replacement for CAS/lease fencing: the registry
remains authoritative for who owns an item. The lock removes the race before it
can waste a worker launch.
"""

from __future__ import annotations

import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_LOCK_DIR = Path.home() / ".cec" / "locks"


class ProgramAlreadyServed(RuntimeError):
    """Another live process already serves this program."""


def _lock_path(program: str, lock_dir: Path) -> Path:
    # Programs name files; keep them filesystem-safe without collapsing distinct
    # names onto one lock (which would let two real programs block each other).
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", program)
    return lock_dir / f"controller-{safe}.lock"


@contextmanager
def program_singleton(
    program: str, *, lock_dir: Path = DEFAULT_LOCK_DIR
) -> Iterator[Path]:
    """Hold exclusive ownership of `program` for the duration of the block.

    Raises ProgramAlreadyServed immediately (never blocks) if another process
    already holds it — a controller that cannot own its program must fail fast
    and loudly rather than run as a silent duplicate.
    """

    lock_dir.mkdir(parents=True, exist_ok=True)
    path = _lock_path(program, lock_dir)
    handle = path.open("a+b")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProgramAlreadyServed(
                f"another controller process already serves program {program!r} "
                f"({path}); one controller per program is required"
            ) from exc
        # Record the owner for diagnosis; the lock itself is the enforcement.
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n".encode())
        handle.flush()
        os.fsync(handle.fileno())
        yield path
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
