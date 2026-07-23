"""Deterministic Task-151 premature-exit replay with no model calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from ..contracts import (
    ClaimedStatus,
    WorkerCommand,
    WorkerHandle,
    WorkerObservation,
    WorkerProcessState,
    WorkerResultClaim,
)


@dataclass
class EchoWorkerAdapter:
    """First execution exits without paperwork; recovery emits typed evidence."""

    attempt: int = 0

    async def launch(self, command: WorkerCommand) -> WorkerHandle:
        self.attempt += 1
        return WorkerHandle(
            command.command_id,
            command.work_item_id,
            f"echo-{self.attempt}",
            None,
            None,
            datetime.now(UTC),
        )

    async def observe(self, handle: WorkerHandle) -> WorkerObservation:
        return WorkerObservation(
            WorkerProcessState.EXITED, datetime.now(UTC), 0, self.attempt > 1, None
        )

    async def collect_result(
        self, handle: WorkerHandle, command: WorkerCommand
    ) -> WorkerResultClaim | None:
        if self.attempt == 1:
            return None
        return WorkerResultClaim(
            command.command_id,
            command.work_item_id,
            handle.worker_instance_id,
            command.lease_token,
            command.lease_epoch,
            ClaimedStatus.RESULT_CLAIMED,
            "PR green and exact deployed SHA verified",
            ({"kind": "ci", "state": "GREEN"}, {"kind": "deploy", "exact_sha": True}),
            (),
        )

    async def terminate(
        self, handle: WorkerHandle, *, reason: str, grace_seconds: int = 10
    ) -> None:
        return None


TASK_151_ID = "phase0-task-151"
TASK_151_LEASE = UUID("15115115-1151-4151-8151-151151151151")
