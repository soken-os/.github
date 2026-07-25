"""Read-only performance metrics, derived entirely from the existing ledger.

Scott's requirement: track everything entering CEC and measure performance
across every program, the same way liveness is already tracked. This is
deliberately NOT a new instrumentation layer -- `cec.work_items` and the
append-only `cec.events` ledger already record everything needed:

  * lead time            = completed_at - created_at
  * per-stage dwell       = consecutive event.observed_at deltas per work_item
  * estimate accuracy     = actual duration vs estimated_duration_seconds
  * first-pass yield      = COMPLETE with recovery_attempts = 0
  * park/recovery rate    = PARKED transitions per item
  * throughput            = COMPLETEs per day, grouped by program
  * custody churn         = lease_epoch at completion (higher = rockier build)

Every function here is a SELECT. Nothing writes to work_items or events, and
nothing touches custody, leases, or dispatch -- this is the reporting layer the
"CEC as a tracking system" requirement calls for, built alongside the control
plane rather than inside it.
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..phase0.bootstrap import database_url


@dataclass(frozen=True)
class ProgramMetrics:
    program: str
    completed_count: int
    parked_count: int
    in_flight_count: int
    first_pass_yield: float | None  # fraction of completions with 0 recovery attempts
    median_lead_time_seconds: float | None
    p90_lead_time_seconds: float | None
    mean_estimate_ratio: float | None  # actual / estimated; >1 means builds run long
    mean_lease_epoch_at_completion: float | None  # custody churn


def _connect():
    return psycopg.connect(database_url(), row_factory=dict_row)


def program_metrics(
    program: str, *, task_class: str = "CIRCUIT_BUILD"
) -> ProgramMetrics:
    """Roll-up metrics for one program, computed directly from work_items."""

    with _connect() as conn:
        counts = conn.execute(
            """SELECT
                count(*) FILTER (WHERE stage = 'COMPLETE') AS completed,
                count(*) FILTER (WHERE stage = 'PARKED') AS parked,
                count(*) FILTER (WHERE stage NOT IN ('COMPLETE', 'CANCELLED')) AS in_flight
            FROM cec.work_items
            WHERE program = %s AND task_class = %s""",
            (program, task_class),
        ).fetchone()

        completions = conn.execute(
            """SELECT
                EXTRACT(EPOCH FROM (completed_at - created_at)) AS lead_time_seconds,
                recovery_attempts,
                lease_epoch,
                estimated_duration_seconds
            FROM cec.work_items
            WHERE program = %s AND task_class = %s AND stage = 'COMPLETE'
            ORDER BY completed_at""",
            (program, task_class),
        ).fetchall()

    # EXTRACT(EPOCH ...) comes back as Decimal; normalize to float immediately so
    # every downstream computation is plain arithmetic.
    lead_times = sorted(float(row["lead_time_seconds"]) for row in completions)
    first_pass = [row["recovery_attempts"] == 0 for row in completions]
    estimate_ratios = [
        float(row["lead_time_seconds"]) / row["estimated_duration_seconds"]
        for row in completions
        if row["estimated_duration_seconds"]
    ]
    epochs = [float(row["lease_epoch"]) for row in completions]

    return ProgramMetrics(
        program=program,
        completed_count=int(counts["completed"]),
        parked_count=int(counts["parked"]),
        in_flight_count=int(counts["in_flight"]),
        first_pass_yield=(sum(first_pass) / len(first_pass)) if first_pass else None,
        median_lead_time_seconds=_percentile(lead_times, 0.5),
        p90_lead_time_seconds=_percentile(lead_times, 0.9),
        mean_estimate_ratio=(
            sum(estimate_ratios) / len(estimate_ratios) if estimate_ratios else None
        ),
        mean_lease_epoch_at_completion=(sum(epochs) / len(epochs)) if epochs else None,
    )


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[index]


def all_programs(*, task_class: str = "CIRCUIT_BUILD") -> list[str]:
    """Every program with at least one item — the roll-up's iteration set."""

    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT program FROM cec.work_items WHERE task_class = %s ORDER BY program",
            (task_class,),
        ).fetchall()
    return [str(row["program"]) for row in rows]


def stage_dwell_seconds(work_item_id: str) -> dict[str, float]:
    """Time spent in each stage for one item, from the event ledger.

    Consecutive events on the same item bound each stage's dwell: the gap
    between one event's observed_at and the next is time spent in the state the
    first event transitioned INTO. The final open interval (last event -> now,
    or -> completed_at if terminal) is included so an item still executing shows
    dwell in its current stage, not just its closed ones.
    """

    with _connect() as conn:
        events = conn.execute(
            """SELECT event_type, observed_at FROM cec.events
            WHERE work_item_id = %s ORDER BY observed_at""",
            (work_item_id,),
        ).fetchall()
        item = conn.execute(
            "SELECT stage, completed_at FROM cec.work_items WHERE id = %s",
            (work_item_id,),
        ).fetchone()

    if not events:
        return {}

    dwell: dict[str, float] = {}
    for current, following in itertools.pairwise(events):
        stage = str(current["event_type"])
        seconds = (following["observed_at"] - current["observed_at"]).total_seconds()
        dwell[stage] = dwell.get(stage, 0.0) + seconds

    last = events[-1]
    end = (item["completed_at"] if item else None) or datetime.now(
        last["observed_at"].tzinfo
    )
    stage = str(last["event_type"])
    dwell[stage] = dwell.get(stage, 0.0) + (end - last["observed_at"]).total_seconds()
    return dwell


def executor_rollup(*, task_class: str = "CIRCUIT_BUILD") -> dict[str, Any]:
    """The Executor's global view: every program's metrics in one call.

    This is the read-only counterpart to routing: routing decided WHICH
    controller a build goes to; this answers "how is everything doing," across
    every program at once, from data every controller already writes.
    """

    programs = all_programs(task_class=task_class)
    return {
        "generated_for_task_class": task_class,
        "programs": {
            program: asdict(program_metrics(program, task_class=task_class))
            for program in programs
        },
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--program", help="report one program; omit for the full roll-up"
    )
    result.add_argument("--task-class", default="CIRCUIT_BUILD")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.program:
        print(
            json.dumps(
                asdict(program_metrics(args.program, task_class=args.task_class))
            )
        )
    else:
        print(json.dumps(executor_rollup(task_class=args.task_class)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
