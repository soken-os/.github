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
        "allowed_tools": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "uniqueItems": True,
        },
        "permission_mode": {"const": "bypassPermissions"},
        "new_files_allowed": {"type": "boolean"},
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
            "artifact path. Write the unified diff to the diff artifact using exactly: "
            "git add -AN && git diff --no-ext-diff --src-prefix=a/ --dst-prefix=b/ "
            "<starting_ref> (run from the worktree root; the add -AN is intent-to-add "
            "so new files appear in the diff) — the controller regenerates the diff with "
            "this exact command and your diff_sha256 must match its bytes. Do not modify "
            "any file outside allowed_paths. Return files_changed, test_output_sha256, "
            "diff_sha256, and file evidence for both artifacts."
        ),
        "starting_ref": current_ref(repo_root),
        "allowed_paths": ALLOWED_PATHS,
        "forbidden_paths": FORBIDDEN_PATHS,
        "allowed_tools": ["Read", "Edit", "Write", "Bash"],
        "permission_mode": "bypassPermissions",
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


# --- P4: worktree-scoped worker Bash (queue order locked in PR #3) -----------

P4_ALLOWED_PATHS = [
    "reference/cec/adapters.py",
    "reference/cec/phase3/sandbox/worker-bash.md",
    "reference/cec/phase3/sandbox/worker.sb",
    "reference/cec/phase3/tests/test_phase3_sandbox.py",
]


