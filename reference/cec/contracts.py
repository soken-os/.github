"""CEC worker-adapter contracts — the locked interface from the V1 decision.

This is the interface half of the executor's worker layer, transcribed from
`docs/executor-dispatch-decision.md` §5 so the reference adapters in
`adapters.py` have something concrete to implement. It is intentionally
dependency-free (stdlib only) so it can be lifted onto the Mac bridge as-is.

Design rules that this interface encodes (do not "improve" them away):

  * The adapter NEVER determines completion and NEVER mutates `work_items`.
    It launches a bounded worker, observes the OS process, and parses typed
    output into a *claim*. The controller verifies evidence and transitions
    state. Prose explains; only evidence transitions state.

  * The adapter carries the lease fence. Every `WorkerResultClaim` it emits is
    stamped with the `lease_token` / `lease_epoch` of the command it ran, so a
    stale worker's late result is rejected by the controller (fencing).

  * Finding A3 (renewal): the CONTROLLER renews leases from observed liveness.
    Adapters do NOT self-renew and expose no worker-side heartbeat call.

  * Finding A4 (secrets): provider API keys are read from the environment by
    the underlying CLI. They MUST NOT appear in `work_packet` (persisted jsonb)
    or any registry column. `adapters.assert_no_secrets_in_packet` enforces it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID


class WorkerKind(StrEnum):
    CLAUDE_CODE = "CLAUDE_CODE"
    CODEX = "CODEX"
    SCRIPT = "SCRIPT"


class WorkerProcessState(StrEnum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    EXITED = "EXITED"
    MISSING = "MISSING"


class ClaimedStatus(StrEnum):
    RESULT_CLAIMED = "RESULT_CLAIMED"
    NEEDS_INPUT = "NEEDS_INPUT"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WorkerCommand:
    command_id: str  # Content-addressed idempotency key.
    work_item_id: str
    worker_kind: WorkerKind
    lease_token: UUID
    lease_epoch: int
    packet_hash: str
    packet: Mapping[str, Any]
    working_directory: Path
    result_schema: Mapping[str, Any]


@dataclass(frozen=True)
class WorkerHandle:
    command_id: str
    work_item_id: str
    worker_instance_id: str
    pid: int | None
    session_id: str | None
    started_at: datetime


@dataclass(frozen=True)
class WorkerObservation:
    state: WorkerProcessState
    observed_at: datetime
    exit_code: int | None
    output_present: bool
    heartbeat_at: datetime | None


@dataclass(frozen=True)
class WorkerResultClaim:
    command_id: str
    work_item_id: str
    worker_instance_id: str
    lease_token: UUID
    lease_epoch: int
    status: ClaimedStatus
    summary: str
    evidence: tuple[Mapping[str, Any], ...]
    proposed_followups: tuple[Mapping[str, Any], ...]


class WorkerAdapter(Protocol):
    async def launch(self, command: WorkerCommand) -> WorkerHandle:
        """Start one bounded worker invocation; do not wait for completion."""

    async def observe(self, handle: WorkerHandle) -> WorkerObservation:
        """Observe OS process state without interpreting model prose."""

    async def collect_result(
        self, handle: WorkerHandle, command: WorkerCommand
    ) -> WorkerResultClaim | None:
        """Parse typed output. Empty or invalid output returns no claim."""

    async def terminate(
        self, handle: WorkerHandle, *, reason: str, grace_seconds: int = 10
    ) -> None:
        """Stop a stale or fenced worker before redispatch."""
