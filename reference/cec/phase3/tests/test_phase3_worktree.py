import subprocess

from reference.cec.phase3.worktree import create_worktree, remove_worktree


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_controller_creates_and_removes_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    record = create_worktree(
        repo_root=repo,
        worktree_root=tmp_path / "worktrees",
        work_item_id="phase3-test",
        starting_ref=base,
    )
    assert (record.worktree_path / "file.txt").read_text(encoding="utf-8") == "base\n"
    remove_worktree(record)
    assert not record.worktree_path.exists()
