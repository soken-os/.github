"""Pure Phase-2 reconciliation decisions.

No clock, database, network, filesystem, or model access is permitted here.
The caller supplies the observation time so identical inputs always produce
an identical action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from .contracts import WorkerObservation, WorkerProcessState, WorkerResultClaim


class ActionKind(StrEnum):
    NONE = "NONE"
    HOLD_UNOBSERVABLE = "HOLD_UNOBSERVABLE"
    RECLAIM_EXPIRED = "RECLAIM_EXPIRED"
    LAUNCH_WORKER = "LAUNCH_WORKER"
    RENEW_LEASE = "RENEW_LEASE"
    VERIFY_RESULT = "VERIFY_RESULT"
    ENABLE_AUTO_MERGE = "ENABLE_AUTO_MERGE"
    REQUEST_DECISION = "REQUEST_DECISION"
    COMPLETE = "COMPLETE"
    ESCALATE_RECOVERY = "ESCALATE_RECOVERY"


@dataclass(frozen=True)
class Observation:
    work_item_id: str
    observed_at: datetime
    worker: WorkerObservation | None = None
    pr: Mapping[str, Any] | None = None
    ci: Mapping[str, Any] | None = None
    deployment: Mapping[str, Any] | None = None
    result_claim: WorkerResultClaim | None = None
    observer_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconcileAction:
    kind: ActionKind
    expected_version: int
    idempotency_key: str
    payload: Mapping[str, Any]


def _action(
    item: Mapping[str, Any], kind: ActionKind, suffix: str, **payload: Any
) -> ReconcileAction:
    return ReconcileAction(
        kind=kind,
        expected_version=int(item["version"]),
        idempotency_key=f"{item['id']}:{suffix}:{item['version']}",
        payload=payload,
    )


def _hold_or_escalate(
    item: Mapping[str, Any], suffix: str, **extra: Any
) -> ReconcileAction:
    """Hold custody on an unobservable expired lease, counting toward the ceiling.

    Every hold on an EXPIRED lease must increment recovery_attempts (the action
    executor performs the CAS-guarded increment when the payload flag is set);
    otherwise a permanently failing observer holds forever and the escalation
    ceiling is unreachable. At the ceiling, escalate instead of incrementing so
    the schema CHECK (recovery_attempts <= max_recovery_attempts) is never hit.
    """
    attempts = int(item.get("recovery_attempts", 0))
    maximum = int(item.get("max_recovery_attempts", 3))
    if attempts >= maximum:
        return _action(
            item,
            ActionKind.ESCALATE_RECOVERY,
            suffix,
            redispatch_allowed=False,
            **extra,
        )
    return _action(
        item,
        ActionKind.HOLD_UNOBSERVABLE,
        suffix,
        redispatch_allowed=False,
        increment_recovery_attempts=True,
        **extra,
    )


def expired_lease_action(
    item: Mapping[str, Any], observation: Observation
) -> ReconcileAction:
    """Decide lease expiry without inferring death from observer failure.

    EXITED/MISSING is authoritative only when the worker observer returned
    successfully. Any observer error holds custody at the controller and
    prevents redispatch. RUNNING is fenced and terminated before redispatch.
    """
    if observation.observed_at < item["lease_expires_at"]:
        return _action(item, ActionKind.NONE, "lease-current")
    if observation.observer_errors:
        return _hold_or_escalate(
            item, "worker-unobservable", observer_errors=observation.observer_errors
        )
    if observation.worker is None:
        return _hold_or_escalate(item, "worker-not-observed")
    if observation.worker.state is WorkerProcessState.RUNNING:
        return _action(
            item,
            ActionKind.RECLAIM_EXPIRED,
            f"reclaim-{item['lease_epoch']}",
            terminate_worker=True,
            redispatch_allowed=False,
            next_custodian="CONTROLLER",
        )
    if observation.worker.state in (
        WorkerProcessState.EXITED,
        WorkerProcessState.MISSING,
    ):
        return _action(
            item,
            ActionKind.RECLAIM_EXPIRED,
            f"reclaim-{item['lease_epoch']}",
            terminate_worker=False,
            redispatch_allowed=True,
            next_custodian="CONTROLLER",
        )
    return _hold_or_escalate(item, "worker-starting")


def decide(item: Mapping[str, Any], observation: Observation) -> ReconcileAction:
    """Pure desired-state + observation -> exactly one idempotent action."""
    if item["stage"] in ("COMPLETE", "CANCELLED"):
        return _action(item, ActionKind.NONE, "terminal")
    if observation.observer_errors:
        return (
            expired_lease_action(item, observation)
            if observation.observed_at >= item["lease_expires_at"]
            else _action(
                item,
                ActionKind.HOLD_UNOBSERVABLE,
                "observer-error",
                redispatch_allowed=False,
                observer_errors=observation.observer_errors,
            )
        )
    if observation.result_claim is not None:
        claim = observation.result_claim
        if claim.lease_epoch != item["lease_epoch"] or str(claim.lease_token) != str(
            item["lease_token"]
        ):
            return _action(item, ActionKind.ESCALATE_RECOVERY, "stale-result-claim")
        return _action(item, ActionKind.VERIFY_RESULT, "verify-result")
    stage, wait = item["stage"], item["wait_reason"]
    if (
        stage == "ACCEPTING"
        and wait == "DEPLOY"
        and observation.deployment
        and observation.deployment.get("all_hosts_exact_sha") is True
    ):
        return _action(item, ActionKind.COMPLETE, "complete")
    if (
        stage == "VERIFYING"
        and wait in ("MERGE", "DEPLOY")
        and observation.pr
        and observation.pr.get("merged") is True
    ):
        return _action(item, ActionKind.NONE, "wait-deploy")
    if (
        stage == "VERIFYING"
        and wait == "CI"
        and observation.ci
        and observation.ci.get("state") == "FAILED"
    ):
        return _action(item, ActionKind.LAUNCH_WORKER, "fix-ci")
    if (
        stage == "VERIFYING"
        and wait == "CI"
        and observation.ci
        and observation.ci.get("state") == "GREEN"
    ):
        if observation.pr is None:
            return _action(
                item,
                ActionKind.HOLD_UNOBSERVABLE,
                "merge-authority-unknown",
                redispatch_allowed=False,
            )
        if observation.pr.get("merge_authorized") is True:
            return _action(item, ActionKind.ENABLE_AUTO_MERGE, "enable-automerge")
        return _action(item, ActionKind.REQUEST_DECISION, "merge-authority")
    if (
        stage == "EXECUTING"
        and wait == "WORKER"
        and observation.observed_at >= item["lease_expires_at"]
    ):
        return expired_lease_action(item, observation)
    if stage == "READY" and wait == "NONE":
        return _action(item, ActionKind.LAUNCH_WORKER, "launch")
    if (
        stage == "EXECUTING"
        and wait == "WORKER"
        and observation.worker
        and observation.worker.state is WorkerProcessState.RUNNING
    ):
        return _action(item, ActionKind.RENEW_LEASE, f"renew-{item['lease_epoch']}")
    return _action(item, ActionKind.NONE, "wait")
