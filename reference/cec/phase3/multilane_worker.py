"""Deterministic lane worker: produces real evidence, or a chosen failure mode.

Used by the multi-lane concurrency proof to drive many lanes at once without
invoking a model. It does exactly what a real worker does — edits an allowed
tracked file inside its own worktree, writes passing test output, and writes the
unified diff with the same command the controller uses to regenerate it — so the
claim it returns is checked by the real `verify_code_change_claim`, not a stub.

A lane asked for `needs_input` returns a genuine non-completion claim, which is
how the harness proves one lane failing does not disturb the others.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edit-file", required=True, help="repo-relative tracked file")
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--diff-artifact", required=True)
    parser.add_argument("--starting-ref", required=True)
    parser.add_argument("--mode", default="done", choices=["done", "needs_input"])
    parser.add_argument("--marker", default="lane")
    args = parser.parse_args()

    if args.mode == "needs_input":
        # A legitimate worker outcome, not an evidence problem: the controller
        # must route it to the human recovery lane without touching other lanes.
        print(
            json.dumps(
                {
                    "status": "needs_input",
                    "summary": "lane deliberately blocked for the isolation proof",
                    "evidence": [],
                    "next": [],
                }
            )
        )
        return 0

    # 1. A real tracked edit, inside this lane's own worktree (the adapter runs
    #    the worker with cwd set to that worktree).
    target = Path(args.edit_file)
    target.write_text(
        target.read_text(encoding="utf-8") + f"\n<!-- multilane {args.marker} -->\n",
        encoding="utf-8",
    )

    # 2. Passing test output (the verifier requires "passed", rejects " failed").
    artifact = Path(args.artifact)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("1 passed in 0.01s\n", encoding="utf-8")

    # 3. The diff, byte-identical to what the controller regenerates.
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            args.starting_ref,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    diff_artifact = Path(args.diff_artifact)
    diff_artifact.parent.mkdir(parents=True, exist_ok=True)
    diff_artifact.write_text(diff, encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "done",
                "summary": f"lane {args.marker} produced verifiable evidence",
                "files_changed": [args.edit_file],
                "test_output_sha256": _sha256(artifact),
                "diff_sha256": _sha256(diff_artifact),
                "evidence": [
                    {
                        "kind": "file",
                        "role": "test_output",
                        "path": str(artifact),
                        "sha256": _sha256(artifact),
                    },
                    {
                        "kind": "file",
                        "role": "diff",
                        "path": str(diff_artifact),
                        "sha256": _sha256(diff_artifact),
                    },
                ],
                "next": [],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
