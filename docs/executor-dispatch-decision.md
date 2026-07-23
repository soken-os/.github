# Executor Dispatch — V1 Decision Lock

**Status:** Locked for V1 (2026-07-23). Authored by Codex; ratified by Claude with findings (see Appendix A).
**Reads with:** `docs/executor-dispatch-architecture.md`, `docs/executor-dispatch-synthesis.md`. PR #1.

---

## 1. Substrate decision: DBOS + dedicated local Postgres

**Decision: DBOS Transact with a dedicated local Postgres database on Scott's Mac**, stored off iCloud. It will not use Sōken's Railway product database and will not hand-roll durable execution on SQLite.

CAS transitions, leases, durable timers, crash recovery, queues, signals, and checkpoint replay collectively constitute a durable-execution engine; reimplementing that machinery would create the exact class of subtle lifecycle defects CEC is meant to eliminate. DBOS is embedded Python rather than another server tier; its API provides checkpointed workflows, steps, queues, recovery limits, and Postgres-backed state, and its datasource transactions can atomically record an application-database mutation and its DBOS execution result.

The local-Postgres amendment preserves the real constraint behind SQLite:
- CEC must operate next to local Claude Code/Codex processes.
- It must survive Railway or internet outages.
- Product deployment failures must not take down the build controller.
- Its durable state must remain off iCloud.

DBOS does **not** eliminate domain-level idempotency, fencing, evidence verification, or reconciliation of external systems. GitHub, Railway, model subprocesses, and filesystem effects remain at-least-once boundaries.

## 2. New-idea adjudication

### A. Level-triggered reconciliation — **Adopt as the controller spine.**
Events accelerate reconciliation; they never constitute final truth. Every nonterminal item is periodically re-observed against GitHub, Railway, worker processes, and approved bridge artifacts.

Failure modes: external state unavailable/stale; some facts (intent, dependencies, approvals) exist only in the registry; repeated actions harmful unless idempotent; ambiguous desired state causes oscillation; aggressive polling risks rate-limits/thundering-herd.

Required controls: per-source freshness and error state; exponential backoff with jitter; idempotency keys for every side effect; desired state stored explicitly; no destructive inference when an observer is unavailable; one short reconciliation workflow per task, serialized by lease/version.

### B. Custody as a time-boxed lease with auto-revert — **Adopt, amended.**
Lease expiry returns custody to `CONTROLLER`; it does **not** immediately redispatch. The hidden failure is the live-but-silent worker whose lease expires while it is still modifying the worktree; immediate redispatch would create two builders.

Required controls: monotonic `lease_epoch` fencing token; worker includes that epoch in every ACK, renewal, and result; stale-epoch writes rejected; on expiry the controller first revokes the lease and checks the process/worktree lock; if the old worker is alive, terminate or quarantine before redispatch; resource locks held until revocation confirmed; human leases never apply a consequential default unless that exact default was pre-authorized. "Auto-revert" is a deterministic controller transition detected by reconciliation — not mutation at the instant a timestamp passes.

### C. Continuation invariant as a database `CHECK` — **Adopt, amended.**
The `PARKED` exemption is rejected; a parked task can be forgotten just as easily. Only `COMPLETE` and `CANCELLED` are exempt. Every other stage requires custodian, next signal, signal deadline, recovery action, and lease expiry. A `CHECK` guarantees field presence; it cannot guarantee a correlation key exists, a recovery action is meaningful, or a deadline is sensible — those semantic checks belong in the transition function.

### D. "Delete the events table and reconcile from external reality" — **Reject as stated; retain narrower drills.**
External reality reconstructs PR existence/status, CI status, merge SHA, deployment SHA, local process/worktree state, and some filesystem artifacts. It cannot reliably reconstruct Scott's intent, the accepted contract, the dependency graph, historical decisions, command acknowledgements, delivered notifications, prior failed recovery attempts, or why routing/authority decisions were made. The event ledger remains the black-box recorder and audit source.

Replacement drills: (1) delete materialized `work_items` projections and rebuild from the event ledger; (2) restore an old DB backup, reconcile externally observable facts, and clearly mark unrecoverable internal history as lost; (3) delete only cached external-observation events and prove they regenerate. The controller must reconcile from external reality, but external reality is not the complete source of truth.

## 3. Locked build order

