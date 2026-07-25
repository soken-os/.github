"""Small always-on Phase-3 bootstrap service loop.

One process serves exactly one program (Option B). `--program` selects it, and
the process holds a per-program singleton lock for its whole life, so a second
controller for the same program cannot start and duplicate-launch workers.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ..contracts import WorkerKind
from .controller import BootstrapController, run_scan_once
from .packet import PROGRAM_CEC
from .singleton import DEFAULT_LOCK_DIR, ProgramAlreadyServed, program_singleton


def build_controller(args: argparse.Namespace) -> BootstrapController:
    """Construct the controller this process will run (M1: program-aware)."""

    return BootstrapController(
        repo_root=Path(args.repo_root),
        bridge_outbox=Path(args.bridge_outbox) if args.bridge_outbox else None,
        worker_kind=WorkerKind(args.worker),
        program=args.program,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--repo-root", default=str(Path.cwd()))
    result.add_argument("--bridge-outbox")
    result.add_argument(
        "--worker", choices=("CLAUDE_CODE", "SCRIPT"), default="CLAUDE_CODE"
    )
    result.add_argument("--interval-seconds", type=float, default=15.0)
    result.add_argument("--once", action="store_true")
    result.add_argument(
        "--program",
        default=PROGRAM_CEC,
        help=(
            "the ONE program this process serves; it claims only this program's "
            "work items and refuses every other program's rows. Defaults to "
            f"{PROGRAM_CEC} for backward compatibility -- pass it explicitly for "
            "any other program (e.g. --program carlson-roofing)."
        ),
    )
    result.add_argument(
        "--lock-dir",
        default=str(DEFAULT_LOCK_DIR),
        help="directory holding the per-program singleton lock",
    )
    result.add_argument(
        "--no-singleton-lock",
        action="store_true",
        help="skip the per-program singleton lock (tests/diagnostics only)",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    controller = build_controller(args)

    def loop() -> int:
        while True:
            run_scan_once(controller)
            if args.once:
                return 0
            time.sleep(args.interval_seconds)

    if args.no_singleton_lock:
        return loop()
    try:
        with program_singleton(args.program, lock_dir=Path(args.lock_dir)):
            return loop()
    except ProgramAlreadyServed as exc:
        # Fail fast and loudly: a silent duplicate controller is exactly the
        # failure this lock exists to prevent, so refusing to start is correct.
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
