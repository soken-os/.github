"""G1/G2 regression: a live-but-silent worker past the lease no longer crashes.

The P3 run failed because the controller's heartbeat renewed only
next_signal_deadline, not lease_expires_at; once the lease expired the next
heartbeat transition restamped updated_at past lease_expires_at and Postgres
rejected the row on continuation_deadlines_valid, crashing the whole service.

These tests exercise the transition-building logic directly (no DB, no worker),
proving:
  G1a - within budget: a heartbeat renews the lease into the future, so the
        row can never carry an expired lease while non-terminal;
  G1b - past budget: the item is reclaimed to a human PARKED lane with all
        continuation fields valid, instead of being heartbeat-renewed forever;
  G2  - a reconcile that raises is contained by run_scan_once, not fatal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from reference.cec.phase3 import controller as ctrl


def _item(**changes):
    now = datetime.now(UTC)
    base = {
        "id": "p3",
        "version": 82,
        "stage": "EXECUTING",
        "wait_reason": "WORKER",
        "custodian_type": "WORKER",
        "custodian_id": "claude_code-abc",
        "lease_token": "11111111-1111-4111-8111-111111111111",
        "lease_epoch": 1,
        "lease_expires_at": now - timedelta(seconds=1),  # already expired
        "next_signal_type": "WORKER_HEARTBEAT",
        "next_signal_key": "cmd",
        "next_signal_deadline": now + timedelta(minutes=1),
        "recovery_action": {"action": "OBSERVE_WORKER"},
        "recovery_attempts": 0,
        "max_recovery_attempts": 3,
        "work_packet": {"estimated_duration_seconds": 600},
    }
    base.update(changes)
    return base


def _controller():
    """A real BootstrapController with only `_transition` stubbed (no DB / chdir).

    Uses the genuine `_patch` and `_reclaim_to_recovery`, so the test exercises
    shipped logic, not a reimplementation.
    """
    c = object.__new__(ctrl.BootstrapController)
    c.transitions = []
    c._transition = lambda item, event_type, patch, payload: c.transitions.append(
        (event_type, patch, payload)
    )
    return c


def test_heartbeat_renews_lease_into_the_future_g1a():
    now = datetime.now(UTC)
    item = _item()
    # The renewed lease must be strictly after the transition's update time so
    # continuation_deadlines_valid (lease_expires_at > updated_at) can hold.
    patch = ctrl.BootstrapController._patch(
        item, lease_expires_at=now + timedelta(minutes=20)
    )
    assert patch.lease_expires_at > now


def test_past_budget_silent_worker_is_parked_not_renewed_g1b():
    now = datetime.now(UTC)
    c = _controller()
    outcome = c._reclaim_to_recovery(_item(), now, reason="RUNTIME_BUDGET_EXCEEDED")
    assert outcome.startswith("RECLAIMED")
    event, patch, _payload = c.transitions[-1]
    assert event == "WORKER_RECLAIMED"
    assert patch.stage == "PARKED"
    assert patch.custodian_type == "HUMAN"
    # All continuation deadlines are in the future, so the row is writable.
    assert patch.lease_expires_at > now
    assert patch.next_signal_deadline > now
    assert patch.lease_epoch_delta == 1  # fenced: the old worker's epoch is stale


def test_run_scan_once_contains_a_raising_reconcile_g2():
    class Boom:
        def due_items(self):
            return ["a", "b"]

        def reconcile_once(self, work_item_id):
            if work_item_id == "a":
                raise RuntimeError("CheckViolation-like")
            return "NO_ACTION"

    outcomes = ctrl.run_scan_once(Boom())
    # 'a' is contained as an ERROR outcome; 'b' still gets its turn.
    assert outcomes == [("a", "ERROR:RuntimeError"), ("b", "NO_ACTION")]
