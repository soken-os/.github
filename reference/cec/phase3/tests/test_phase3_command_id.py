"""Command identity is unique per dispatch attempt (stale-sidecar replay fix).

The requeue bug: a requeued item's fresh dispatch reused the previous attempt's
command_id, so the adapter found the OLD sidecar (<command_id>.process.json) and
replayed the historical worker handle + NEEDS_INPUT result — no new worker ever
launched. Binding the id to the lease epoch (bumped by every requeue/reclaim)
gives each attempt its own sidecar namespace.
"""

from __future__ import annotations

from reference.cec.phase3.controller import _command_id


def _item(lease_epoch):
    return {"id": "phase3-p3-notification-tick", "lease_epoch": lease_epoch}


def test_command_id_changes_with_lease_epoch():
    # Two dispatch attempts of the SAME item at different epochs must produce
    # different command_ids, so the new attempt's sidecar path cannot collide
    # with (and replay) the old attempt's.
    first = _command_id(_item(1))
    later = _command_id(_item(5))
    assert first != later
    assert "epoch-1" in first
    assert "epoch-5" in later


def test_command_id_is_stable_within_one_attempt():
    # Same item, same epoch → same id (idempotent within an attempt: _command()
    # and _handle() must derive the identical sidecar path).
    assert _command_id(_item(3)) == _command_id(_item(3))
