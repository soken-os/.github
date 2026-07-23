from datetime import UTC, datetime, timedelta

from reference.cec.contracts import WorkerObservation, WorkerProcessState
from reference.cec.controller import (
    ActionKind,
    Observation,
    decide,
    expired_lease_action,
)


def item(**changes):
    base = {
        "id": "151",
        "version": 3,
        "stage": "EXECUTING",
        "wait_reason": "WORKER",
        "lease_epoch": 2,
        "lease_expires_at": datetime.now(UTC) - timedelta(seconds=1),
        "recovery_attempts": 0,
        "max_recovery_attempts": 3,
    }
    base.update(changes)
    return base


def obs(state=None, errors=()):
    worker = (
        None
        if state is None
        else WorkerObservation(state, datetime.now(UTC), None, False, None)
    )
    return Observation("151", datetime.now(UTC), worker=worker, observer_errors=errors)


def test_observer_failure_never_redispatches():
    action = expired_lease_action(item(), obs(errors=("process observer timeout",)))
    assert action.kind is ActionKind.HOLD_UNOBSERVABLE
    assert action.payload["redispatch_allowed"] is False


def test_confirmed_dead_can_be_reclaimed():
    for state in (WorkerProcessState.EXITED, WorkerProcessState.MISSING):
        action = expired_lease_action(item(), obs(state))
        assert action.kind is ActionKind.RECLAIM_EXPIRED
        assert action.payload["redispatch_allowed"] is True


def test_live_expired_worker_is_terminated_before_redispatch():
    action = decide(item(), obs(WorkerProcessState.RUNNING))
    assert action.kind is ActionKind.RECLAIM_EXPIRED
    assert action.payload == {
        "terminate_worker": True,
        "redispatch_allowed": False,
        "next_custodian": "CONTROLLER",
    }


def test_decide_is_referentially_transparent():
    i, o = item(), obs(errors=("offline",))
    assert decide(i, o) == decide(i, o)


def test_hold_counts_toward_escalation_ceiling():
    # Every hold on an expired lease must carry the increment flag; otherwise a
    # permanently failing observer holds forever and escalation is unreachable.
    action = expired_lease_action(item(), obs(errors=("process observer timeout",)))
    assert action.kind is ActionKind.HOLD_UNOBSERVABLE
    assert action.payload["increment_recovery_attempts"] is True


def test_escalates_at_ceiling_instead_of_holding_forever():
    at_ceiling = item(recovery_attempts=3, max_recovery_attempts=3)
    for observation in (obs(errors=("offline",)), obs()):  # errored / not observed
        action = expired_lease_action(at_ceiling, observation)
        assert action.kind is ActionKind.ESCALATE_RECOVERY
        assert action.payload["redispatch_allowed"] is False
        assert "increment_recovery_attempts" not in action.payload


def test_green_ci_holds_when_merge_authority_is_unknown():
    verifying = item(stage="VERIFYING", wait_reason="CI")
    observation = Observation("151", datetime.now(UTC), ci={"state": "GREEN"}, pr=None)
    action = decide(verifying, observation)
    assert action.kind is ActionKind.HOLD_UNOBSERVABLE
    assert action.payload["redispatch_allowed"] is False


def test_green_ci_requests_decision_when_merge_authority_is_absent():
    verifying = item(stage="VERIFYING", wait_reason="CI")
    observation = Observation(
        "151",
        datetime.now(UTC),
        ci={"state": "GREEN"},
        pr={"merge_authorized": False},
    )
    assert decide(verifying, observation).kind is ActionKind.REQUEST_DECISION
