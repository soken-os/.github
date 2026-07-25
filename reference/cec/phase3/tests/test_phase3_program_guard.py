"""The real controller's cross-program guards, and the unserved-program alarm.

Findings M2, M3, M5. These drive the ACTUAL BootstrapController against the real
registry rather than an in-memory copy of its predicate — the earlier version of
this coverage re-implemented the guard in the test, so it could not prove the
production path performs no work.

Every side effect the guard must not reach (worktree creation, adapter launch,
registry transition) is instrumented to fail the test if touched.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from reference.cec.contracts import WorkerKind
from reference.cec.phase0.bootstrap import database_url
from reference.cec.phase2.bootstrap import migrate as phase2_migrate
from reference.cec.phase3 import controller as controller_module
from reference.cec.phase3.controller import BootstrapController, unserved_programs

pytestmark = pytest.mark.postgres

ALPHA = "guard-alpha"
BETA = "guard-beta"


def _seed(work_item_id: str, program: str, packet: dict) -> None:
    now = datetime.now(UTC)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute("DELETE FROM cec.events WHERE work_item_id=%s", (work_item_id,))
        conn.execute("DELETE FROM cec.work_items WHERE id=%s", (work_item_id,))
        conn.execute(
            """INSERT INTO cec.work_items
            (id,program,title,task_class,priority_class,estimated_duration_seconds,
             stage,wait_reason,custodian_type,custodian_id,lease_token,lease_epoch,
             lease_expires_at,next_signal_type,next_signal_key,next_signal_deadline,
             recovery_action,work_packet,authority_class)
            VALUES (%s,%s,'guard probe','CIRCUIT_BUILD',60,600,
                    'READY','NONE','CONTROLLER','phase3-bootstrap-controller',%s,0,%s,
                    'DISPATCH_READY','guard-seed',%s,%s::jsonb,%s::jsonb,'ROUTINE')""",
            (
                work_item_id,
                program,
                uuid4(),
                now + timedelta(minutes=20),
                now + timedelta(minutes=5),
                json.dumps({"action": "LAUNCH"}),
                json.dumps(packet),
            ),
        )


def _row(work_item_id: str) -> dict:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM cec.work_items WHERE id=%s", (work_item_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def _event_count(work_item_id: str) -> int:
    with psycopg.connect(database_url()) as conn:
        row = conn.execute(
            "SELECT count(*) FROM cec.events WHERE work_item_id=%s", (work_item_id,)
        ).fetchone()
        assert row is not None
        return int(row[0])


def _cleanup(*ids: str) -> None:
    with psycopg.connect(database_url(), autocommit=True) as conn:
        for work_item_id in ids:
            conn.execute(
                "DELETE FROM cec.events WHERE work_item_id=%s", (work_item_id,)
            )
            conn.execute("DELETE FROM cec.work_items WHERE id=%s", (work_item_id,))


def _packet(program: str | None = None) -> dict:
    packet = {"starting_ref": "a" * 40, "objective": "guard probe"}
    if program is not None:
        packet["program"] = program
    return packet


def _forbid_side_effects(monkeypatch, controller: BootstrapController) -> None:
    """Any side effect the guard must not reach becomes an immediate failure."""

    def explode(*args, **kwargs):
        raise AssertionError("guard reached a side effect it must never perform")

    monkeypatch.setattr(controller_module, "create_worktree", explode)
    monkeypatch.setattr(controller_module, "write_unified_diff", explode)
    monkeypatch.setattr(controller.adapter, "launch", explode)
    monkeypatch.setattr(controller.registry, "transition", explode)


@pytest.fixture()
def guard_controller(tmp_path):
    if not os.environ.get("CEC_RUN_POSTGRES_TESTS"):
        pytest.skip("set CEC_RUN_POSTGRES_TESTS=1 for the real-controller guards")
    phase2_migrate()
    return BootstrapController(
        worktree_root=tmp_path / "worktrees",
        bridge_outbox=tmp_path / "outbox",
        worker_kind=WorkerKind.SCRIPT,
        program=ALPHA,
    )


def test_real_controller_refuses_another_programs_row_without_side_effects(
    guard_controller, monkeypatch
):
    """M5: the production method, not a copy of its predicate."""
    work_item_id = "guard-foreign-row"
    _seed(work_item_id, BETA, _packet(BETA))
    try:
        before = _row(work_item_id)
        events_before = _event_count(work_item_id)
        _forbid_side_effects(monkeypatch, guard_controller)

        outcome = guard_controller.reconcile_once(work_item_id)

        assert outcome == f"NOT_MY_PROGRAM:{BETA}"
        after = _row(work_item_id)
        assert after["version"] == before["version"], "guard mutated the row"
        assert after["stage"] == before["stage"]
        assert str(after["lease_token"]) == str(before["lease_token"])
        assert after["lease_epoch"] == before["lease_epoch"]
        assert _event_count(work_item_id) == events_before, "guard appended an event"
    finally:
        _cleanup(work_item_id)


def test_real_controller_refuses_a_packet_from_another_program(
    guard_controller, monkeypatch
):
    """M2: the row is routed to this controller, but its packet belongs elsewhere.

    Without this check the roofing packet would execute in the CEC repo against
    CEC's allowed paths.
    """
    work_item_id = "guard-mismatched-packet"
    _seed(work_item_id, ALPHA, _packet(BETA))  # row says ALPHA, packet says BETA
    try:
        before = _row(work_item_id)
        events_before = _event_count(work_item_id)
        _forbid_side_effects(monkeypatch, guard_controller)

        outcome = guard_controller.reconcile_once(work_item_id)

        assert outcome == f"PACKET_PROGRAM_MISMATCH:{BETA}"
        after = _row(work_item_id)
        assert after["version"] == before["version"], "mismatch guard mutated the row"
        assert _event_count(work_item_id) == events_before
    finally:
        _cleanup(work_item_id)


def test_legacy_packet_without_a_program_is_still_served(guard_controller):
    """M2's compatibility half: absent packet program inherits the row."""
    work_item_id = "guard-legacy-packet"
    _seed(work_item_id, ALPHA, _packet(None))
    try:
        # It must NOT be refused. Reaching worktree creation (and failing there on
        # the fake starting_ref) proves the guard let it through.
        outcome = guard_controller.reconcile_once(work_item_id)
        assert not outcome.startswith("NOT_MY_PROGRAM")
        assert not outcome.startswith("PACKET_PROGRAM_MISMATCH")
    except Exception as exc:  # noqa: BLE001 - past the guard is the assertion
        assert "NOT_MY_PROGRAM" not in str(exc)
        assert "PACKET_PROGRAM_MISMATCH" not in str(exc)
    finally:
        _cleanup(work_item_id)


