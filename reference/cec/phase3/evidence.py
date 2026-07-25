"""Mechanical verification for code-change evidence."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class CodeEvidenceRejected(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_under(path: Path, root: Path) -> Path:
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise CodeEvidenceRejected(f"artifact escaped workspace: {path}")
    return resolved


def _changed_paths(
    worktree: Path, starting_ref: str, *, excluded_paths: set[str]
) -> tuple[set[str], set[str]]:
    completed = subprocess.run(
        ["git", "diff", "--name-status", "-z", starting_ref],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    fields = completed.stdout.decode("utf-8").split("\0")
    changed: set[str] = set()
    new_files: set[str] = set()
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        code = status[0]
        if code in {"R", "C"}:
            old_path = fields[i + 1]
            new_path = fields[i + 2]
            changed.update({old_path, new_path} - excluded_paths)
            i += 3
        else:
            path = fields[i + 1]
            if path not in excluded_paths:
                changed.add(path)
            if code == "A" and path not in excluded_paths:
                new_files.add(path)
            i += 2
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=worktree,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    for path in [p for p in untracked.split("\0") if p]:
        if path not in excluded_paths:
            changed.add(path)
            new_files.add(path)
    return changed, new_files


def verify_code_change_claim(
    claim: Mapping[str, Any],
    *,
    worktree: Path,
    starting_ref: str,
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    if claim.get("status") != "RESULT_CLAIMED":
        raise CodeEvidenceRejected("worker did not claim a completed result")
    files_changed = claim.get("files_changed")
    if not isinstance(files_changed, list) or not all(
        isinstance(p, str) for p in files_changed
    ):
        raise CodeEvidenceRejected("files_changed is required")
    evidence = claim.get("evidence")
    if not isinstance(evidence, list):
        raise CodeEvidenceRejected("file evidence is required")
    file_items = [
        item
        for item in evidence
        if isinstance(item, Mapping) and item.get("kind") == "file"
    ]
    by_role = {item.get("role"): item for item in file_items}
    test_item = by_role.get("test_output")
    diff_item = by_role.get("diff")
    if not isinstance(test_item, Mapping) or not isinstance(diff_item, Mapping):
        raise CodeEvidenceRejected("test_output and diff evidence are required")
    # D2 / G8-3: each new (untracked) file the task creates must carry its own
    # first-class byte hash. The diff artifact covers only tracked changes -- an
    # intent-to-add would write to the shared object store, which the worker
    # sandbox denies -- so without this, a new file's bytes are outside every
    # verified hash and COMPLETE could be granted over unverified content.
    new_file_items = [item for item in file_items if item.get("role") == "new_file"]

    test_path = _resolve_under(Path(str(test_item.get("path", ""))), worktree)
    diff_path = _resolve_under(Path(str(diff_item.get("path", ""))), worktree)
    excluded_paths = {
        str(path.relative_to(worktree.resolve()))
        for path in (test_path, diff_path)
        if path.is_relative_to(worktree.resolve())
    }
    changed, new_files = _changed_paths(
        worktree, starting_ref, excluded_paths=excluded_paths
    )
    if set(files_changed) != changed:
        raise CodeEvidenceRejected("files_changed does not match git diff")
    allowed = set(packet.get("allowed_paths", []))
    if not changed:
        raise CodeEvidenceRejected("empty diff is not evidence of a code change")
    if not changed <= allowed:
        raise CodeEvidenceRejected("diff touches paths outside allowed_paths")
    if new_files and not packet.get("new_files_allowed", False):
        raise CodeEvidenceRejected("new files are forbidden for this packet")
    # Bind every new file's bytes into the verified evidence: the worker must
    # declare a hash for each, the declared set must match the enumerated new
    # files exactly (no missing, no extra), and each declared hash must match the
    # bytes actually present in the worktree (controller-authoritative).
    declared_new: dict[str, str] = {}
    for item in new_file_items:
        item_path = _resolve_under(Path(str(item.get("path", ""))), worktree)
        rel = str(item_path.relative_to(worktree.resolve()))
        if rel in declared_new:
            raise CodeEvidenceRejected(f"duplicate new_file evidence for {rel}")
        declared_new[rel] = str(item.get("sha256", ""))
    if set(declared_new) != new_files:
        raise CodeEvidenceRejected(
            "new_file evidence does not match the set of new files in the diff"
        )
    for rel, declared_sha in declared_new.items():
        actual_sha = _sha256(worktree / rel)
        if actual_sha != declared_sha:
            raise CodeEvidenceRejected(f"new file SHA-256 does not match: {rel}")
    forbidden = tuple(str(p) for p in packet.get("forbidden_paths", []))
    for path in changed:
        if path in forbidden or any(
            prefix.endswith("/") and path.startswith(prefix) for prefix in forbidden
        ):
            raise CodeEvidenceRejected("diff touches forbidden_paths")
    test_digest = _sha256(test_path)
    diff_digest = _sha256(diff_path)
    if test_digest != test_item.get("sha256") or test_digest != claim.get(
        "test_output_sha256"
    ):
        raise CodeEvidenceRejected("test output SHA-256 does not match")
    if diff_digest != diff_item.get("sha256") or diff_digest != claim.get(
        "diff_sha256"
    ):
        raise CodeEvidenceRejected("diff SHA-256 does not match")
    test_text = test_path.read_text(encoding="utf-8")
    if "passed" not in test_text or " failed" in test_text.lower():
        raise CodeEvidenceRejected("test output does not prove passing tests")
    if not diff_path.read_text(encoding="utf-8").strip():
        raise CodeEvidenceRejected("diff artifact is empty")
    return {
        "completion_verified": True,
        "starting_ref": starting_ref,
        "files_changed": sorted(changed),
        "test_output_artifact": str(test_path),
        "test_output_sha256": test_digest,
        "diff_artifact": str(diff_path),
        "diff_sha256": diff_digest,
        "new_files": {rel: declared_new[rel] for rel in sorted(declared_new)},
    }