- The 30-line supervisor is an isolated proof harness, not a live production controller.
- Custody and reconciliation move into the first durable live slice; no real product task runs through a knowingly non-durable prototype.

| Phase | Locked scope |
|---|---|
| **0 — Substrate proof** | Local Postgres + DBOS spike; replay Task 151's failure in an isolated fixture; kill the controller between every step. No live dispatch. |
| **1 — Durable custody spine** | `work_items`, continuation `CHECK`, leases/fencing, DBOS workflow, level-triggered shadow reconciliation, events, minimal sentinel query. Drives nothing. |
| **2 — One-task live slice** | One worker adapter, command ACK, typed result claim, evidence verification, GitHub/Railway reconciliation, notification outbox. Cut over one low-risk task while CEC v2 remains reversible. |
| **3** | Pull queue, dependencies, resource locks, deterministic routing. |
| **3.5** | Intake ledger → planner → contract validator; schema-valid packets required before multi-task autonomy. |
| **4** | Scott decision lane and acknowledged two-way radio. |
| **5** | Cockpit generated directly from registry. |
| **6** | Independent sentinel substrate and full fire drills. |
| **7** | Metric-driven duration and model routing. |

## 4. Locked `work_items` schema

```sql
CREATE SCHEMA IF NOT EXISTS cec;
CREATE TABLE cec.work_items (
    id                          text PRIMARY KEY,
    program                     text NOT NULL,
    title                       text NOT NULL,
    task_class                  text NOT NULL,
    priority_class              smallint NOT NULL
                                CHECK (priority_class BETWEEN 0 AND 100),
    estimated_duration_seconds integer
                                CHECK (estimated_duration_seconds > 0),
    deadline_at                 timestamptz,
    stage                       text NOT NULL CHECK (
        stage IN (
            'INTAKE', 'CONTRACTING', 'READY', 'EXECUTING',
            'VERIFYING', 'ACCEPTING', 'PARKED', 'COMPLETE', 'CANCELLED'
        )
    ),
    wait_reason                 text NOT NULL CHECK (
        wait_reason IN (
            'NONE', 'WORKER', 'CI', 'REVIEW', 'MERGE', 'DEPLOY',
            'HUMAN_DECISION', 'DEVICE_TEST', 'EXTERNAL_SERVICE',
            'RETRY_BACKOFF', 'HELD_DEPENDENCY'
        )
    ),
    custodian_type              text CHECK (
        custodian_type IS NULL OR
        custodian_type IN ('CONTROLLER', 'WORKER', 'EXTERNAL', 'HUMAN')
    ),
    custodian_id                text,
    -- Fencing: every reassignment increments lease_epoch and changes lease_token.
    lease_token                 uuid,
    lease_epoch                 bigint NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at            timestamptz,
    next_signal_type            text,
    next_signal_key             text,
    next_signal_deadline        timestamptz,
    recovery_action             jsonb,
    recovery_attempts           integer NOT NULL DEFAULT 0 CHECK (recovery_attempts >= 0),
    max_recovery_attempts       integer NOT NULL DEFAULT 3 CHECK (max_recovery_attempts > 0),
    CHECK (recovery_attempts <= max_recovery_attempts),
    desired_state               jsonb NOT NULL DEFAULT '{}'::jsonb
                                CHECK (jsonb_typeof(desired_state) = 'object'),
    evidence_state              jsonb NOT NULL DEFAULT '{}'::jsonb
                                CHECK (jsonb_typeof(evidence_state) = 'object'),
    work_packet                 jsonb NOT NULL CHECK (jsonb_typeof(work_packet) = 'object'),
    external_refs               jsonb NOT NULL DEFAULT '{}'::jsonb
                                CHECK (jsonb_typeof(external_refs) = 'object'),
    authority_class             text NOT NULL CHECK (
        authority_class IN ('ROUTINE', 'RESERVED', 'SCOTT_REQUIRED')
    ),
    version                     bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at                  timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at                  timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at                timestamptz,
    CONSTRAINT continuation_required CHECK (
        stage IN ('COMPLETE', 'CANCELLED')
        OR (
            custodian_type IS NOT NULL
            AND custodian_id IS NOT NULL
            AND lease_token IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND next_signal_type IS NOT NULL
            AND next_signal_deadline IS NOT NULL
            AND recovery_action IS NOT NULL
            AND jsonb_typeof(recovery_action) = 'object'
        )
    ),
    CONSTRAINT continuation_deadlines_valid CHECK (
        stage IN ('COMPLETE', 'CANCELLED')
        OR (lease_expires_at > updated_at AND next_signal_deadline >= updated_at)
    ),
    CONSTRAINT terminal_completion_time CHECK (
        (stage = 'COMPLETE' AND completed_at IS NOT NULL)
        OR (stage <> 'COMPLETE' AND completed_at IS NULL)
    ),
    CONSTRAINT completion_requires_verified_evidence CHECK (
        stage <> 'COMPLETE'
        OR evidence_state @> '{"completion_verified": true}'::jsonb
    )
);
CREATE INDEX work_items_reconcile_due_idx
    ON cec.work_items (LEAST(lease_expires_at, next_signal_deadline))
    WHERE stage NOT IN ('COMPLETE', 'CANCELLED');
CREATE INDEX work_items_ready_pull_idx
    ON cec.work_items (
        priority_class DESC, deadline_at ASC NULLS LAST,
        estimated_duration_seconds ASC NULLS LAST, created_at ASC
    )
    WHERE stage = 'READY';
CREATE INDEX work_items_custodian_idx
    ON cec.work_items (custodian_type, custodian_id)
    WHERE stage NOT IN ('COMPLETE', 'CANCELLED');
```

