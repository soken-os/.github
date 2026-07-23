"""Restartable controller process used by the Phase-2 kill harness."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from ..contracts import WorkerKind
from .controller import OneTaskController


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-item", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--bridge-outbox", required=True)
    parser.add_argument("--worker", choices=("SCRIPT", "CLAUDE_CODE"), required=True)
    args = parser.parse_args()
    controller = OneTaskController(
        Path(args.workspace), WorkerKind(args.worker), Path(args.bridge_outbox)
    )
    deadline = time.monotonic() + float(
        os.environ.get("CEC_PHASE2_RUNNER_TIMEOUT", "240")
    )
    while time.monotonic() < deadline:
        outcome = controller.reconcile_once(args.work_item)
        if outcome == "RESULT_CLAIMED":
            time.sleep(float(os.environ.get("CEC_PHASE2_PAUSE_AFTER_CLAIM", "0")))
        if outcome == "COMPLETE":
            return 0
        time.sleep(0.2)
    raise TimeoutError(f"Phase-2 controller timed out for {args.work_item}")


if __name__ == "__main__":
    raise SystemExit(main())
