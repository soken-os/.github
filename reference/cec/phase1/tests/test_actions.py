from datetime import UTC, datetime, timedelta
from uuid import UUID

from reference.cec.controller import ActionKind, ReconcileAction
from reference.cec.phase1.actions import ShadowActionExecutor
from reference.cec.phase1.registry import TransitionResult


class CapturingRegistry:
    def transition(self, **kwargs):
        self.kwargs = kwargs
        return TransitionResult(True, False, kwargs["work_item_id"], 8, 1)


def item():
    now = datetime.now(UTC)
    return {
        "id": "151",
        "version": 7,
        "stage": "EXECUTING",
        "wait_reason": "WORKER",
        "custodian_type": "WORKER",
        "custodian_id": "echo-1",
        "lease_token": UUID("15115115-1151-4151-8151-151151151151"),
        "lease_expires_at": now - timedelta(seconds=1),
        "next_signal_type": "WORKER_EXIT",
        "next_signal_key": "echo-1",
        "next_signal_deadline": now - timedelta(seconds=1),
        "recovery_action": {"action": "OBSERVE_WORKER"},
    }


def action(kind, **payload):
    return ReconcileAction(kind, 7, f"151:{kind.value}:7", payload)


def test_hold_increment_is_part_of_atomic_transition_patch():
    registry = CapturingRegistry()
    executor = ShadowActionExecutor(registry)
    result = executor.execute(
        item(),
        action(
            ActionKind.HOLD_UNOBSERVABLE,
            redispatch_allowed=False,
            increment_recovery_attempts=True,
        ),
        observed_at=datetime.now(UTC),
        source_event_id="observer-timeout-1",
    )
    assert result.applied
    patch = registry.kwargs["patch"]
    assert patch.recovery_attempt_delta == 1
    assert patch.custodian_type == "CONTROLLER"
    assert patch.lease_epoch_delta == 1


def test_current_lease_hold_does_not_fence_or_increment():
    registry = CapturingRegistry()
    executor = ShadowActionExecutor(registry)
    executor.execute(
        item(),
        action(ActionKind.HOLD_UNOBSERVABLE, redispatch_allowed=False),
        observed_at=datetime.now(UTC),
        source_event_id="transient-gap-1",
    )
    patch = registry.kwargs["patch"]
    assert patch.recovery_attempt_delta == 0
    assert patch.lease_epoch_delta == 0
    assert patch.custodian_type == "WORKER"


def test_escalation_does_not_increment_past_ceiling():
    registry = CapturingRegistry()
    executor = ShadowActionExecutor(registry)
    executor.execute(
        item(),
        action(ActionKind.ESCALATE_RECOVERY, redispatch_allowed=False),
        observed_at=datetime.now(UTC),
        source_event_id="ceiling-1",
    )
    patch = registry.kwargs["patch"]
    assert patch.recovery_attempt_delta == 0
    assert patch.stage == "PARKED"
    assert patch.wait_reason == "HUMAN_DECISION"


def test_live_worker_reclaim_requires_termination_confirmation_signal():
    registry = CapturingRegistry()
    executor = ShadowActionExecutor(registry)
    executor.execute(
        item(),
        action(
            ActionKind.RECLAIM_EXPIRED,
            terminate_worker=True,
            redispatch_allowed=False,
        ),
        observed_at=datetime.now(UTC),
        source_event_id="reclaim-live-1",
    )
    patch = registry.kwargs["patch"]
    assert patch.next_signal_type == "WORKER_TERMINATION_CONFIRMED"
    assert patch.recovery_action == {"action": "CONFIRM_TERMINATION"}


def test_renewal_preserves_worker_fence():
    registry = CapturingRegistry()
    executor = ShadowActionExecutor(registry)
    executor.execute(
        item(),
        action(ActionKind.RENEW_LEASE),
        observed_at=datetime.now(UTC),
        source_event_id="heartbeat-1",
    )
    patch = registry.kwargs["patch"]
    assert patch.lease_epoch_delta == 0
    assert patch.custodian_type == "WORKER"
    assert patch.lease_token == "15115115-1151-4151-8151-151151151151"