def p4_packet(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Packet for the machine's second self-built change: confining its worker.

    Per the PR #3 adjudication, V1 scope is the smallest enforceable control
    that retires blanket permissioning for the worker's shell — not a general
    sandbox. Mechanism choice (allowed-tool patterns vs macOS sandbox-exec) is
    the worker's, because enforceability can only be proven on the Mac.
    """
    runtime = PHASE3_DIR / "runtime"
    artifact = runtime / "p4-test-output.txt"
    diff_artifact = runtime / "p4-change.diff"
    packet = {
        "task_class": "CIRCUIT_BUILD",
        "objective": (
            "Confine the CEC worker's Bash to its task worktree, retiring the "
            "blanket bypassPermissions caveat (E4; PR #3 adjudication P4). Modify "
            "ClaudeCodeAdapter._argv in reference/cec/adapters.py so the launched "
            "worker's shell commands are constrained to worktree-rooted execution "
            "using the smallest enforceable control: allowed-tool patterns if they "
            "enforceably restrict Bash, otherwise a macOS sandbox-exec profile "
            "written to reference/cec/phase3/sandbox/worker.sb. Document the chosen "
            "mechanism and its known limits in "
            "reference/cec/phase3/sandbox/worker-bash.md. Add tests in "
            "reference/cec/phase3/tests/test_phase3_sandbox.py proving both "
            "directions: (1) a worktree-rooted command run through the confinement "
            "succeeds; (2) a write attempt outside the worktree fails. Do not "
            "attempt a general security sandbox; do not change contracts.py, the "
            "registry, or any gate. Run the full non-Postgres suite and write "
            "complete output to the artifact path. Write the unified diff to the "
            "diff artifact using exactly: git add -AN && git diff --no-ext-diff "
            "--src-prefix=a/ --dst-prefix=b/ <starting_ref> (from the worktree root; "
            "the add -AN is intent-to-add so new files appear in the diff) — the "
            "controller regenerates the diff with this exact command and your "
            "diff_sha256 must match its bytes. Do not modify any file outside "
            "allowed_paths. Return files_changed, test_output_sha256, diff_sha256, "
            "and file evidence for both artifacts."
        ),
        "starting_ref": current_ref(repo_root),
        "allowed_paths": P4_ALLOWED_PATHS,
        "forbidden_paths": FORBIDDEN_PATHS,
        "allowed_tools": ["Read", "Edit", "Write", "Bash"],
        "permission_mode": "bypassPermissions",  # the last packet that carries this
        "new_files_allowed": True,
        "artifact_path": str(artifact.resolve()),
        "diff_artifact_path": str(diff_artifact.resolve()),
        "estimated_duration_seconds": 600,
        "priority_class": 60,
        "authority_class": "ROUTINE",
        "acceptance": {
            "tests": (
                "all non-Postgres suites pass, including both confinement tests "
                "(worktree command succeeds; out-of-worktree write fails)"
            ),
            "diff": "touches only allowed_paths; new files only at the named paths",
            "evidence": [
                "file:artifact_path (passing test output)",
                "file:diff_artifact_path (unified diff)",
            ],
        },
    }
    Draft202012Validator(PACKET_SCHEMA).validate(packet)
    return packet


# --- P3: deterministic notification delivery tick (F1) -----------------------

P3_ALLOWED_PATHS = [
    "reference/cec/phase3/service.py",
    "reference/cec/phase3/controller.py",
    "reference/cec/phase3/conftest.py",
    "reference/cec/phase3/tests/test_phase3_delivery.py",
]


def p3_packet(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Packet for the F1 fix: a stage-independent notification delivery tick.

    The defect: pending notifications for terminal (COMPLETE) rows are only
    delivered when that row is reconciled, but the scan skips terminal rows, so
    after a service restart a COMPLETE item's notification stays PENDING until a
    manual deliver_pending() call. The fix runs delivery every service cycle,
    independent of work-item stage, under the single controller — NOT a second
    scheduler (PR #3 adjudication: two lifecycle owners create race classes).
    """
    runtime = PHASE3_DIR / "runtime"
    artifact = runtime / "p3-test-output.txt"
    diff_artifact = runtime / "p3-change.diff"
    packet = {
        "task_class": "CIRCUIT_BUILD",
        "objective": (
            "Fix finding F1 (PR #3 queue P3): pending notification-outbox rows "
            "must be delivered by a deterministic tick that runs every service "
            "cycle, independent of work-item stage, so a COMPLETE row's PENDING "
            "notification is delivered after a fresh service start without a "
            "manual call. In reference/cec/phase3/service.py, call the existing "
            "reference/cec/phase2/notifications.deliver_pending(bridge_outbox) "
            "once per scan cycle in run_scan_once (or the service loop), using "
            "the controller's bridge_outbox, under the same single controller — "
            "do NOT add a second scheduler or launchd timer. deliver_pending is "
            "already idempotent (it only touches PENDING rows and writes atomically); "
            "delivery failures must not block reconciliation of work items. Also "
            "add reference/cec/phase3/conftest.py setting norecursedirs to exclude "
            "'worktrees' so pytest cannot double-collect test modules copied into "
            "runtime worktrees. Add tests in "
            "reference/cec/phase3/tests/test_phase3_delivery.py proving: a COMPLETE "
            "work item with a PENDING notification and a freshly started service "
            "(no reconcilable non-terminal rows) results in the notification "
            "DELIVERED to the bridge outbox without the work item being touched or "
            "reprocessed. Run the full non-Postgres suite and write complete output "
            "to the artifact path. Write the unified diff to the diff artifact using "
            "exactly: git add -AN && git diff --no-ext-diff --src-prefix=a/ "
            "--dst-prefix=b/ <starting_ref> (from the worktree root; the add -AN is "
            "intent-to-add so new files appear in the diff) — the controller regenerates "
            "the diff with this exact command and your diff_sha256 must match its "
            "bytes. Do not modify any file outside allowed_paths. Return "
            "files_changed, test_output_sha256, diff_sha256, and file evidence for "
            "both artifacts."
        ),
        "starting_ref": current_ref(repo_root),
        "allowed_paths": P3_ALLOWED_PATHS,
        "forbidden_paths": FORBIDDEN_PATHS,
        "allowed_tools": ["Read", "Edit", "Write", "Bash"],
        "permission_mode": "bypassPermissions",
        "new_files_allowed": True,
        "artifact_path": str(artifact.resolve()),
        "diff_artifact_path": str(diff_artifact.resolve()),
        "estimated_duration_seconds": 600,
        "priority_class": 60,
        "authority_class": "ROUTINE",
        "acceptance": {
            "tests": (
                "all non-Postgres suites pass, including the terminal-row "
                "delivery-after-restart test"
            ),
            "diff": "touches only allowed_paths; new files only at the named paths",
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
