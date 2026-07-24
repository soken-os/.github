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

---

## Appendix E — Phase 1 review (Claude, 2026-07-23)

Reviewed the Phase 1 custody-spine drop (`11c6e8d`) and the Appendix-C adjudication. **Ratified.** Verified in code:

- **B1 executor contract honored:** `Registry._transition_tx` runs the event append and the CAS-plus-increment in one SERIALIZABLE transaction; a duplicate `source_event_id` returns the prior transition without incrementing; a failed CAS rolls back the reserved event (no orphan events). The `recovery_attempts + delta <= max_recovery_attempts` guard sits in the UPDATE's WHERE as defense-in-depth.
- **B2 implemented** in `decide()` with the two new table rows and tests.
- **Shadow-mode guarantee holds:** `ShadowActionExecutor` writes custody state only; no process, GitHub, or Railway side effect exists anywhere in phase1 code. `ESCALATE_RECOVERY` parks the item under `custodian=HUMAN/scott` with a 4-hour decision deadline — the decision lane, realized early.
- **Proof-receipt gate:** `require_pass()` binds the receipt to the SHA-256 of the exact migration file, so a schema change invalidates stale receipts; `Registry.__init__` and the sentinel both refuse to run ungated.
- **Pure suites pass in review sandbox: 13/13** (controller incl. B2 rows + shadow executor). Postgres-backed suites (atomicity, duplicate-noop, stale-CAS rollback, append-only trigger, sentinel view) run via `run-shadow-tests.sh` on the target substrate.

Notes (no change made):

**C1 — no-increment HOLD can trip `continuation_deadlines_valid`.** The current-lease HOLD path records an observation with an unchanged patch; the transition re-stamps `updated_at`, and if `next_signal_deadline` is already past (signal overdue while lease current), the CHECK `next_signal_deadline >= updated_at` rejects the write. Benign observation-recording then fails noisily. Fix in Phase 2: either extend the signal deadline on observation-record, or skip the registry write for pure observation records (event-only append).

**C2 — duplicate `source_event_id` with different content silently returns the prior transition** (same work item). Acceptable under the content-addressed-ID convention; worth an assert-on-mismatch in Phase 2 to catch a miscomputed ID early.

**Acceptance path (target substrate):** on the Mac, re-run `reference/cec/phase0/run-proof.sh` (now also mints the proof receipt — the 2026-07-23 Appendix-D run predates the receipt mechanism), then run `reference/cec/phase1/run-shadow-tests.sh`. Both green on the Mac = Phase 1 accepted, Phase 2 gate opens.

### Phase 1 acceptance record (2026-07-23)

