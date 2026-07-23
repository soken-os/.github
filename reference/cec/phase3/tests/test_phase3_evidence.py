import hashlib
import subprocess

import pytest

from reference.cec.phase3.evidence import CodeEvidenceRejected, verify_code_change_claim


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _claim(test_output, diff_artifact, changed):
    test_digest = hashlib.sha256(test_output.read_bytes()).hexdigest()
    diff_digest = hashlib.sha256(diff_artifact.read_bytes()).hexdigest()
    return {
        "status": "RESULT_CLAIMED",
        "files_changed": changed,
        "test_output_sha256": test_digest,
        "diff_sha256": diff_digest,
        "evidence": [
            {"kind": "file", "role": "test_output", "path": str(test_output), "sha256": test_digest},
            {"kind": "file", "role": "diff", "path": str(diff_artifact), "sha256": diff_digest},
        ],
    }


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    target = repo / "allowed.txt"
    target.write_text("before\n", encoding="utf-8")
    _git(repo, "add", "allowed.txt")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, base, target


def test_verifies_code_change_dual_artifacts(tmp_path):
    repo, base, target = _repo(tmp_path)
    target.write_text("after\n", encoding="utf-8")
    test_output = repo / "test-output.txt"
    test_output.write_text("2 passed in 0.01s\n", encoding="utf-8")
    diff_artifact = repo / "change.diff"
    diff_artifact.write_text(_git(repo, "diff", base).stdout, encoding="utf-8")
    evidence = verify_code_change_claim(
        _claim(test_output, diff_artifact, ["allowed.txt"]),
        worktree=repo,
        starting_ref=base,
        packet={"allowed_paths": ["allowed.txt"], "forbidden_paths": [], "new_files_allowed": False},
    )
    assert evidence["completion_verified"] is True
    assert evidence["files_changed"] == ["allowed.txt"]


def test_rejects_diff_outside_allow_list(tmp_path):
    repo, base, target = _repo(tmp_path)
    target.write_text("after\n", encoding="utf-8")
    test_output = repo / "test-output.txt"
    test_output.write_text("2 passed\n", encoding="utf-8")
    diff_artifact = repo / "change.diff"
    diff_artifact.write_text(_git(repo, "diff", base).stdout, encoding="utf-8")
    with pytest.raises(CodeEvidenceRejected, match="allowed_paths"):
        verify_code_change_claim(
            _claim(test_output, diff_artifact, ["allowed.txt"]),
            worktree=repo,
            starting_ref=base,
            packet={"allowed_paths": ["other.txt"], "forbidden_paths": [], "new_files_allowed": False},
        )


def test_rejects_new_files_when_forbidden(tmp_path):
    repo, base, _target = _repo(tmp_path)
    new_file = repo / "new.txt"
    new_file.write_text("new\n", encoding="utf-8")
    test_output = repo / "test-output.txt"
    test_output.write_text("2 passed\n", encoding="utf-8")
    diff_artifact = repo / "change.diff"
    diff_artifact.write_text("diff --git a/new.txt b/new.txt\n", encoding="utf-8")
    with pytest.raises(CodeEvidenceRejected, match="new files"):
        verify_code_change_claim(
            _claim(test_output, diff_artifact, ["new.txt"]),
            worktree=repo,
            starting_ref=base,
            packet={"allowed_paths": ["new.txt"], "forbidden_paths": [], "new_files_allowed": False},
        )

