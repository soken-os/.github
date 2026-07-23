import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime

from reference.cec import adapters
from reference.cec.adapters import ScriptAdapter
from reference.cec.contracts import WorkerHandle, WorkerObservation, WorkerProcessState


def _handle(pid):
    return WorkerHandle(
        "identity-test", "phase2", "script-test", pid, None, datetime.now(UTC)
    )


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
