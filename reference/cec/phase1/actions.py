"""Shadow-mode registry executor for custody-only reconciliation actions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping
from uuid import uuid4

from ..controller import ActionKind, ReconcileAction
from .registry import Registry, TransitionPatch, TransitionResult


class UnsupportedShadowAction(ValueError):
    pass


class ShadowActionExecutor:
    """Writes custody state only; performs no process/GitHub/Railway side effect."""

    def __init__(
        self, registry: Registry, *, controller_id: str = "cec-controller"
    ) -> None:
        self.registry = registry
        self.controller_id = controller_id

    def execute(
        self,
        item: Mapping[str, Any],
        action: ReconcileAction,
        *,
        observed_at: datetime,
        source_event_id: str,
    ) -> TransitionResult:
        patch = self._patch(item, action, observed_at)
        return self.registry.transition(
            work_item_id=str(item["id"]),
            expected_version=action.expected_version,
            source="reconcile",
            source_event_id=source_event_id,
            event_type=action.kind.value,
            observed_at=observed_at,
            patch=patch,
            event_payload={
                "action": action.kind.value,
                "idempotency_key": action.idempotency_key,
                "payload": dict(action.payload),
                "mode": "SHADOW",
            },
        )

    def _patch(
        self, item: Mapping[str, Any], action: ReconcileAction, now: datetime
    ) -> TransitionPatch:
        if action.kind is ActionKind.RENEW_LEASE:
            return self._base(
                item,
                lease_expires_at=now + timedelta(minutes=5),
                next_signal_type="WORKER_HEARTBEAT",
                next_signal_deadline=now + timedelta(minutes=2),
                recovery_action={"action": "OBSERVE_WORKER"},
            )
        if action.kind is ActionKind.HOLD_UNOBSERVABLE:
            increment = 1 if action.payload.get("increment_recovery_attempts") else 0
            if not increment:
                # Current lease: record the observation, but do not fence or
                # reassign a worker on a transient observer gap.
                return self._base(item)
            return self._base(
                item,
                custodian_type="CONTROLLER",
                custodian_id=self.controller_id,
                lease_token=str(uuid4()),
                lease_epoch_delta=1,
                lease_expires_at=now + timedelta(minutes=5),
                next_signal_type="RETRY_OBSERVER",
                next_signal_key=action.idempotency_key,
                next_signal_deadline=now + timedelta(minutes=1),
                recovery_action={"action": "ESCALATE_RECOVERY_AT_CEILING"},
                recovery_attempt_delta=increment,
            )
        if action.kind is ActionKind.RECLAIM_EXPIRED:
            needs_termination = bool(action.payload.get("terminate_worker"))
            return self._base(
                item,
                custodian_type="CONTROLLER",
                custodian_id=self.controller_id,
                lease_token=str(uuid4()),
                lease_epoch_delta=1,
                lease_expires_at=now + timedelta(minutes=5),
                next_signal_type=(
                    "WORKER_TERMINATION_CONFIRMED"
                    if needs_termination
                    else "DISPATCH_READY"
                ),
                next_signal_key=action.idempotency_key,
                next_signal_deadline=now + timedelta(minutes=1),
                recovery_action={
                    "action": "CONFIRM_TERMINATION"
                    if needs_termination
                    else "REDISPATCH"
                },
            )
        if action.kind is ActionKind.ESCALATE_RECOVERY:
            return self._base(
                item,
                stage="PARKED",
                wait_reason="HUMAN_DECISION",
                custodian_type="HUMAN",
                custodian_id="scott",
                lease_token=str(uuid4()),
                lease_epoch_delta=1,
                lease_expires_at=now + timedelta(days=1),
                next_signal_type="RECOVERY_DECISION",
                next_signal_key=action.idempotency_key,
                next_signal_deadline=now + timedelta(hours=4),
                recovery_action={"action": "REMIND_ANDON"},
            )
        raise UnsupportedShadowAction(action.kind.value)

    @staticmethod
    def _base(item: Mapping[str, Any], **changes: Any) -> TransitionPatch:
        values = {
            "stage": item["stage"],
            "wait_reason": item["wait_reason"],
            "custodian_type": item["custodian_type"],
            "custodian_id": item["custodian_id"],
            "lease_token": str(item["lease_token"]),
            "lease_epoch_delta": 0,
            "lease_expires_at": item["lease_expires_at"],
            "next_signal_type": item["next_signal_type"],
            "next_signal_key": item.get("next_signal_key") or "phase1",
            "next_signal_deadline": item["next_signal_deadline"],
            "recovery_action": item["recovery_action"],
            "recovery_attempt_delta": 0,
        }
        values.update(changes)
        return TransitionPatch(**values)
