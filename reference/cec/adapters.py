"""CEC worker adapters — reference implementations of the locked WorkerAdapter.

Drop-in reference for the Mac bridge. Implements the `WorkerAdapter` Protocol
from `contracts.py` for the two model CLIs (Claude Code, Codex) plus a
deterministic script worker. Stdlib only — no third-party deps — so it runs on
the Mac as-is once the CLIs and provider keys are present.

WHAT THIS IS / ISN'T
  * It IS the translation layer: launch a bounded, headless model turn; observe
    the OS process; parse the CLI's *typed* output into a WorkerResultClaim.
  * It is NOT the controller. It never decides completion and never touches the
    registry. `collect_result` returns a *claim*; the controller verifies
    evidence and transitions state (decision doc §5, §2 completion rule).

CRASH SAFETY
  The controller is durable and may restart while a worker runs. So observation
  must not depend on an in-memory Process object. Each launch writes sidecar
  files under `<working_directory>/.cec/<command_id>.*` (pid, stdout, exit
  code). `observe()` re-derives state purely from the process table + those
  files, so a restarted controller can still see a worker it did not start.

PRODUCTION NOTES (kept honest rather than hidden)
  * Workers are started in a new session (`start_new_session=True`) so they
    outlive a controller crash. True double-fork/daemonization and a real
    process-supervisor are a later hardening; the sidecar model is enough for
    the Phase 2 one-task slice.
  * Exit-code capture is best-effort via a reaper task. If the controller dies
    mid-run, the exit file may be absent; `observe()` then reports EXITED with
    exit_code=None (pid gone, output present) or MISSING (pid gone, no output).
    Per finding A1, the controller must treat an *unobservable* worker as
    possibly-alive and must not redispatch on that basis.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .contracts import (
    ClaimedStatus,
    WorkerCommand,
    WorkerHandle,
    WorkerKind,
    WorkerObservation,
    WorkerProcessState,
    WorkerResultClaim,
)

# --- Finding A4: secrets must never live in the durable work packet ----------

_SECRET_HINTS = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "bearer",
)


def assert_no_secrets_in_packet(packet: Mapping[str, Any]) -> None:
    """Raise if a packet field name looks like a credential.

    Provider keys (XAI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY, and Codex's
    CODEX_API_KEY) are read from the process environment by the CLIs. The packet
    is persisted as jsonb, so a key placed there would leak into durable state.
    """

    def _walk(node: Any, path: str = "") -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                lowered = str(k).lower()
                if any(hint in lowered for hint in _SECRET_HINTS):
                    raise ValueError(
                        f"work_packet field {path + str(k)!r} looks like a secret; "
                        "provider keys must come from the environment, not the packet"
                    )
                _walk(v, f"{path}{k}.")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                _walk(v, f"{path}{i}.")

    _walk(packet)


# --- shared process + sidecar helpers ----------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = liveness probe, does not actually signal
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


def _process_start_time(pid: int) -> str | None:
    """Return the OS start-time identity used to defeat PID reuse.

    `lstart` is available on both macOS and Linux. The raw OS value is stored
    and compared; it is never parsed into a lossy timestamp.
    """
    completed = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    value = completed.stdout.strip()
    return value or None


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _process_identity_matches(handle: WorkerHandle) -> bool:
    if handle.pid is None:
        return False
    record = _read_json_or_none(
        Path.cwd() / ".cec" / f"{handle.command_id}.process.json"
    )
    if not isinstance(record, dict):
        return False
    recorded_start = record.get("process_start_time")
    return (
        record.get("pid") == handle.pid
        and isinstance(recorded_start, str)
        and _pid_alive(handle.pid)
        and _process_start_time(handle.pid) == recorded_start
    )


def _cec_dir(working_directory: Path, command_id: str) -> Path:
    d = working_directory / ".cec"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _paths(command: WorkerCommand) -> dict[str, Path]:
    d = _cec_dir(command.working_directory, command.command_id)
    stem = command.command_id
    return {
        "stdout": d / f"{stem}.stdout",
        "stderr": d / f"{stem}.stderr",
        "exit": d / f"{stem}.exit",
        "pid": d / f"{stem}.pid",
        "process": d / f"{stem}.process.json",
        "schema": d / f"{stem}.schema.json",
        "last": d / f"{stem}.last.json",  # Codex --output-last-message target
    }


def _read_json_or_none(path: Path) -> Any | None:
    """Return parsed JSON, or None if absent/empty/invalid.

    Empty-or-invalid -> None directly serves finding: a Codex run can exit 0
    with empty stdout when stdio is detached; an empty file must yield NO claim
    so the controller treats it as a failure to retry, never as success.
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