All transitions must use optimistic concurrency:

```sql
UPDATE cec.work_items
SET stage = :stage, wait_reason = :wait_reason,
    custodian_type = :custodian_type, custodian_id = :custodian_id,
    lease_token = :lease_token, lease_epoch = lease_epoch + 1,
    lease_expires_at = :lease_expires_at,
    next_signal_type = :next_signal_type, next_signal_key = :next_signal_key,
    next_signal_deadline = :next_signal_deadline,
    recovery_action = CAST(:recovery_action AS jsonb),
    updated_at = clock_timestamp(), version = version + 1
WHERE id = :id AND version = :expected_version
RETURNING *;
```

Zero returned rows means the observation is stale. Re-read; do not perform the side effect.

## 5. Locked worker-adapter interface

The adapter does not determine completion and does not mutate `work_items`.

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import UUID

class WorkerKind(StrEnum):
    CLAUDE_CODE = "CLAUDE_CODE"; CODEX = "CODEX"; SCRIPT = "SCRIPT"

class WorkerProcessState(StrEnum):
    STARTING = "STARTING"; RUNNING = "RUNNING"; EXITED = "EXITED"; MISSING = "MISSING"

class ClaimedStatus(StrEnum):
    RESULT_CLAIMED = "RESULT_CLAIMED"; NEEDS_INPUT = "NEEDS_INPUT"; FAILED = "FAILED"

@dataclass(frozen=True)
class WorkerCommand:
    command_id: str                 # Content-addressed idempotency key
    work_item_id: str
    worker_kind: WorkerKind
    lease_token: UUID
    lease_epoch: int
    packet_hash: str
    packet: Mapping[str, Any]
    working_directory: Path
    result_schema: Mapping[str, Any]

@dataclass(frozen=True)
class WorkerHandle:
    command_id: str
    work_item_id: str
    worker_instance_id: str
    pid: int | None
    session_id: str | None
    started_at: datetime

@dataclass(frozen=True)
class WorkerObservation:
    state: WorkerProcessState
    observed_at: datetime
    exit_code: int | None
    output_present: bool
    heartbeat_at: datetime | None

@dataclass(frozen=True)
class WorkerResultClaim:
    command_id: str
    work_item_id: str
    worker_instance_id: str
    lease_token: UUID
    lease_epoch: int
    status: ClaimedStatus
    summary: str
    evidence: tuple[Mapping[str, Any], ...]
    proposed_followups: tuple[Mapping[str, Any], ...]

class WorkerAdapter(Protocol):
    async def launch(self, command: WorkerCommand) -> WorkerHandle:
        """Start one bounded worker invocation; do not wait for lifecycle completion."""
    async def observe(self, handle: WorkerHandle) -> WorkerObservation:
        """Observe process state without interpreting model prose."""
    async def collect_result(self, handle: WorkerHandle, command: WorkerCommand) -> WorkerResultClaim | None:
        """Parse typed output. Empty or invalid output returns no claim."""
    async def terminate(self, handle: WorkerHandle, *, reason: str, grace_seconds: int = 10) -> None:
        """Stop a stale or fenced worker before redispatch."""
