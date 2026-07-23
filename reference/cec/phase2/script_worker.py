"""Deterministic dry-run worker implementing the same typed result contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    time.sleep(4)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    artifact = Path(args.artifact)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "status": "done" if completed.returncode == 0 else "failed",
                "summary": "read-only CEC tests completed",
                "evidence": [
                    {
                        "kind": "file",
                        "path": str(artifact.resolve()),
                        "sha256": digest,
                        "contains": "passed",
                    }
                ],
                "next": [],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
