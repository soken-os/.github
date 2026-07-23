"""Hand-authored bootstrap packet for the first self-dispatched CEC task."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

PHASE3_DIR = Path(__file__).resolve().parent
REPO_ROOT = PHASE3_DIR.parents[2]
RUNTIME_DIR = PHASE3_DIR / "runtime"

ALLOWED_PATHS = [
    "reference/cec/adapters.py",
    "reference/cec/phase2/tests/test_adapter_identity.py",
]

FORBIDDEN_PATHS = [
    "docs/",
    "reference/cec/migrations/",
    "reference/cec/phase0/gate.py",
    "reference/cec/phase1/gate.py",
    "reference/cec/phase1/registry.py",
    "reference/cec/contracts.py",
]

BOOTSTRAP_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "summary",
        "files_changed",
        "test_output_sha256",
        "diff_sha256",
        "evidence",
        "next",
    ],
    "properties": {
        "status": {"enum": ["done", "needs_input", "failed"]},
        "summary": {"type": "string"},
        "files_changed": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "test_output_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "diff_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "evidence": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "path", "sha256"],
                "properties": {
                    "kind": {"const": "file"},
                    "path": {"type": "string"},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "role": {"enum": ["test_output", "diff"]},
                    "contains": {"type": "string"},
                },
            },
        },
        "next": {"type": "array", "items": {"type": "object"}},
    },
}


PACKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "task_class",
        "objective",
        "starting_ref",
        "allowed_paths",
        "forbidden_paths",
        "new_files_allowed",
        "artifact_path",
        "diff_artifact_path",
        "estimated_duration_seconds",
        "priority_class",
        "authority_class",
        "acceptance",
    ],
    "properties": {
        "task_class": {"const": "CIRCUIT_BUILD"},
        "objective": {"type": "string", "minLength": 1},
        "starting_ref": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "allowed_paths": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "uniqueItems": True,
        },
        "forbidden_paths": {"type": "array", "items": {"type": "string"}},
        "new_files_allowed": {"const": False},
        "artifact_path": {"type": "string"},
        "diff_artifact_path": {"type": "string"},
        "estimated_duration_seconds": {"const": 600},
        "priority_class": {"const": 60},
        "authority_class": {"const": "ROUTINE"},
        "acceptance": {"type": "object"},
    },
}


def current_ref(repo_root: Path = REPO_ROOT) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()


def bootstrap_packet(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    runtime = PHASE3_DIR / "runtime"
    artifact = runtime / "bootstrap-test-output.txt"
    diff_artifact = runtime / "bootstrap-change.diff"
    packet = {
        "task_class": "CIRCUIT_BUILD",
        "objective": (
            "Fix finding D1 in reference/cec/adapters.py: collect_result must stamp "
            "WorkerResultClaim.lease_token and lease_epoch from the launch-time sidecar "
            "record (<command_id>.process.json), not from the current WorkerCommand. "
            "Read docs/executor-dispatch-decision.md Appendix F section 'D1'. Add two "
            "tests in reference/cec/phase2/tests/test_adapter_identity.py: (1) a claim "
            "carries the epoch recorded at launch even when the passed command has a "
            "newer epoch; (2) a claim from a sidecar with a mismatched command_id yields "
            "no claim. Run the full non-Postgres suite and write complete output to the "
            "artifact path. Write the unified diff to the diff artifact. Do not modify "
            "any file outside allowed_paths. Return files_changed, test_output_sha256, "
            "diff_sha256, and file evidence for both artifacts."
        ),
        "starting_ref": current_ref(repo_root),
        "allowed_paths": ALLOWED_PATHS,
        "forbidden_paths": FORBIDDEN_PATHS,
        "new_files_allowed": False,
        "artifact_path": str(artifact.resolve()),
        "diff_artifact_path": str(diff_artifact.resolve()),
        "estimated_duration_seconds": 600,
        "priority_class": 60,
        "authority_class": "ROUTINE",
        "acceptance": {
            "tests": "all non-Postgres suites pass, including the two D1 tests",
            "diff": "touches only allowed_paths and contains no new files",
            "evidence": [
                "file:artifact_path (passing test output)",
                "file:diff_artifact_path (unified diff)",
            ],
        },
    }
    Draft202012Validator(PACKET_SCHEMA).validate(packet)
    return packet


def write_seed_packet(path: Path, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    packet = bootstrap_packet(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return packet