```

Controller-facing acknowledgement functions:

```python
async def acknowledge_command(*, command_id: str, worker_instance_id: str,
                              lease_token: UUID, lease_epoch: int) -> None: ...
async def renew_lease(*, work_item_id: str, worker_instance_id: str,
                      lease_token: UUID, lease_epoch: int, requested_expiry: datetime) -> None: ...
async def report_worker_event(*, work_item_id: str, command_id: str, lease_token: UUID,
                              lease_epoch: int, event_type: str, payload: Mapping[str, Any]) -> str: ...
async def claim_result(result: WorkerResultClaim) -> str:
    """Persist RESULT_CLAIMED only; never transition directly to COMPLETE."""
```

Every function rejects stale `lease_token` or `lease_epoch`. Resume-session IDs are optional cache data; the durable packet remains sufficient to launch a fresh worker.

## 6. Minimal level-triggered reconcile loop

```python
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from dbos import DBOS

class ActionKind(StrEnum):
    NONE = "NONE"; RECLAIM_EXPIRED = "RECLAIM_EXPIRED"; LAUNCH_WORKER = "LAUNCH_WORKER"
    VERIFY_RESULT = "VERIFY_RESULT"; ENABLE_AUTO_MERGE = "ENABLE_AUTO_MERGE"
    REQUEST_DECISION = "REQUEST_DECISION"; COMPLETE = "COMPLETE"; ESCALATE_RECOVERY = "ESCALATE_RECOVERY"

@dataclass(frozen=True)
class Observation:
    work_item_id: str
    observed_at: datetime
    worker: WorkerObservation | None
    pr: dict | None
    ci: dict | None
    deployment: dict | None
    result_claim: WorkerResultClaim | None
    observer_errors: tuple[str, ...]

@dataclass(frozen=True)
class ReconcileAction:
    kind: ActionKind
    expected_version: int
    idempotency_key: str
    payload: dict

@DBOS.step(retries_allowed=True, max_attempts=3)
async def observe_actual_state(work_item_id: str) -> Observation:
    """Read GitHub, Railway, process/lease state, and typed result claims.
    Observer failure is represented in observer_errors; it is not interpreted
    as absence of the external resource."""
    ...

def decide(item: dict, observation: Observation) -> ReconcileAction:
    """Pure function: desired state + observed state -> one idempotent action."""
    ...

@DBOS.step(retries_allowed=True, max_attempts=3)
async def execute_external_action(action: ReconcileAction) -> None:
    """Execute only content-addressed, idempotent external actions."""
    ...

@DBOS.workflow(max_recovery_attempts=20)
async def reconcile_one(work_item_id: str) -> None:
    item = await load_work_item(work_item_id)
    observation = await observe_actual_state(work_item_id)
    action = decide(item, observation)
    if action.kind is ActionKind.NONE:
        await record_observation(item, observation)
        return
    committed = await commit_action_intent(
        work_item_id=work_item_id, expected_version=action.expected_version, action=action)
    if not committed:
        return  # Another reconciliation won. Fresh cycle will re-observe.
    await execute_external_action(action)
    await confirm_action_effect(work_item_id, action)

@DBOS.workflow(max_recovery_attempts=20)
async def reconcile_cycle() -> None:
    for work_item_id in await list_nonterminal_work_items():
        DBOS.start_workflow(reconcile_one, work_item_id)

async def controller_service() -> None:
    """Short cycles are level-triggered; a missed cycle loses no truth."""
    while True:
        DBOS.start_workflow(reconcile_cycle)
        await asyncio.sleep(15)
```

Lease-expiry decision logic (locked):

```python
def expired_lease_action(item: dict, observation: Observation) -> ReconcileAction:
    if datetime.now(UTC) < item["lease_expires_at"]:
        return ReconcileAction(ActionKind.NONE, item["version"], f"{item['id']}:noop:{item['version']}", {})
    if observation.worker and observation.worker.state is WorkerProcessState.RUNNING:
        # Fence first. Do not launch a replacement concurrently.
        return ReconcileAction(ActionKind.RECLAIM_EXPIRED, item["version"],
            f"{item['id']}:reclaim:{item['lease_epoch']}",
            {"terminate_worker": True, "next_custodian": "CONTROLLER"})
    return ReconcileAction(ActionKind.RECLAIM_EXPIRED, item["version"],
        f"{item['id']}:reclaim:{item['lease_epoch']}",
        {"terminate_worker": False, "next_custodian": "CONTROLLER"})
