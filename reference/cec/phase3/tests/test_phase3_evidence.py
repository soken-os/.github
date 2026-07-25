import hashlib
import subprocess

import pytest

from reference.cec.phase3.evidence import CodeEvidenceRejected, verify_code_change_claim


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _claim(test_output, diff_artifact, changed):
    test_digest = hashlib.sha256(test_output.read_bytes()).hexdigest()
    diff_digest = hashlib.sha256(diff_artifact.read_bytes()).hexdigest()
    return {
        "status": "RESULT_CLAIMED",
        "files_changed": changed,
        "test_output_sha256": test_digest,
        "diff_sha256": diff_digest,
        "evidence": [
            {
                "kind": "file",
                "role": "test_output",
                "path": str(test_output),
                "sha256": test_digest,
            },
            {
                "kind": "file",
                "role": "diff",
                "path": str(diff_artifact),
                "sha256": diff_digest,
            },
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
        packet={
            "allowed_paths": ["allowed.txt"],
            "forbidden_paths": [],
            "new_files_allowed": False,
        },
    )
    assert evidence["completion_verified"] is True
    assert evidence["files_changed"] == ["allowed.txt"]


def test_verifies_repo_relative_evidence_artifact_paths(tmp_path):
    repo, base, target = _repo(tmp_path)
    target.write_text("after\n", encoding="utf-8")
    test_output = repo / "reference" / "cec" / "phase3" / "runtime" / "test.txt"
    diff_artifact = repo / "reference" / "cec" / "phase3" / "runtime" / "change.diff"
    test_output.parent.mkdir(parents=True)
    test_output.write_text("2 passed in 0.01s\n", encoding="utf-8")
    diff_artifact.write_text(_git(repo, "diff", base).stdout, encoding="utf-8")
    claim = _claim(test_output, diff_artifact, ["allowed.txt"])
    claim["evidence"][0]["path"] = "reference/cec/phase3/runtime/test.txt"
    claim["evidence"][1]["path"] = "reference/cec/phase3/runtime/change.diff"

    evidence = verify_code_change_claim(
        claim,
        worktree=repo,
        starting_ref=base,
        packet={
            "allowed_paths": ["allowed.txt"],
            "forbidden_paths": [],
            "new_files_allowed": False,
        },
    )

    assert evidence["completion_verified"] is True
    assert evidence["test_output_artifact"].endswith(
        "reference/cec/phase3/runtime/test.txt"
    )


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
            packet={
                "allowed_paths": ["other.txt"],
                "forbidden_paths": [],
                "new_files_allowed": False,
            },
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
            packet={
                "allowed_paths": ["new.txt"],
                "forbidden_paths": [],
                "new_files_allowed": False,
            },
        )


# --- D2 / G8-3: new files' bytes must be covered by first-class evidence ------


def _new_file_item(path, sha256):
    return {"kind": "file", "role": "new_file", "path": path, "sha256": sha256}


def _scenario_tracked_plus_new(tmp_path):
    """A task that edits a tracked file (non-empty diff) AND creates a new file
    -- the shape P3 takes. Returns everything needed to assemble a claim."""
    repo, base, target = _repo(tmp_path)
    target.write_text("after\n", encoding="utf-8")  # tracked change -> non-empty diff
    new_file = repo / "added.txt"
    new_file.write_text("brand new bytes\n", encoding="utf-8")
    test_output = repo / "test-output.txt"
    test_output.write_text("3 passed in 0.01s\n", encoding="utf-8")
    diff_artifact = repo / "change.diff"
    diff_artifact.write_text(_git(repo, "diff", base).stdout, encoding="utf-8")
    new_sha = hashlib.sha256(new_file.read_bytes()).hexdigest()
    packet = {
        "allowed_paths": ["allowed.txt", "added.txt"],
        "forbidden_paths": [],
        "new_files_allowed": True,
    }
    return repo, base, test_output, diff_artifact, new_sha, packet


def test_verifies_new_file_with_matching_hash(tmp_path):
    repo, base, test_output, diff_artifact, new_sha, packet = (
        _scenario_tracked_plus_new(tmp_path)
    )
    claim = _claim(test_output, diff_artifact, ["allowed.txt", "added.txt"])
    claim["evidence"].append(_new_file_item("added.txt", new_sha))

    evidence = verify_code_change_claim(
        claim, worktree=repo, starting_ref=base, packet=packet
    )

    assert evidence["completion_verified"] is True
    assert evidence["new_files"] == {"added.txt": new_sha}


def test_rejects_new_file_with_no_hash_evidence(tmp_path):
    # The new file is enumerated by name (so allow-list gating applies) but the
    # claim carries no 'new_file' entry -> its bytes are unverified -> rejected.
    repo, base, test_output, diff_artifact, _new_sha, packet = (
        _scenario_tracked_plus_new(tmp_path)
    )
    claim = _claim(test_output, diff_artifact, ["allowed.txt", "added.txt"])

    with pytest.raises(CodeEvidenceRejected, match="new_file evidence does not match"):
        verify_code_change_claim(claim, worktree=repo, starting_ref=base, packet=packet)


def test_rejects_new_file_with_wrong_hash(tmp_path):
    repo, base, test_output, diff_artifact, _new_sha, packet = (
        _scenario_tracked_plus_new(tmp_path)
    )
    claim = _claim(test_output, diff_artifact, ["allowed.txt", "added.txt"])
    claim["evidence"].append(_new_file_item("added.txt", "0" * 64))

    with pytest.raises(CodeEvidenceRejected, match="new file SHA-256 does not match"):
        verify_code_change_claim(claim, worktree=repo, starting_ref=base, packet=packet)


def test_rejects_new_file_evidence_for_nonexistent_new_file(tmp_path):
    # An 'extra' new_file entry that does not correspond to an actually-created
    # new file must be rejected (no smuggling hashes for files not in the diff).
    repo, base, test_output, diff_artifact, new_sha, packet = (
        _scenario_tracked_plus_new(tmp_path)
    )
    claim = _claim(test_output, diff_artifact, ["allowed.txt", "added.txt"])
    claim["evidence"].append(_new_file_item("added.txt", new_sha))
    claim["evidence"].append(_new_file_item("ghost.txt", "1" * 64))

    with pytest.raises(CodeEvidenceRejected, match="new_file evidence does not match"):
        verify_code_change_claim(claim, worktree=repo, starting_ref=base, packet=packet)