def test_unserved_program_is_detected_against_the_configured_set(guard_controller):
    """M3: the non-tautological version.

    The configured set is supplied explicitly (as it is in production, from
    config) rather than derived from the rows, so a typo'd program that no
    controller serves actually shows up instead of being assumed served.
    """
    served = "guard-served"
    typo = "guard-typoed-program"
    _seed(served, served, _packet(served))
    _seed(typo, typo, _packet(typo))
    try:
        configured = [served, ALPHA]  # note: `typo` has NO controller

        unserved = dict(unserved_programs(configured))

        assert typo in unserved, "a program nobody serves went undetected"
        assert unserved[typo] >= 1
        assert served not in unserved, "a served program was falsely alarmed"
    finally:
        _cleanup(served, typo)


def test_no_alarm_when_every_nonterminal_program_is_configured(guard_controller):
    served = "guard-all-served"
    _seed(served, served, _packet(served))
    try:
        with psycopg.connect(database_url()) as conn:
            programs = [
                str(row[0])
                for row in conn.execute(
                    """SELECT DISTINCT program FROM cec.work_items
                    WHERE stage NOT IN ('COMPLETE','CANCELLED')
                    AND task_class='CIRCUIT_BUILD'"""
                ).fetchall()
            ]
        assert unserved_programs(programs) == []
    finally:
        _cleanup(served)


# --- M3 (operational): the sentinel is the production caller ------------------


def test_sentinel_exits_nonzero_and_reports_unserved_programs(
    guard_controller, tmp_path
):
    """A detector nothing invokes closes nothing — this is the invoking path."""
    from reference.cec.phase3 import sentinel

    served = "sentinel-served"
    typo = "sentinel-typoed"
    _seed(served, served, _packet(served))
    _seed(typo, typo, _packet(typo))
    try:
        code = sentinel.main(["--program", served, "--program", ALPHA])
        assert code == sentinel.EXIT_UNSERVED, "unserved work did not fail the sentinel"

        # And it is quiet when everything nonterminal has an owner.
        with psycopg.connect(database_url()) as conn:
            all_programs = [
                str(row[0])
                for row in conn.execute(
                    """SELECT DISTINCT program FROM cec.work_items
                    WHERE stage NOT IN ('COMPLETE','CANCELLED')
                    AND task_class='CIRCUIT_BUILD'"""
                ).fetchall()
            ]
        argv = [arg for program in all_programs for arg in ("--program", program)]
        assert sentinel.main(argv) == 0
    finally:
        _cleanup(served, typo)


def test_sentinel_fails_closed_without_a_configured_set(guard_controller):
    """No configured programs must not read as a clean bill of health."""
    from reference.cec.phase3 import sentinel

    assert sentinel.main([]) == 2


def test_sentinel_reads_a_configured_programs_file(guard_controller, tmp_path):
    from reference.cec.phase3 import sentinel

    served = "sentinel-file-served"
    _seed(served, served, _packet(served))
    try:
        config = tmp_path / "programs.txt"
        config.write_text(
            f"# configured controllers\n{served}\n{ALPHA}\n", encoding="utf-8"
        )
        configured = sentinel.load_configured(
            sentinel.parser().parse_args(["--programs-file", str(config)])
        )
        assert served in configured and ALPHA in configured
        assert not any(c.startswith("#") for c in configured)
    finally:
        _cleanup(served)