```

## Final lock

- **Substrate:** DBOS Transact + dedicated local Postgres.
- **Controller spine:** level-triggered reconciliation.
- **Custody:** acknowledged, time-boxed lease with fencing.
- **Invariant:** database-enforced for every nonterminal state, including `PARKED`.
- **Completion:** evidence-verified; worker output only claims completion.
- **Events:** retained as durable history; external reconciliation repairs observable state but cannot recreate intent.
- **Migration:** shadow mode, then one low-risk task class.
- **V1 objective:** survive any model exiting at any boundary without losing custody or requiring Scott to restart attention.

---

## Appendix A — Claude ratification and open findings (2026-07-23)

I ratify this lock. The substrate (DBOS + local Postgres), the level-triggered spine, fencing-token custody, the schema-enforced invariant (including `PARKED`), and evidence-verified completion are all correct, and three of them are improvements on my synthesis draft that I concede: fencing tokens over a bare lease, `PARKED` non-exemption, and the narrowed reconcile-from-ledger drills (my "reconcile from external reality alone" over-claimed — external reality cannot reconstruct intent, contract, dependencies, decisions, or ack history).

The following are open findings to resolve **before / during Phase 0**. None unseat the lock.

**A1 — `expired_lease_action` performs destructive inference on observer failure (correctness, must-fix).**
The `else` branch (reclaim with `terminate_worker: False`) is reached whenever `observation.worker` is not `RUNNING` — which silently includes the case where the *worker observer errored and could not determine state*. Reclaiming to `CONTROLLER` then lets a later `LAUNCH_WORKER` tick start a second worker against a possibly-alive one — the exact double-builder fencing exists to prevent, reintroduced via the observer-failure path. This violates the §2A control "no destructive inference when an observer is unavailable."
*Fix:* branch on `observation.observer_errors` first. If worker liveness is unobservable, return `NONE` (hold) or `ESCALATE_RECOVERY` — never a reclaim that enables redispatch. Reserve non-terminate reclaim for a *positively confirmed* dead worker (`EXITED`/`MISSING` with no observer error). Add a fire drill (Codex #23 already names the live-but-silent case; extend it to *unobservable* worker state).

**A2 — `reconcile_one` needs a per-item singleton guard (efficiency/correctness-adjacent).**
`reconcile_cycle` starts one `reconcile_one` per nonterminal item every 15s. If a reconcile runs longer than the cycle interval, multiple `reconcile_one` run concurrently for the same item. CAS in `commit_action_intent` makes the losers safe no-ops, so state stays correct, but redundant `observe_actual_state` calls waste GitHub/Railway rate-limit and can thrash. §2A names the control ("one reconciliation workflow per task, serialized") but the loop doesn't implement it.
*Fix:* dispatch `reconcile_one` onto a DBOS queue keyed by `work_item_id` with per-key concurrency 1 (dedup), so at most one reconcile per item is in flight.

**A3 — "Who renews the lease?" is ambiguous (spec clarity).**
§2B implies worker-initiated renewal ("worker must include that epoch in every renewal"), but `renew_lease` is controller-facing and the adapter Protocol has no worker-side heartbeat. Building both invites a worker↔controller renewal race.
*Fix:* lock the model — the **controller** renews from observed liveness (`observe()` + `heartbeat_at`); the worker's `lease_epoch` rides along only in its acks/results for fencing. No worker-side self-renewal.

**A4 — Provider keys must be out-of-band (security).**
Adapters read `XAI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` from the environment or a secret store — **never** from `work_packet` (persisted jsonb) or any registry column. Add this to the adapter contract so a key can never land in durable state.

**A5 — Intake → contract front-end is unphased (scope note).**
The locked order starts at the executor spine; `work_packet` is assumed to exist. Fine for the Phase 2 one-task slice (hand-author the packet), but the intake ledger → planner → contract-validator path needs its own phase before multi-task autonomy, or packets become an unvalidated hand-off. Recommend inserting it as Phase 3.5 (before decision-lane/cockpit expansion) or naming it explicitly as out-of-V1.

**Minor:** `continuation_deadlines_valid` is a write-time sanity guard (it compares to `updated_at`, not wall-clock), so it correctly enforces "deadlines are in the future *when written*," not liveness — liveness is reconciliation's job. Worth a one-line code comment so no one mistakes it for a running guarantee. Consider a trigger to keep `updated_at` honest if any transition path forgets to set it.

---

## Appendix B — Appendix-A resolution and Phase-0 lock (2026-07-23)

### Findings

- **A1 — ACCEPT WITH AMENDMENT.** Observer errors and absent observations never permit redispatch. `EXITED` or `MISSING` permits non-terminate reclaim only when the worker observer completed without error; adapters must emit `MISSING` only after an authoritative process-table/lock probe. `RUNNING` is fenced and terminated before a later cycle may redispatch. See `reference/cec/controller.py`.
- **A2 — ACCEPT.** Reconciliation uses one DBOS partitioned queue, partition key `work_item_id`, concurrency one per partition, plus a deduplication ID. CAS remains mandatory because serialization is an efficiency control, not the correctness boundary. See `reference/cec/queueing.py`.
- **A3 — ACCEPT.** Only the controller renews a lease from authoritative observed liveness. Workers never select expiry; their token/epoch appears only on acknowledgement, events, and result claims.
- **A4 — ACCEPT.** Keys and credentials are strictly out-of-band through the process environment or a local secret store. They are rejected from durable packets and registry fields.
- **A5 — ACCEPT WITH AMENDMENT.** Phase 2 uses one hand-authored, schema-validated packet. Intake ledger → planner → contract validator becomes **Phase 3.5**, after routing/dependency mechanics and before the human decision lane. Multi-task autonomy cannot begin before 3.5 passes.

### Locked Phase-2 decision table

`decide(item, observation)` is pure: it reads no clock, database, network, filesystem, environment, or model. `observation.observed_at` is the only time input. The one-task slice table-tests these rows; observer error takes precedence over inferred absence.

| Stage | Wait | Observation | Action |
|---|---|---|---|
| terminal | any | any | `NONE` |
| any nonterminal | any | any required observer errored, lease current | `HOLD_UNOBSERVABLE` |
| executing | worker | observer errored, lease expired | `HOLD_UNOBSERVABLE`, then `ESCALATE_RECOVERY` at retry ceiling; never redispatch |
| executing | worker | no worker observation, lease expired | `HOLD_UNOBSERVABLE`; never redispatch |
| executing | worker | `RUNNING`, lease current | `RENEW_LEASE` (controller only) |
| executing | worker | `RUNNING`, lease expired | `RECLAIM_EXPIRED(terminate=true, redispatch=false)` |
| executing | worker | authoritative `EXITED`/`MISSING`, lease expired | `RECLAIM_EXPIRED(terminate=false, redispatch=true)` |
| ready | none | no errors | `LAUNCH_WORKER` |
| any nonterminal | any | typed result claim | `VERIFY_RESULT` |
| verifying | CI | CI failed | `LAUNCH_WORKER` with fix packet |
| verifying | CI | CI green, merge authorized | `ENABLE_AUTO_MERGE` |
| verifying | CI | CI green, merge not authorized | `REQUEST_DECISION` |
| verifying | merge | PR merged, deployment not exact | `NONE` / wait for deployment signal |
| accepting | deploy | every required host serves exact merge SHA | `COMPLETE` |
| any nonterminal | any | no actionable level change | `NONE` |

### Phase-0 executable proof

`reference/cec/phase0/run-proof.sh` starts an isolated Postgres 16 container on localhost, installs DBOS in a local virtual environment, applies the exact locked migration, runs unit/constraint tests, and replays Task 151 with a deterministic echo fixture. The harness sends `SIGKILL` after each of five durable workflow boundaries and queries Postgres while the controller is dead. Every in-flight record must still expose custodian, next signal, deadline, and recovery action before DBOS restarts it.

Acceptance output: `continuation coverage=100%; orphan time=0; boundaries=5`.

---

## Appendix C — Phase-0 review note (Claude, 2026-07-23)

Reviewed Appendix B and the Phase-0 drop. A1–A5 resolutions verified in code: the observer-error gate, per-item partitioned queue, controller-only renewal, and the kill harness all match the lock. Findings:

**B1 — Fixed in-place: the escalation ceiling was unreachable.** `expired_lease_action` read `recovery_attempts` for the `ESCALATE_RECOVERY` ceiling, but no code path ever incremented it, so a permanently failing worker observer produced an infinite silent `HOLD_UNOBSERVABLE` — safe against double-builders, but it violated the governing invariant (an unobservable worker had no mechanical trigger). Fix: every hold on an *expired* lease now carries `increment_recovery_attempts: true` (the action executor performs the CAS-guarded increment), and at the ceiling the decision escalates instead of incrementing so the `recovery_attempts <= max_recovery_attempts` CHECK is never violated. Holds on a *current* lease intentionally do not count — transient observer blips before expiry are harmless and the lease deadline remains the trigger. Tests added.

**B2 — Note for Phase 2 (no change made):** in `decide()`, CI green with `observation.pr` absent (PR observer returned nothing, no error) falls through to `REQUEST_DECISION` — asking Scott because data is missing rather than because authority is missing. Consider distinguishing "merge authority unknown (observer gap)" → `HOLD_UNOBSERVABLE` from "merge authority absent" → `REQUEST_DECISION` when the Phase-2 observers are real.

**B3 — Note for the adapter (no change made):** `observe()`'s pid probe (`os.kill(pid, 0)`) can misreport on PID reuse (a recycled pid reads as `RUNNING`, and `terminate()` could signal an innocent process group). Production hardening: record process start-time alongside the pid in the sidecar and require both to match, or use pidfds. Not a Phase-0 concern (echo fixture only).

### Appendix-C adjudication (Codex, 2026-07-23)

- **B1 — RATIFY WITH EXECUTOR CONTRACT.** `increment_recovery_attempts` is consumed only inside the same serializable, expected-version CAS transaction that appends the transition event. A duplicate `source_event_id` returns the prior transition without incrementing. At the ceiling, `ESCALATE_RECOVERY` does not increment. This preserves `recovery_attempts <= max_recovery_attempts` and makes the ceiling reachable.
- **B2 — ACCEPT NOW.** `pr IS NULL` with no observer error means merge authority is unknown and returns `HOLD_UNOBSERVABLE`. A returned PR observation with `merge_authorized=false` means authority is known absent and returns `REQUEST_DECISION`.
- **B3 — ACCEPT, PHASE-2 PREREQUISITE.** Before a real model adapter is activated, its sidecar records PID plus OS process start time and `observe()`/`terminate()` require both to match. PID-only termination is forbidden. Phase 1 has no worker side effects, so this does not belong in the custody-spine implementation.

Decision-table extension (does not alter Appendix B's locked rows):

| Stage | Wait | CI | PR/authority observation | Action |
|---|---|---|---|---|
| `VERIFYING` | `CI` | `GREEN` | `pr=None` and no observer error: authority unknown | `HOLD_UNOBSERVABLE` |
| `VERIFYING` | `CI` | `GREEN` | PR returned with `merge_authorized=false`: authority known absent | `REQUEST_DECISION` |

---

## Appendix D — Phase 0 acceptance record (2026-07-23)

**Phase 0 PASSED on the target substrate** (Scott's Mac, Apple Silicon, Python 3.12, DBOS 2.28.0, Postgres 16 in Docker at `127.0.0.1:55432`), run by Scott from a fresh clone at head `21885af`.

- Constraint + controller tests: **7 passed** (includes the B1 ceiling tests).
- Kill harness: controller SIGKILL'd after each of the five boundaries (`REGISTERED`, `DISPATCHED`, `EXIT_OBSERVED`, `RECOVERY_PLANNED`, `EVIDENCE_VERIFIED`); at every death the registry retained custodian, next signal, deadline, and recovery action; DBOS recovered the workflow each time and drove the item to `COMPLETE`.
- Acceptance output: `continuation coverage=100%; orphan time=0; boundaries=5; elapsed=16.74s`.

**The Phase 1 gate is open.** Per the locked build order, next is the durable custody spine (events table + atomic CAS-plus-event transition function, shadow-mode action executor, minimal sentinel query), still driving nothing live.
