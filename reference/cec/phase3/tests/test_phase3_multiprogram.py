"""Multi-program routing: many projects share one registry without colliding.

The goal is that ANY project's build routes through CEC — the roofing SaaS, the
executor, anything — with one controller per program. Two properties make that
safe over a shared registry:

  (1) a controller only picks up its OWN program's items (otherwise every
      controller dispatches every item — the same work run twice, in the wrong
      repo);
  (2) a controller handed another program's item refuses it rather than acting
      on it with its own repo/worktree/sandbox.

These are pure-logic tests: no Postgres, no worker, no Mac.
"""

from __future__ import annotations

from reference.cec.phase3.packet import (
    PACKET_SCHEMA,
    PROGRAM_CEC,
    build_packet,
    p3_packet,
)

from jsonschema import Draft202012Validator


class _ProgramScopedController:
    """The exact due_items filter + refusal predicate, over an in-memory registry.

    Mirrors BootstrapController's SQL/guard without needing a live database: the
    filter is `program = self.program` and the guard is `item.program != program
    -> refuse`.
    """

    def __init__(self, program: str, rows: list[dict]) -> None:
        self.program = program
        self.rows = rows

    def due_items(self) -> list[str]:
        return [
            str(row["id"])
            for row in self.rows
            if row["stage"] not in {"COMPLETE", "CANCELLED"}
            and row["task_class"] == "CIRCUIT_BUILD"
            and row["program"] == self.program
            and "starting_ref" in row["work_packet"]
        ]

    def reconcile_once(self, work_item_id: str) -> str:
        item = next(row for row in self.rows if str(row["id"]) == work_item_id)
        if str(item["program"]) != self.program:
            return f"NOT_MY_PROGRAM:{item['program']}"
        return "RECONCILED"


def _row(item_id: str, program: str, stage: str = "READY") -> dict:
    return {
        "id": item_id,
        "program": program,
        "stage": stage,
        "task_class": "CIRCUIT_BUILD",
        "work_packet": {"starting_ref": "a" * 40},
    }


def _registry() -> list[dict]:
    # Three programs sharing one registry, the target state: CEC's own build, the
    # roofing SaaS, and the executor.
    return [
        _row("cec-1", PROGRAM_CEC),
        _row("roof-1", "carlson-roofing"),
        _row("roof-2", "carlson-roofing"),
        _row("exec-1", "executor"),
        _row("cec-done", PROGRAM_CEC, stage="COMPLETE"),
    ]


def test_each_controller_claims_only_its_own_program() -> None:
    rows = _registry()

    assert _ProgramScopedController(PROGRAM_CEC, rows).due_items() == ["cec-1"]
    assert _ProgramScopedController("carlson-roofing", rows).due_items() == [
        "roof-1",
        "roof-2",
    ]
    assert _ProgramScopedController("executor", rows).due_items() == ["exec-1"]


def test_no_item_is_dispatched_by_two_controllers() -> None:
    # The collision the program filter exists to prevent: the union of every
    # controller's queue must contain each item at most once.
    rows = _registry()
    programs = {row["program"] for row in rows}
    claimed = [
        item_id
        for program in programs
        for item_id in _ProgramScopedController(program, rows).due_items()
    ]

    assert len(claimed) == len(set(claimed)), "an item was claimed by two controllers"
    # ...and every non-terminal item is claimed by exactly one controller: scoping
    # must not silently orphan work either.
    assert set(claimed) == {"cec-1", "roof-1", "roof-2", "exec-1"}


def test_controller_refuses_another_programs_item() -> None:
    rows = _registry()
    controller = _ProgramScopedController("carlson-roofing", rows)

    # Handed CEC's item directly (not via due_items) it must refuse, not act with
    # the roofing repo/worktree/sandbox.
    assert controller.reconcile_once("cec-1") == "NOT_MY_PROGRAM:CEC"
    assert controller.reconcile_once("roof-1") == "RECONCILED"


def test_generic_packet_validates_for_an_arbitrary_program(tmp_path) -> None:
    # Onboarding a new project is a call, not a new hand-authored function.
    packet = build_packet(
        program="carlson-roofing",
        objective="Add the roof-pitch multiplier to the estimating engine.",
        allowed_paths=["src/estimating/pitch.py", "tests/test_pitch.py"],
        artifact_path=tmp_path / "test-output.txt",
        diff_artifact_path=tmp_path / "change.diff",
        starting_ref="b" * 40,
    )

    Draft202012Validator(PACKET_SCHEMA).validate(packet)
    assert packet["program"] == "carlson-roofing"
    assert packet["starting_ref"] == "b" * 40
    assert packet["allowed_paths"] == ["src/estimating/pitch.py", "tests/test_pitch.py"]
    # The evidence contract travels with every generic packet, so a new program
    # cannot be onboarded without the verify-don't-trust requirements.
    assert "new_file" in packet["objective"]
    assert "diff_sha256" in packet["objective"]


def test_existing_cec_packets_declare_their_program() -> None:
    # The self-describing field must be present on CEC's own packets too, or the
    # refusal guard has nothing to check against.
    assert p3_packet()["program"] == PROGRAM_CEC
