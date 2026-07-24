"""P4 / finding E4: the worker's Bash is confined to its task worktree.

These tests prove the *shipped* confinement -- the exact profile
(`phase3/sandbox/worker.sb`) and the exact wrapper `ClaudeCodeAdapter._argv`
composes -- enforces both directions:

  (1) a worktree-rooted write run through the confinement succeeds;
  (2) a write outside the worktree is denied by the kernel, not by the CLI.

The mechanism is a macOS `sandbox-exec` profile, so the whole module is skipped
off macOS (enforceability can only be proven on the Mac, where the bridge runs).
"""

import os
import subprocess
import sys
from uuid import uuid4

import pytest

from reference.cec import adapters
from reference.cec.adapters import (
    WORKER_SANDBOX_PROFILE,
    ClaudeCodeAdapter,
    sandbox_available,
)
from reference.cec.contracts import WorkerCommand, WorkerKind

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="worker Bash confinement uses macOS sandbox-exec"
)


def _run_confined(*, worktree, home_state, proc_tmp, gitdir, shell_command):
    """Run one shell command under the shipped worker.sb profile.

    Mirrors exactly what ClaudeCodeAdapter._argv composes: the same profile and
    the same four realpath'd `-D` params (incl. GITDIR_ROOT, finding G3). Paths
    are resolved because the kernel evaluates the canonical path
    (`/var/...` -> `/private/var/...`).
    """
    return subprocess.run(
        [
            adapters._SANDBOX_EXEC,
            "-f",
            str(WORKER_SANDBOX_PROFILE),
            "-D",
            f"WORKTREE_ROOT={os.path.realpath(str(worktree))}",
            "-D",
            f"HOME_STATE={os.path.realpath(str(home_state))}",
            "-D",
            f"PROC_TMP={os.path.realpath(str(proc_tmp))}",
            "-D",
            f"GITDIR_ROOT={os.path.realpath(str(gitdir))}",
            "/bin/sh",
            "-c",
            shell_command,
        ],
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def confinement(tmp_path):
    """A worktree plus the auxiliary write locations the profile grants, each a
    distinct sibling so 'outside' is covered by no allow rule."""
    worktree = tmp_path / "wt"
    home_state = tmp_path / "home"
    proc_tmp = tmp_path / "proctmp"
    gitdir = tmp_path / "gitdir"
    outside = tmp_path / "outside"
    for d in (worktree, home_state, proc_tmp, gitdir, outside):
        d.mkdir()
    assert sandbox_available(), "shipped profile + sandbox-exec must be present on macOS"
    return worktree, home_state, proc_tmp, gitdir, outside


def test_worktree_rooted_write_succeeds(confinement):
    worktree, home_state, proc_tmp, gitdir, _outside = confinement
    target = os.path.realpath(str(worktree)) + "/inside.txt"
    result = _run_confined(
        worktree=worktree,
        home_state=home_state,
        proc_tmp=proc_tmp,
        gitdir=gitdir,
        # A nested mkdir + write, the shape a real build/test turn takes.
        shell_command=f"mkdir -p '{os.path.dirname(target)}/nested' "
        f"&& echo confined > '{target}'",
    )
    assert result.returncode == 0, result.stderr
    assert os.path.isfile(target)
    with open(target, encoding="utf-8") as fh:
        assert fh.read().strip() == "confined"


def test_out_of_worktree_write_fails(confinement):
    worktree, home_state, proc_tmp, gitdir, outside = confinement
    target = os.path.realpath(str(outside)) + "/escape.txt"
    result = _run_confined(
        worktree=worktree,
        home_state=home_state,
        proc_tmp=proc_tmp,
        gitdir=gitdir,
        shell_command=f"echo escape > '{target}'",
    )
    assert result.returncode != 0
    assert not os.path.exists(target), "write outside the worktree must not land"
    assert "not permitted" in result.stderr.lower()


def _claude_command(tmp_path):
    return WorkerCommand(
        command_id="sandbox-argv",
        work_item_id="phase3-p4",
        worker_kind=WorkerKind.CLAUDE_CODE,
        lease_token=uuid4(),
        lease_epoch=0,
        packet_hash="0" * 64,
        packet={"objective": "do the confined work"},
        working_directory=tmp_path,
        result_schema={},
    )


def test_argv_wraps_claude_in_the_worktree_sandbox(tmp_path):
    """The launched argv is the sandbox invocation, so the confinement is what
    actually runs -- not a profile that exists but is never applied."""
    argv = ClaudeCodeAdapter()._argv(_claude_command(tmp_path), {})

    assert argv[0] == adapters._SANDBOX_EXEC
    assert argv[argv.index("-f") + 1] == str(WORKER_SANDBOX_PROFILE)
    # The worktree is passed symlink-resolved, or no rule would match it.
    assert f"WORKTREE_ROOT={os.path.realpath(str(tmp_path))}" in argv
    d_params = [argv[i + 1] for i, a in enumerate(argv) if a == "-D"]
    assert any(p.startswith("HOME_STATE=") for p in d_params)
    assert any(p.startswith("PROC_TMP=") for p in d_params)
    # G3: GITDIR_ROOT must be supplied, or the profile's rule references an
    # undefined param and sandbox-exec rejects it ("expected pattern, got boolean").
    assert any(p.startswith("GITDIR_ROOT=") for p in d_params)
    # The wrapped CLI turn survives intact after the sandbox prefix.
    assert "claude" in argv
    assert argv.index("claude") > argv.index("-f")
    assert "-p" in argv and "do the confined work" in argv
