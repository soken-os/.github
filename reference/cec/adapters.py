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
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

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


def _process_identity_matches(
    handle: WorkerHandle, working_directory: Path | None = None
) -> bool:
    if handle.pid is None:
        return False
    base = working_directory or Path.cwd()
    record = _read_json_or_none(base / ".cec" / f"{handle.command_id}.process.json")
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

    def _env(self, command: WorkerCommand, paths: dict[str, Path]) -> dict[str, str]:
        return os.environ.copy()

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
            env=self._env(command, paths),
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
                # D1: persist the command_id and the lease fence of the run that
                # actually launched. collect_result stamps the claim from this
                # launch record, so a stale worker's late output is fenced by the
                # epoch it ran under, not by a command rebuilt off a since-
                # renewed live row.
                "command_id": command.command_id,
                "pid": proc.pid,
                "process_start_time": process_start_time,
                "worker_instance_id": worker_instance_id,
                "started_at": started_at.isoformat(),
                "lease_token": str(command.lease_token),
                "lease_epoch": command.lease_epoch,
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

    async def observe(
        self, handle: WorkerHandle, *, working_directory: Path | None = None
    ) -> WorkerObservation:
        # The controller passes working_directory explicitly so observe() never
        # relies on the process-global CWD — which is racy when multiple
        # controller threads serve different programs concurrently (Option B).
        base = working_directory or Path.cwd()
        alive = _process_identity_matches(handle, base)
        exit_code: int | None = None
        output_present = False

        d = base / ".cec"
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
        # D1: stamp the claim's lease fence from the launch-time sidecar of the
        # run that produced this output, never from the live `command` (which may
        # have been rebuilt off a since-renewed row with a newer epoch). A
        # missing, incomplete, or foreign-command_id record yields NO claim, so a
        # stale worker's late output is fenced by content, not by circumstance.
        record = _read_json_or_none(paths["process"])
        if not isinstance(record, dict):
            return None
        if record.get("command_id") != command.command_id:
            return None
        recorded_token = record.get("lease_token")
        recorded_epoch = record.get("lease_epoch")
        if not isinstance(recorded_token, str) or not isinstance(recorded_epoch, int):
            return None
        try:
            launch_lease_token = UUID(recorded_token)
        except ValueError:
            return None
        parsed = self._parse_output(command, paths)
        if parsed is None:
            return None  # empty/invalid output -> no claim (never a false DONE)
        status, summary, evidence, followups = parsed
        return WorkerResultClaim(
            command_id=command.command_id,
            work_item_id=command.work_item_id,
            worker_instance_id=handle.worker_instance_id,
            lease_token=launch_lease_token,  # fence recorded at launch (D1)
            lease_epoch=recorded_epoch,
            status=status,
            summary=summary,
            evidence=evidence,
            proposed_followups=followups,
        )

    async def terminate(
        self,
        handle: WorkerHandle,
        *,
        reason: str,
        grace_seconds: int = 10,
        working_directory: Path | None = None,
    ) -> None:
        if not _process_identity_matches(handle, working_directory):
            return
        assert handle.pid is not None
        try:
            os.killpg(os.getpgid(handle.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        for _ in range(max(1, grace_seconds)):
            if not _process_identity_matches(handle, working_directory):
                return
            await asyncio.sleep(1)
        if not _process_identity_matches(handle, working_directory):
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


# --- E4: confine the worker's shell to its task worktree ---------------------
#
# The worker CLI runs each Bash tool call in a shell it spawns. Nothing in the
# CLI's own permission model is a security boundary: allowed-tool patterns gate
# prompting, not execution, and under `bypassPermissions` (the mode this packet
# carries) they are waived entirely, so they cannot enforceably restrict Bash.
# The smallest control that *is* enforceable on the Mac bridge is a macOS
# sandbox-exec profile: we wrap the whole CLI process in it, and because the
# kernel sandbox is inherited by every child, it binds the spawned shells too.
# Its sole enforced boundary is that filesystem writes are confined to the task
# worktree (plus the CLI's own state dir and the per-process temp dir). This
# retires the blanket-bypassPermissions caveat (finding E4): the worker may
# still edit freely, but it can no longer write outside its worktree. See
# phase3/sandbox/worker.sb and phase3/sandbox/worker-bash.md for scope + limits.

WORKER_SANDBOX_PROFILE = (
    Path(__file__).resolve().parent / "phase3" / "sandbox" / "worker.sb"
)
_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _worker_cli_state_dir(command: WorkerCommand | None = None) -> Path:
    """The worker CLI's own writable state/cache dir.

    Granted inside the sandbox so a real headless turn can still persist its
    session while everything else outside the worktree stays read-only.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    if command is not None:
        return command.working_directory / ".cec" / "claude-config"
    return Path.home() / ".claude"


def _worker_cache_dir() -> Path:
    return Path.home() / "Library" / "Caches" / "claude-cli-nodejs"


def _claude_tmp_root() -> Path:
    return Path("/private/tmp") / f"claude-{os.getuid()}"


def _private_tmp_root() -> Path:
    return Path("/private/tmp")


def _routine_auth_fallback_enabled(command: WorkerCommand) -> bool:
    return str(command.packet.get("authority_class", "")).upper() == "ROUTINE"


def _routine_auth_profile(command: WorkerCommand) -> Path:
    """Per-worker overlay profile for Claude Code's legacy user auth file.

    A disposable CLAUDE_CONFIG_DIR was tested first and breaks auth on Scott's
    Mac ("Not logged in"). The fallback is intentionally ROUTINE-only: only
    routine packets use this generated profile; higher-authority packets keep
    the base worker.sb profile and do not get ~/.claude.json write grants.
    """
    profile = command.working_directory / ".cec" / "worker-routine-auth.sb"
    home = _canonical(Path.home())
    base_profile = WORKER_SANDBOX_PROFILE.read_text(encoding="utf-8")
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        base_profile
        + f"""

;; ROUTINE-only fallback for Claude Code's current Mac auth storage.
;; Generated by sandbox_wrap only for authority_class=ROUTINE after the safer
;; disposable CLAUDE_CONFIG_DIR path proved insufficient for authenticated turns.
(allow file-write*
    (literal "{home}/.claude.json")
    (literal "{home}/.claude.json.lock")
    (regex #"^{home}/\\.claude\\.json\\.tmp\\.[^/]*$"))
(allow file-write* (subpath "{home}/.claude/session-env"))
""",
        encoding="utf-8",
    )
    return profile


def sandbox_available() -> bool:
    """True when worktree-write confinement is enforceable in this process.

    Requires macOS (sandbox-exec is Apple's), the sandbox-exec binary, and the
    shipped profile. The reference bridge runs on the Mac, where all three hold.
    """
    return (
        sys.platform == "darwin"
        and Path(_SANDBOX_EXEC).exists()
        and WORKER_SANDBOX_PROFILE.exists()
    )


def _canonical(path: Path) -> str:
    """Symlink-resolved absolute path.

    The kernel sandbox evaluates the canonical path, so an un-resolved param
    (e.g. `/var/...`) would never match a rule against its real `/private/var/...`
    target. Every `-D` path must therefore be realpath'd before it is passed.
    """
    return os.path.realpath(str(path))


def _worktree_gitdir(working_directory: Path) -> Path | None:
    """The linked worktree's OWN gitdir (`.git/worktrees/<name>/`), or None.

    Finding G3: a linked worktree's git operations (`git add -N`, and the
    index-stat refresh a plain `git diff` performs) write `index`/`index.lock`
    into this dir, which lives OUTSIDE the worktree subtree — so the P4 profile
    denied them and silenced the worker. Granting this narrow dir fixes it.

    Deliberately NOT the shared object store (`.git/objects/`): with intent-to-add
    (`git add -N`) the diff needs only an index entry, never a blob, so the
    worker can produce a complete diff yet still cannot commit content — only the
    controller commits/publishes.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(working_directory), "rev-parse", "--absolute-git-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    return Path(out) if out else None


def sandbox_wrap(
    argv: list[str],
    working_directory: Path,
    *,
    cli_state_dir: Path | None = None,
    profile: Path | None = None,
) -> list[str]:
    """Prefix argv with the worktree-confining sandbox when it is enforceable.

    Off macOS (no sandbox-exec) the argv is returned unchanged; enforceability
    can only be proven on the Mac, which is where the bridge runs.
    """
    if not sandbox_available():
        return argv
    params = [
        "-D",
        f"WORKTREE_ROOT={_canonical(working_directory)}",
        "-D",
        f"HOME_STATE={_canonical(cli_state_dir or _worker_cli_state_dir())}",
        "-D",
        f"PROC_TMP={_canonical(Path(tempfile.gettempdir()))}",
        "-D",
        f"CLAUDE_TMP_ROOT={_canonical(_claude_tmp_root())}",
        "-D",
        f"PRIVATE_TMP_ROOT={_canonical(_private_tmp_root())}",
        "-D",
        f"CLAUDE_CACHE_ROOT={_canonical(_worker_cache_dir())}",
    ]
    gitdir = _worktree_gitdir(working_directory)
    # GITDIR_ROOT is optional in the profile: a non-worktree cwd resolves its
    # gitdir to the repo's own .git and needs no separate grant, so we point the
    # param at the worktree subtree in that case to keep the rule a harmless
    # no-op rather than granting anything extra.
    params += [
        "-D",
        f"GITDIR_ROOT={_canonical(gitdir) if gitdir else _canonical(working_directory)}",
    ]
    return [_SANDBOX_EXEC, "-f", str(profile or WORKER_SANDBOX_PROFILE), *params, *argv]


# --- Claude Code -------------------------------------------------------------


class ClaudeCodeAdapter(_SubprocessAdapterBase):
    """Drives `claude -p` headless, forcing typed JSON output.

    Reads ANTHROPIC_API_KEY (or the CLI's configured auth) from the environment.
    """

    worker_kind = WorkerKind.CLAUDE_CODE

    def __init__(self, claude_bin: str = "claude") -> None:
        self._bin = claude_bin

    def _env(self, command: WorkerCommand, paths: dict[str, Path]) -> dict[str, str]:
        env = os.environ.copy()
        # G8: mark that the worker runs inside the confinement, so a test suite it
        # runs as its acceptance gate skips host-capability tests (ps, nested
        # sandbox-exec) it physically cannot run — instead of failing them and
        # never claiming a completed result.
        if sandbox_available():
            env["CEC_WORKER_SANDBOX"] = "1"
        if _routine_auth_fallback_enabled(command):
            # ROUTINE fallback: keep Claude Code on its existing authenticated
            # ~/.claude.json path, then grant only that file family via a
            # generated routine-only profile in _argv().
            return env
        state_dir = _worker_cli_state_dir(command)
        state_dir.mkdir(parents=True, exist_ok=True)
        env["CLAUDE_CONFIG_DIR"] = str(state_dir)
        return env

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
        # Unattended edits within the packet's sandbox. allowed_paths /
        # forbidden_paths are still enforced by the controller via post-hoc diff
        # checks; the packet's permission_mode governs *editing* only. What the
        # worker's shell may *write* is now confined to the worktree at the OS
        # level by sandbox_wrap() below (finding E4): no longer left to the
        # CLI's honor system, so bypassPermissions no longer means unbounded FS.
        permission_mode = str(packet.get("permission_mode") or "acceptEdits")
        argv += ["--permission-mode", permission_mode]
        session_id = packet.get("resume_session_id")
        if isinstance(session_id, str) and session_id:
            argv += ["--resume", session_id]  # resume is a cache, never the truth
        return sandbox_wrap(
            argv,
            command.working_directory,
            cli_state_dir=_worker_cli_state_dir(command),
            profile=(
                _routine_auth_profile(command)
                if _routine_auth_fallback_enabled(command)
                else None
            ),
        )

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
