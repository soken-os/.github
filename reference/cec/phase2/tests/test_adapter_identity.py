import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from uuid import UUID

import pytest

from reference.cec import adapters
from reference.cec.adapters import ScriptAdapter
from reference.cec.contracts import (
    WorkerCommand,
    WorkerHandle,
    WorkerKind,
    WorkerObservation,
    WorkerProcessState,
)


def _handle(pid):
    return WorkerHandle(
        "identity-test", "phase2", "script-test", pid, None, datetime.now(UTC)
    )


@pytest.mark.requires_unsandboxed  # uses ps / real process identity
def test_observe_never_calls_pid_reuse_running(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sidecars = tmp_path / ".cec"
    sidecars.mkdir()
    (sidecars / "identity-test.process.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "process_start_time": "definitely-not-this-process",
                "worker_instance_id": "script-test",
                "started_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    observation: WorkerObservation = asyncio.run(
        ScriptAdapter().observe(_handle(os.getpid()))
    )
    assert observation.state is WorkerProcessState.MISSING


@pytest.mark.requires_unsandboxed  # uses ps / real process identity
def test_terminate_refuses_mismatched_start_time(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sidecars = tmp_path / ".cec"
    sidecars.mkdir()
    (sidecars / "identity-test.process.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "process_start_time": "recycled-pid",
                "worker_instance_id": "script-test",
                "started_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        adapters.os,
        "killpg",
        lambda *_: (_ for _ in ()).throw(AssertionError("PID-only signal attempted")),
    )
    asyncio.run(ScriptAdapter().terminate(_handle(os.getpid()), reason="test"))


@pytest.mark.requires_unsandboxed  # uses ps / real process identity
def test_terminate_requires_and_accepts_matching_pid_start_pair(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        start_time = adapters._process_start_time(process.pid)
        assert start_time
        sidecars = tmp_path / ".cec"
        sidecars.mkdir()
        (sidecars / "identity-test.process.json").write_text(
            json.dumps(
                {
                    "pid": process.pid,
                    "process_start_time": start_time,
                    "worker_instance_id": "script-test",
                    "started_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        asyncio.run(
            ScriptAdapter().terminate(
                _handle(process.pid), reason="test", grace_seconds=1
            )
        )
        process.wait(timeout=5)
        assert process.returncode is not None
    finally:
        if process.poll() is None:
            process.kill()


# --- Finding D1: the claim's lease fence comes from the launch record ---------
#
# collect_result must stamp WorkerResultClaim.lease_token/lease_epoch from the
# launch-time sidecar (<command_id>.process.json), not from the live command a
# reconciling controller rebuilt off a possibly-renewed row. A stale worker's
# late output must be fenced by the epoch it actually ran under.

_LAUNCH_TOKEN = UUID("11111111-1111-4111-8111-111111111111")
_NEWER_TOKEN = UUID("22222222-2222-4222-8222-222222222222")


def _command(tmp_path, command_id, *, lease_token, lease_epoch):
    return WorkerCommand(
        command_id=command_id,
        work_item_id="phase2",
        worker_kind=WorkerKind.SCRIPT,
        lease_token=lease_token,
        lease_epoch=lease_epoch,
        packet_hash="0" * 64,
        packet={},
        working_directory=tmp_path,
        result_schema={},
    )


def _write_process_sidecar(tmp_path, filename_command_id, record):
    sidecars = tmp_path / ".cec"
    sidecars.mkdir(exist_ok=True)
    (sidecars / f"{filename_command_id}.process.json").write_text(
        json.dumps(record), encoding="utf-8"
    )


def _write_done_stdout(tmp_path, command_id):
    sidecars = tmp_path / ".cec"
    sidecars.mkdir(exist_ok=True)
    (sidecars / f"{command_id}.stdout").write_text(
        json.dumps({"status": "done", "summary": "ok", "evidence": [], "next": []}),
        encoding="utf-8",
    )


def test_claim_carries_launch_epoch_over_newer_command_epoch(tmp_path):
    command_id = "d1-launch-epoch"
    _write_process_sidecar(
        tmp_path,
        command_id,
        {
            "command_id": command_id,
            "pid": 4321,
            "process_start_time": "launch-start",
            "worker_instance_id": "script-test",
            "started_at": datetime.now(UTC).isoformat(),
            "lease_token": str(_LAUNCH_TOKEN),
            "lease_epoch": 7,
        },
    )
    _write_done_stdout(tmp_path, command_id)
    # The live command carries a *newer* fence (a renewal/reclaim happened after
    # launch). The claim must still carry the launch-time epoch and token.
    command = _command(tmp_path, command_id, lease_token=_NEWER_TOKEN, lease_epoch=12)
    claim = asyncio.run(ScriptAdapter().collect_result(_handle(4321), command))
    assert claim is not None
    assert claim.lease_epoch == 7
    assert claim.lease_token == _LAUNCH_TOKEN


def test_claim_from_mismatched_sidecar_command_id_yields_none(tmp_path):
    command_id = "d1-mismatch"
    # Parseable, done-status output is present, so only the command_id mismatch
    # can suppress the claim, proving the fence is bound by content, not luck.
    _write_done_stdout(tmp_path, command_id)
    _write_process_sidecar(
        tmp_path,
        command_id,
        {
            "command_id": "a-different-command",
            "pid": 4321,
            "process_start_time": "launch-start",
            "worker_instance_id": "script-test",
            "started_at": datetime.now(UTC).isoformat(),
            "lease_token": str(_LAUNCH_TOKEN),
            "lease_epoch": 3,
        },
    )
    command = _command(tmp_path, command_id, lease_token=_LAUNCH_TOKEN, lease_epoch=3)
    claim = asyncio.run(ScriptAdapter().collect_result(_handle(4321), command))
    assert claim is None