**Phase 1 PASSED on the target substrate** (Scott's Mac), run by Scott at head `e310f5e`:

- Phase 0 proof re-run: 9 tests passed (incl. B2 decision rows); kill harness green — `continuation coverage=100%; orphan time=0; boundaries=5; elapsed=16.58s`; proof receipt minted against the current migration hash.
- Phase 1 shadow suite through the receipt gate: **18 passed** — including the Postgres-backed proofs of atomic event+CAS commit, duplicate `source_event_id` no-op, stale-CAS rollback of the reserved event, the `cec.events` append-only trigger, and the `stale_nonterminal_items` sentinel view.

**The Phase 2 gate is open:** the one-task live slice — first real worker adapter activated under the B3 prerequisite (PID + process start-time sidecar; PID-only termination forbidden), one hand-authored schema-validated packet, evidence verification, GitHub/Railway observers, notification outbox, with CEC v2 still reversible.

### Bootstrap criterion (locked 2026-07-23)

**Phase 3 is not accepted unless CEC itself dispatched at least one of its own build tasks end-to-end** — packet authored by Scott or a planner, entered into the registry, dispatched by the kernel to a worker, leased, observed, result-claimed, and evidence-verified to `COMPLETE`. Phase 2 is the last phase whose build a human ferries to a model by hand. Rationale: harnesses exercise the paths we scripted; a real unscripted build task is the actual graduation exam, and running it first on the circuit's own next phase means any fumble is itself the highest-value bug report available, on a task trivially restartable by hand.

---

## Appendix F — Phase 2 one-task live-slice contract (2026-07-23)

### Additive schema decision

The locked `cec.work_items` schema is unchanged. Phase 2 adds only `cec.notification_outbox` through migration `003_notification_outbox.sql`. A completion-event trigger creates one durable notification row in the same transaction as the `COMPLETE` event and work-item CAS. Delivery writes a deterministic markdown file and moves the row `PENDING → DELIVERED`; explicit acknowledgement moves it `DELIVERED → ACKNOWLEDGED`. Retried file delivery is idempotent.

### Binding findings

- **B3 closed:** worker sidecars persist PID plus raw OS process start time. `observe()` requires the pair before reporting `RUNNING`. `terminate()` checks the pair before every signal, including escalation from `SIGTERM` to `SIGKILL`; PID-only termination is forbidden. A command ID is single-use, so controller replay returns its durable handle rather than relaunching an exited command.
- **C1 closed:** a no-increment `HOLD_UNOBSERVABLE` preserves the worker fence but advances `next_signal_deadline`, so restamping `updated_at` satisfies `continuation_deadlines_valid`.
- **C2 closed:** duplicate `(source, source_event_id)` now asserts equality of work item, event type, from/to versions, and payload. Different content raises `EventContentMismatch`; it never silently aliases the earlier event.

### One task

The sole packet is hand-authored and JSON-Schema validated as `LOW_RISK_TEST_REPORT`. It runs the read-only CEC controller/action unit tests and writes complete output to `reference/cec/phase2/runtime/live-slice-test-output.txt`. The deterministic `SCRIPT` adapter runs first as a dry run; live acceptance then invokes `claude -p --output-format json --json-schema` through `ClaudeCodeAdapter`. Neither path edits source, writes GitHub, or touches Railway.

`RESULT_CLAIMED` is not completion. The kernel requires a typed file claim, constrains the path to the workspace, recomputes SHA-256, and verifies passing test output. Only then does it write `evidence_state.completion_verified=true` and transition to `COMPLETE`.

### Acceptance criteria

1. Current Phase-0 receipt and Phase-1 receipt both validate against their bound implementation hashes.
2. Unit/Postgres suites pass, including B3, C1, C2, atomic completion/outbox, and evidence rejection tests.
3. Both deterministic and real-Claude runs complete the same packet.
4. In each run, the controller is killed after acknowledged worker custody while the worker remains alive and again after `RESULT_CLAIMED` before verification.
5. During both deaths the registry retains custodian, next signal, deadline, and recovery action: continuation coverage 100%, orphan time zero.
6. Final state is `COMPLETE` only after mechanical evidence verification.
7. Completion notification is durably enqueued, delivered to markdown, and acknowledged.

Required acceptance line per worker:

`controller_kills=2; continuation coverage=100%; orphan time=0; final=COMPLETE; notification=ACKNOWLEDGED`

### Phase 2 acceptance record + independent review (Claude, 2026-07-23)

**Phase 2 PASSED on the target substrate** (Scott's Mac), head `780c6c9`:

- Phase 0: 9/9 · Phase 1: 19/19 · Phase 2: 27/27.
- `SCRIPT` worker and **real `CLAUDE_CODE` worker** each completed the one-task packet after **two controller SIGKILLs** — one while the worker was alive mid-run (worker survived with pid + start-time identity intact), one after `RESULT_CLAIMED` before verification. Continuation coverage 100%, orphan time 0 throughout.
- Durable real-worker row `phase2-claude_code-f378a2de` reached `COMPLETE` at 2026-07-23 14:04:42 UTC with `evidence_state.completion_verified=true`; notification delivered and acknowledged.

**Independent review (this reviewer): RATIFIED.** Verified in code: B3 enforcement (`observe()` requires pid + raw `ps lstart` identity before reporting `RUNNING`; `terminate()` re-checks the pair before every signal; launch replay returns the durable handle instead of relaunching); C1 fix (no-increment hold advances the signal deadline, satisfying `continuation_deadlines_valid`); C2 fix (`EventContentMismatch` on content-divergent duplicate `source_event_id`); evidence verifier constrains the artifact inside the workspace, recomputes SHA-256, and requires passing-test content; the completion transition and outbox row commit in one transaction; the Phase 1 receipt binds to the spine's implementation hash so any spine edit forces re-proof. Pure suites re-run in review sandbox: 20/20.

**D1 — note for Phase 3 (no change made):** `collect_result` stamps the claim's `lease_token`/`lease_epoch` from the *current* command built off the live row, not from the sidecar record of the run that actually produced the output. In this single-controller slice the two always coincide; once reclaim/redispatch paths go live, the claim must carry the epoch recorded at launch (from `<command_id>.process.json`) so a stale worker's late output is fenced by content, not by circumstance. Bind this into the Phase 3 scope alongside the bootstrap criterion.

**This satisfies the live-slice milestone: the kernel drove a real Claude Code worker end-to-end — dispatch, lease, observe, claim, evidence-verify, notify — surviving controller death twice, with no human holding the loop.** Next: Phase 3, dispatched by CEC itself per the locked bootstrap criterion.

---

## Appendix G — Bootstrap acceptance record (2026-07-24): CEC dispatched its first build task

**The locked bootstrap criterion is SATISFIED.** Work item `phase3-bootstrap-d1` (the D1 claim-fencing fix, per the adjudicated contract in `docs/phase3-bootstrap-packet.md`) was seeded into the registry on Scott's Mac and driven by the CEC service end-to-end with no human ferrying: worktree prepared at pinned `starting_ref` → real `CLAUDE_CODE` worker launched → observed under pid+start-time identity → typed claim collected → **mechanically verified** → `COMPLETE` at `2026-07-24 02:56:00 UTC` with `evidence_state.completion_verified=true` → notification `DELIVERED` to the bridge outbox.

- Registry: stage `COMPLETE`, wait `NONE`, custodian `CONTROLLER/phase3-bootstrap-controller`, `lease_epoch=5` (the lease fenced over five turnovers mid-flight — the machine recovered through unscripted churn and still landed), `recovery_attempts=0` at completion.
- Evidence: `files_changed` exactly the two allowed paths; test output `27 passed` (SHA-256 `72bc7be8…be9ed1`); diff SHA-256 `21fb0512…f33d`. The worker diff was **not** committed or pushed — publication remains human-gated, as contracted.
- Run performed by Codex operating the Mac terminal; receipts re-minted first (Phase 0: 9 passed / coverage 100% / orphan 0; Phase 1: 19 passed).

### Adjudications on the run report (Claude)

**E5 — uncommitted verifier patch breaks evidence-chain reproducibility (must-fix before the next replay).** The run relied on a local stash (`cec-phase3-local-relative-artifact-verifier`, resolving repo-relative evidence paths). The verifier that ratified an accepted run **must exist in git history** — an acceptance produced by uncommitted code is not reproducible from the repo. Resolution: commit the stash as its own reviewed change (with a test) before any future replay; until then this acceptance stands on the registry/artifact evidence but carries this caveat explicitly.

**F1 — pending notifications for terminal rows are not delivered after service restart (real defect, follow-up packet).** The scan loop skips terminal rows, so a `COMPLETE` item's undelivered notification requires a manual delivery call. Fix: a deterministic delivery tick over the notification outbox independent of work-item stage. Queued as a self-dispatched packet alongside E1 (symlink type-change hardening) and E4 (worktree-scoped Bash).

**Publication path (decided):** the machine-produced D1 diff is applied verbatim from the diff artifact to a publication branch by Codex-at-the-terminal, pushed for Claude's content review on GitHub, and merged only after that review — machine produces, AI reviews, human approves. GitHub mutation stays outside the machine, per contract.

**With this record, Phase 2's closing sentence is upgraded: the human is no longer the dispatcher for build work. Remaining Phase 3 scope (pull queue, dependencies, resource locks, routing, D1-followups E1/E4/E5/F1) flows through the lane the machine just proved.**

### Publication review (Claude, 2026-07-24) — machine diff APPROVED, E5 closed

- **E5 closed** (`d1e5216`): the live-run verifier patch is now in git history — a two-line resolution of relative evidence paths against the worktree root, with tests (7 passed on the Mac). The evidence chain is reproducible from the repo; the redundant stash may be dropped at leisure.
- **D1 machine diff reviewed on content** (`70abd62`, applied verbatim from the artifact; working-diff SHA matched the acceptance artifact exactly: `21fb0512…f33d`). Verdict: **correct and approved.** The worker persisted the launch-time fence (`command_id`, `lease_token`, `lease_epoch`) in the process sidecar and made `collect_result` stamp the claim from that launch record with conservative failure semantics — missing/foreign/malformed record ⇒ no claim, never a guess — closing D1 exactly as specified. Both required tests present and sharp (the mismatch test plants parseable done-output so only the command-id mismatch can suppress the claim). Bonus compatibility property: legacy sidecars lacking the fence fields now yield no claim, which fails safe. Suites: 28 passed on the Mac post-apply; 28 passed + 1 skipped (Postgres-only) in independent review sandbox.
- `70abd62` is the first machine-authored change merged into the circuit, with provenance in the commit title.

### Precision amendment (Claude, on Scott's challenge — 2026-07-24)

Scott correctly challenged the phrase "the machine dispatched its first build task itself" as reading grander than the fact. The precise record:

- **Human/Codex acts:** the packet objective was authored by Claude at design time; Scott ferried prompts to Codex; Codex-at-the-terminal typed the seed command (loading the packet into the registry) and started the service. The machine did not originate the work, author it, or start its own engine.
- **Machine acts (the leg that was actually under test):** once running, the service found the READY row, created the worktree, launched the `claude -p` worker itself with the packet as its instruction, leased/observed/heartbeat it, collected the typed claim, regenerated and verified the diff mechanically, transitioned `COMPLETE`, and delivered the notification. No conversational handoff existed anywhere in that leg; the worker's prompt was a database row.

This satisfies the bootstrap criterion exactly as locked (which deliberately permitted a hand-authored packet and human seeding — the dispatch leg was the thing under test), and no more. The honest summary: **the loop no longer needs a human to stay alive; it still needs one to be born.** The gap-closers, in order: (1) always-on service via launchd — nobody types the start command; (2) registry seeding without a terminal (packet files the service watches, submittable from Scott's phone or Claude's session); (3) Phase 3.5 intake → planner → validator — machine-authored packets from raw intent, at which point "I gave the prompt" genuinely disappears.
