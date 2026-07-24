"""Requeue builds a valid READY re-dispatch patch for a reclaimed item."""

from __future__ import annotations

from datetime import UTC, datetime

from reference.cec.phase3.requeue import requeue_patch


def _parked_item():
    return {"id": "phase3-p3-notification-tick", "version": 151, "stage": "PARKED"}


def test_requeue_patch_arms_ready_with_fresh_future_lease():
    now = datetime.now(UTC)
    patch = requeue_patch(_parked_item(), now)
    assert patch.stage == "READY"
    assert patch.wait_reason == "NONE"
    assert patch.custodian_type == "CONTROLLER"
    # Fresh lease fences any stale worker; deadlines future so the row is writable
    # under continuation_deadlines_valid.
    assert patch.lease_epoch_delta == 1
    assert patch.lease_expires_at > now
    assert patch.next_signal_deadline >= now
    assert patch.next_signal_type == "DISPATCH_READY"
    # A human retry is not an automatic recovery attempt.
    assert patch.recovery_attempt_delta == 0


def test_requeue_patch_uses_distinct_tokens_per_call():
    now = datetime.now(UTC)
    a = requeue_patch(_parked_item(), now)
    b = requeue_patch(_parked_item(), now)
    assert a.lease_token != b.lease_token
