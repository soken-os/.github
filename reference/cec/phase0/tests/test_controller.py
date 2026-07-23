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
