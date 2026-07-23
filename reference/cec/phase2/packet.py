"""Load, validate, and materialize the single hand-authored work packet."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

PHASE2_DIR = Path(__file__).resolve().parent
PACKET_PATH = PHASE2_DIR / "fixtures" / "one_task_packet.json"
SCHEMA_PATH = PHASE2_DIR / "fixtures" / "work_packet.schema.json"


def load_packet(workspace: Path, worker_kind: str) -> dict[str, Any]:
    source = json.loads(PACKET_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(source)
    artifact = (PHASE2_DIR / source["artifact_relative_path"]).resolve()
    artifact.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable if part == "python" else part for part in source["test_command"]
    ]
    packet = {**source, "artifact_path": str(artifact)}
    if worker_kind == "SCRIPT":
        packet["argv"] = [
            sys.executable,
            "-m",
            "reference.cec.phase2.script_worker",
            "--artifact",
            str(artifact),
            "--",
            *command,
        ]
    elif worker_kind == "CLAUDE_CODE":
        shell_command = " ".join(shlex.quote(part) for part in command)
        packet["objective"] = (
            f"{source['objective']} First run `sleep 6`, then run `{shell_command}` from "
            f"`{workspace}` and write complete stdout/stderr to `{artifact}`. Compute the "
            "artifact SHA-256. Return status=done only if tests pass. Evidence must contain "
            f"one object with kind=file, path={artifact}, sha256, and contains='passed'."
        )
    else:
        raise ValueError(f"unsupported Phase-2 worker kind: {worker_kind}")
    return packet


RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "evidence", "next"],
    "properties": {
        "status": {"enum": ["done", "needs_input", "failed"]},
        "summary": {"type": "string"},
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "path", "sha256", "contains"],
                "properties": {
                    "kind": {"const": "file"},
                    "path": {"type": "string"},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "contains": {"const": "passed"},
                },
            },
        },
        "next": {"type": "array", "items": {"type": "object"}},
    },
}
