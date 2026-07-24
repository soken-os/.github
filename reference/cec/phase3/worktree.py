"""Controller-owned worktree custody for editing workers."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeRecord:
    repo_root: Path
    worktree_path: Path
    starting_ref: str
    branch_name: str
    cleanup_policy: str = "after_notification_ack"


def create_worktree(
    *,
    repo_root: Path,
    worktree_root: Path,
    work_item_id: str,
    starting_ref: str,
) -> WorktreeRecord:
    branch_name = f"cec/bootstrap/{work_item_id}"
    worktree_path = (worktree_root / work_item_id).resolve()
    if worktree_path.exists():
        return WorktreeRecord(
            repo_root.resolve(), worktree_path, starting_ref, branch_name
        )
    worktree_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "worktree",
            "add",
            "--detach",
            str(worktree_path),
            starting_ref,
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return WorktreeRecord(repo_root.resolve(), worktree_path, starting_ref, branch_name)


def remove_worktree(record: WorktreeRecord) -> None:
    if not record.worktree_path.exists():
        return
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(record.worktree_path)],
        cwd=record.repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if record.worktree_path.exists():
        shutil.rmtree(record.worktree_path)


def write_unified_diff(record: WorktreeRecord, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # Intent-to-add so untracked NEW files appear in the diff (a plain
    # `git diff <ref>` omits them, while evidence.files_changed counts them via
    # `ls-files --others` — leaving the artifact incomplete). `-N` writes only an
    # index entry, never a blob, so it needs just the worktree gitdir (granted to
    # the sandboxed worker as GITDIR_ROOT, finding G3). The worker's objective
    # runs the SAME `git add -AN` before its own diff, so its diff_sha256 matches
    # this regeneration byte-for-byte. Gitignored runtime artifacts are excluded
    # by -A honoring .gitignore.
    subprocess.run(
        ["git", "add", "-AN"],
        cwd=record.worktree_path,
        check=True,
        capture_output=True,
        text=True,
    )
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            record.starting_ref,
        ],
        cwd=record.worktree_path,
        check=True,
        capture_output=True,
        text=True,
    )
    target.write_text(completed.stdout, encoding="utf-8")

