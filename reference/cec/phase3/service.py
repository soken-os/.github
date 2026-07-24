"""Small always-on Phase-3 bootstrap service loop."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ..contracts import WorkerKind
from .controller import BootstrapController, run_scan_once


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path.cwd()))
    parser.add_argument("--bridge-outbox")
    parser.add_argument("--worker", choices=("CLAUDE_CODE", "SCRIPT"), default="CLAUDE_CODE")
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    controller = BootstrapController(
        repo_root=Path(args.repo_root),
        bridge_outbox=Path(args.bridge_outbox) if args.bridge_outbox else None,
        worker_kind=WorkerKind(args.worker),
    )
    while True:
        run_scan_once(controller)
        if args.once:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