class _SubprocessAdapterBase:
    """Common launch/observe/terminate over a headless CLI subprocess."""

    worker_kind: WorkerKind

    def _argv(self, command: WorkerCommand, paths: dict[str, Path]) -> list[str]:
        raise NotImplementedError

    def _parse_output(
        self, command: WorkerCommand, paths: dict[str, Path]
    ) -> (
        tuple[
            ClaimedStatus,
            str,
            tuple[Mapping[str, Any], ...],
            tuple[Mapping[str, Any], ...],
        ]
        | None
    ):
        raise NotImplementedError

    async def launch(self, command: WorkerCommand) -> WorkerHandle:
        assert_no_secrets_in_packet(command.packet)  # A4
        paths = _paths(command)
        argv = self._argv(command, paths)

        prior = _read_json_or_none(paths["process"])
        if isinstance(prior, dict):
            prior_pid = prior.get("pid")
            prior_start = prior.get("process_start_time")
            if (
                isinstance(prior_pid, int)
                and isinstance(prior_start, str)
                and isinstance(prior.get("worker_instance_id"), str)
                and isinstance(prior.get("started_at"), str)
            ):
                # A command ID is single-use even after its process exits.
                # Returning the durable handle lets reconciliation collect the
                # result (or escalate missing output) without relaunching it.
                return WorkerHandle(
                    command_id=command.command_id,
                    work_item_id=command.work_item_id,
                    worker_instance_id=str(prior["worker_instance_id"]),
                    pid=prior_pid,
                    session_id=None,
                    started_at=datetime.fromisoformat(str(prior["started_at"])),
                )

        stdout_f = paths["stdout"].open("wb")
        stderr_f = paths["stderr"].open("wb")
        # New session so the worker survives a controller crash; the CLI inherits
        # the environment (and thus its provider key) but never the packet's.
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=stdout_f,
            stderr=stderr_f,
            cwd=str(command.working_directory),
            start_new_session=True,
            env=os.environ.copy(),
        )
        process_start_time = _process_start_time(proc.pid)
        if process_start_time is None:
            proc.terminate()
            await proc.wait()
            stdout_f.close()
            stderr_f.close()
            raise RuntimeError(
                "worker start time is unobservable; refusing PID-only custody"
            )
        worker_instance_id = f"{self.worker_kind.value.lower()}-{uuid4().hex[:12]}"
        started_at = _now()
        paths["pid"].write_text(str(proc.pid), encoding="utf-8")
        _atomic_json(
            paths["process"],
            {
                "pid": proc.pid,
                "process_start_time": process_start_time,
                "worker_instance_id": worker_instance_id,
                "started_at": started_at.isoformat(),
            },
        )

        # Best-effort reaper: record the exit code when the process finishes.
        async def _reap() -> None:
            rc = await proc.wait()
            stdout_f.close()
            stderr_f.close()
            paths["exit"].write_text(str(rc), encoding="utf-8")

        asyncio.ensure_future(_reap())

        return WorkerHandle(
            command_id=command.command_id,
            work_item_id=command.work_item_id,
            worker_instance_id=worker_instance_id,
            pid=proc.pid,
            session_id=None,  # populated by collect_result if the CLI reports one
            started_at=started_at,
        )

    async def observe(self, handle: WorkerHandle) -> WorkerObservation:
        # Reconstruct paths from the handle without the original command.
        # (working_directory is not on the handle; observe() is given enough by
        #  the controller in practice. Here we probe by pid + the exit sidecar
        #  the reaper writes; output presence is confirmed in collect_result.)
        alive = _process_identity_matches(handle)
        exit_code: int | None = None
        output_present = False

        # The controller knows the working directory; a production controller
        # passes it in. For the reference we look relative to CWD's .cec.
        d = Path.cwd() / ".cec"
        exit_path = d / f"{handle.command_id}.exit"
        stdout_path = d / f"{handle.command_id}.stdout"
        last_path = d / f"{handle.command_id}.last.json"

        rc_text = None
        try:
            rc_text = exit_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            rc_text = None
        if rc_text:
            try:
                exit_code = int(rc_text)
            except ValueError:
                exit_code = None

        for p in (last_path, stdout_path):
            try:
                if p.stat().st_size > 0:
                    output_present = True
                    break
            except FileNotFoundError:
                continue

        heartbeat_at: datetime | None = None
        try:
            mtime = stdout_path.stat().st_mtime
            heartbeat_at = datetime.fromtimestamp(mtime, tz=UTC)
        except FileNotFoundError:
            pass

        if alive:
            state = WorkerProcessState.RUNNING
        elif rc_text is not None or output_present:
            state = WorkerProcessState.EXITED
        else:
            # pid gone AND no exit record AND no output: we cannot tell whether it
            # ran. Report MISSING; per finding A1 the controller must NOT infer
            # "dead, safe to redispatch" from MISSING alone.
            state = WorkerProcessState.MISSING

        return WorkerObservation(
            state=state,
            observed_at=_now(),
            exit_code=exit_code,
            output_present=output_present,
            heartbeat_at=heartbeat_at,
        )

    async def collect_result(
        self, handle: WorkerHandle, command: WorkerCommand
    ) -> WorkerResultClaim | None:
        paths = _paths(command)
        parsed = self._parse_output(command, paths)
        if parsed is None:
            return None  # empty/invalid output -> no claim (never a false DONE)
        status, summary, evidence, followups = parsed
        return WorkerResultClaim(
            command_id=command.command_id,
            work_item_id=command.work_item_id,
            worker_instance_id=handle.worker_instance_id,
            lease_token=command.lease_token,  # stamp the fence onto the claim
            lease_epoch=command.lease_epoch,
            status=status,
            summary=summary,
            evidence=evidence,
            proposed_followups=followups,
        )

    async def terminate(
        self, handle: WorkerHandle, *, reason: str, grace_seconds: int = 10
    ) -> None:
        if not _process_identity_matches(handle):
            return
        assert handle.pid is not None
        try:
            # Signal the whole process group (new session) so children die too.
            os.killpg(os.getpgid(handle.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        for _ in range(max(1, grace_seconds)):
            if not _process_identity_matches(handle):
                return
            await asyncio.sleep(1)
        if not _process_identity_matches(handle):
            return
        try:
            os.killpg(os.getpgid(handle.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return


def _objective(packet: Mapping[str, Any]) -> str:
    obj = packet.get("objective")
    if not isinstance(obj, str) or not obj.strip():
        raise ValueError("work_packet.objective must be a non-empty string")
    return obj


# --- Claude Code -------------------------------------------------------------


class ClaudeCodeAdapter(_SubprocessAdapterBase):
    """Drives `claude -p` headless, forcing typed JSON output.

    Reads ANTHROPIC_API_KEY (or the CLI's configured auth) from the environment.
    """

    worker_kind = WorkerKind.CLAUDE_CODE

    def __init__(self, claude_bin: str = "claude") -> None:
        self._bin = claude_bin

    def _argv(self, command: WorkerCommand, paths: dict[str, Path]) -> list[str]:
        packet = command.packet
        argv = [
            self._bin,
            "-p",
            _objective(packet),
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(command.result_schema),
        ]
        allowed = packet.get("allowed_tools")
        if isinstance(allowed, (list, tuple)) and allowed:
            argv += ["--allowedTools", ",".join(map(str, allowed))]
        argv += ["--add-dir", str(command.working_directory)]
        # Unattended edits within the packet's sandbox; the packet's
        # allowed_paths/forbidden_paths are enforced by the controller via cwd
        # selection and post-hoc diff checks, not by the CLI.
        permission_mode = str(packet.get("permission_mode") or "acceptEdits")
        argv += ["--permission-mode", permission_mode]
        session_id = packet.get("resume_session_id")
        if isinstance(session_id, str) and session_id:
            argv += ["--resume", session_id]  # resume is a cache, never the truth
        return argv

    def _parse_output(self, command: WorkerCommand, paths: dict[str, Path]):
        doc = _read_json_or_none(paths["stdout"])
        if not isinstance(doc, dict):
            return None
        # `claude --output-format json` wraps the result; the schema-valid object
        # lands in `structured_output`.
        structured = doc.get("structured_output")
        if not isinstance(structured, dict):
            return None
        return _claim_from_structured(
            structured, fallback_summary=str(doc.get("result", ""))
        )


# --- Codex -------------------------------------------------------------------


class CodexAdapter(_SubprocessAdapterBase):
    """Drives `codex exec` headless, constraining the final answer to a schema.

    Reads CODEX_API_KEY from the environment (the only place codex exec accepts
    it). Writes the schema to a file and the final message to `<id>.last.json`.
    """

    worker_kind = WorkerKind.CODEX

    def __init__(self, codex_bin: str = "codex") -> None:
        self._bin = codex_bin

    def _argv(self, command: WorkerCommand, paths: dict[str, Path]) -> list[str]:
        paths["schema"].write_text(json.dumps(command.result_schema), encoding="utf-8")
        return [
            self._bin,
            "exec",
            "--json",
            "--output-schema",
            str(paths["schema"]),
            "--output-last-message",
            str(paths["last"]),
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            _objective(command.packet),
        ]

    def _parse_output(self, command: WorkerCommand, paths: dict[str, Path]):
        # Prefer the dedicated final-message file; it is exactly the schema-valid
        # object. Empty file (the detached-TTY exit-0 bug) -> None -> no claim.
        doc = _read_json_or_none(paths["last"])
        if not isinstance(doc, dict):
            return None
        return _claim_from_structured(doc, fallback_summary="")


# --- deterministic script ----------------------------------------------------


class ScriptAdapter(_SubprocessAdapterBase):
    """Runs a deterministic script worker (no model). The packet supplies argv.

    The script is expected to write a schema-valid JSON object to stdout.
    """

    worker_kind = WorkerKind.SCRIPT

    def _argv(self, command: WorkerCommand, paths: dict[str, Path]) -> list[str]:
        argv = command.packet.get("argv")
        if not isinstance(argv, (list, tuple)) or not argv:
            raise ValueError("SCRIPT work_packet must provide a non-empty 'argv'")
        return [str(a) for a in argv]

    def _parse_output(self, command: WorkerCommand, paths: dict[str, Path]):
        doc = _read_json_or_none(paths["stdout"])
        if not isinstance(doc, dict):
            return None
        return _claim_from_structured(doc, fallback_summary="")


# --- shared claim parsing ----------------------------------------------------


def _claim_from_structured(
    structured: Mapping[str, Any], *, fallback_summary: str
) -> (
    tuple[
        ClaimedStatus, str, tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]
    ]
    | None
):
    """Map a schema-valid worker object to (status, summary, evidence, followups).

    Expected worker result_schema shape (the controller defines it per task):
        {"status": "done|needs_input|failed", "summary": str,
         "evidence": [ {...}, ... ], "next": [ {...}, ... ]}
    Unknown/malformed status -> None so the controller treats it as no claim.
    """
    raw_status = str(structured.get("status", "")).lower()
    status_map = {
        "done": ClaimedStatus.RESULT_CLAIMED,
        "result_claimed": ClaimedStatus.RESULT_CLAIMED,
        "needs_input": ClaimedStatus.NEEDS_INPUT,
        "failed": ClaimedStatus.FAILED,
    }
    status = status_map.get(raw_status)
    if status is None:
        return None
    summary = str(structured.get("summary") or fallback_summary or "")
    evidence = tuple(
        e for e in structured.get("evidence", []) if isinstance(e, Mapping)
    )
    followups = tuple(f for f in structured.get("next", []) if isinstance(f, Mapping))
    return status, summary, evidence, followups
