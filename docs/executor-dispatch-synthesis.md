# Executor Dispatch — Synthesis with CEC v3, Adversarial Review, and New Ideas

**Author:** Claude Code, for Scott Carlson
**Date:** 2026-07-23
**Reads with:** `docs/executor-dispatch-architecture.md` (my base design) and Codex's *CEC v3 — Mechanical Build-Control System*
**Purpose:** Take the best of Codex's CEC v3, say honestly where I'd steer differently and why, add ideas neither document has yet, and answer the 10 adversarial-review questions Codex addressed to me.

---

## 0. Verdict in one paragraph

Codex and I independently reached the **same core diagnosis** — an LLM conversation must never own custody of nonterminal work — which should raise your confidence that it's correct. Codex's document is excellent, and it's *stronger than mine in three specific places* that I'm adopting outright: the **four-field continuation invariant**, **evidence-verified completion** (killing false-DONE), and **custody-as-acknowledged-handoff**. I diverge on exactly **one** thing, but it's a big one: Codex says build the control plane by hand on SQLite and explicitly *don't* use a durable-execution engine — while simultaneously specifying compare-and-swap versioning, event replay, at-least-once delivery, leases, reconciliation, and a sentinel. **That list *is* a durable-execution engine.** Codex is proposing to hand-build Temporal and call it lean. The genuinely lean move is to write your *domain* logic (custody, evidence, routing) on top of an engine that already gives you the *plumbing* (DBOS Transact, on the Postgres Sōken already runs). Same invariants Codex wants — far less dangerous code you have to get right yourself. Everything below is the merge.

---

## 1. What Codex nailed — adopting these outright

These are real improvements over my base design. I'm pulling all of them in.

### 1.1 The four-field continuation invariant — the single best idea in either document

Codex's §4: **every nonterminal task must always carry all four of —**

1. **Current custodian** (who owns it right now)
2. **Next expected signal** (what event advances it)
3. **Deadline for that signal**
4. **Mechanical trigger if the signal never arrives**

— with a headline metric, **continuation coverage = % of nonterminal tasks that have all four**, target **100%**, and its shadow, **orphan time = total time any task had no valid custodian or continuation**, target **zero**.

This is the crispest possible statement of the fix. My design implied it ("the next step fires on the completion event"); Codex made it a *checkable property of every row in the database*. Adopting it, and **hardening it** (see §3.2 — I'd make it physically impossible to violate, not just measured).

### 1.2 Evidence-verified completion — this fixes a hole in my design

Codex's §9 is sharper than my "done is a JSON field." My rule was necessary but not sufficient: a worker emitting `{"status":"done"}` is making a **claim**, and an LLM can claim done from narrative rather than evidence — that's your false-DONE problem. Codex's correction:

- A worker's self-report is `RESULT_CLAIMED`, **never** `COMPLETE`.
- `WORKER_EXITING` is **never** read as completion.
- The kernel promotes a task to `COMPLETE` only from **mechanical evidence**: required artifacts exist, CI is green on the exact head SHA, review satisfied, the deployed SHA is actually serving, acceptance checks pass.

So the pipeline is `RESULT_CLAIMED → (kernel verifies evidence) → COMPLETE | FIX_REQUIRED`. Prose explains; **only evidence transitions state.** I'm adopting this wholesale — it's the difference between "the agent said it's done" and "it's done."

### 1.3 Custody as an *acknowledged* handoff (IATA Resolution 753)

Codex's §2/§11: the important event isn't "work happened," it's "**custody changed and the receiver acknowledged it**." Baggage is scanned at four custody points precisely because *transfers* are the highest-risk moment — which is exactly your return-leg. The hardening this implies: a handoff is a **two-phase ACK** (like TCP). Until the receiver acknowledges custody, the *sender remains custodian*. That closes the deadly gap where "the worker exited but the controller hadn't taken over yet" — from the task's point of view there is always **exactly one** custodian, never zero. I only had a one-way completion signal; the ACK protocol is better.

### 1.4 Three orthogonal axes: stage ≠ wait-reason ≠ custodian

Codex's §8: one state enum can't describe a task. Split it into **stage** (INTAKE…COMPLETE), **wait-reason** (CI, REVIEW, HUMAN_DECISION, DEVICE_TEST…), and **custodian** (GITHUB, CLAUDE_CODE, SCOTT…). Then "waiting" stops meaning "inactive" and starts meaning "under the custody of a named external system, awaiting a named signal." This makes the radar legible in a way a single status column never could. Adopting.

### 1.5 At-least-once + idempotency + compare-and-swap

Codex's §7 is correct distributed-systems hygiene and I under-specified it: unique source IDs on every event, dedupe duplicates, **CAS against a version column** on every transition (`WHERE version = :expected`), record intent before side-effects, ACK every receipt. The one nuance — with DBOS a lot of this is handled by the engine's exactly-once step semantics, so you write *less* of it by hand. But the principle (design for at-least-once, make actions idempotent, never promise exactly-once across GitHub + Railway + filesystem) is right and I'm keeping it front-and-center.

### 1.6 The independent sentinel — "who watches the watcher"

Codex's §15: the controller can't be trusted to report its own death. A **separate minimal process, on a different scheduling substrate**, checks controller heartbeat freshness, event-ledger advancement, orphaned tasks, notification-outbox age, DB integrity. It understands nothing about tasks and launches no models — it only reports the *absence of expected control-plane signals*. This is a genuinely good idea I didn't have. Adopting.

### 1.7 Fire drills + the killer acceptance test

Codex's §18 is the best testing idea in either doc. The acceptance test to frame the whole build:

> **Kill every LLM at every boundary. The registry must still show exactly where every task is, who owns it, what signal comes next, and what fires if that signal never arrives.**

I'm adopting the fire-drill methodology and most of the 20 scenarios (with two additions in §5.9). This is how you *prove* the loop survives, rather than hoping.

### 1.8 Also pulling in, with less fanfare

- **Immutable work packet** (§9) with `allowed_paths` / **`forbidden_paths`** (a worker must not touch the circuit's own code), `resource_lease`, `required_evidence`, `result_schema`. Richer than my task envelope.
- **Intake ledger** (§5): append-only raw inputs (voice, screenshots, video) separated from interpreted contracts — good for your multimodal intake.
- **Contract validator** between planner and ready-queue: a planner *proposes* task contracts; deterministic code *validates* (schema, acceptance test, dependencies, authority) before anything becomes eligible.
- **Versioned, metric-learning model routing** (§13): route by *measured* performance per task class (accepted-outcome rate, escaped defects, cost per accepted outcome), not "one model is primary." Assignment changes when evidence changes.
- **Named resource locks** (§6.3): `repo:soken`, `path:...`, `migration-head`, `deployment:production`, and cleverly `human:scott-device-test` — a person is a lockable resource.

---

## 2. The one real divergence — where I'd steer differently (adversarial)

You asked for honesty and said you lean toward my logic for the build. So here is the disagreement, stated plainly.

### 2.1 Don't hand-roll the durable-execution engine. Use one.

Codex's §6/§16 says: SQLite + one controller process, and *"Do not introduce Temporal, Kafka, RabbitMQ… yet."* But then §7–§15 specify, by hand:

- compare-and-swap versioned transitions (optimistic concurrency control),
- an append-only event log with replay,
- at-least-once delivery with dedupe,
- leases/locking with expiry,
- callback-token waits (it even cites AWS Step Functions' task-token pattern),
- reconciliation loops,
- a heartbeat sentinel.

**That is the feature list of a durable-execution engine.** Codex is right that you shouldn't stand up a *Temporal cluster* — that's heavy. But the conclusion "therefore write the engine yourself in SQLite" is the false economy I flagged in my base design (§5). The hard, dangerous code in this whole system is exactly that plumbing — CAS races, idempotency, crash-resume, exactly-once side-effects. Getting it *subtly* wrong is how you get a new, harder-to-debug class of the same orphaning bug.

**My recommendation stands: DBOS Transact.** It is *not* Temporal-heavy — it's an embedded library, no separate server, whose only dependency is Postgres, which **Sōken already runs**. It hands you durable workflows (the loop), checkpointed steps, queues (lanes/priority/dedup), durable timers (deadlines), and `send()`/`recv()` (the two-way radio + callback-token pattern) — *for free and correct*. Then you spend your effort writing the part that's actually yours: custody rules, evidence verification, routing policy. You get **every invariant Codex wants** with a fraction of the plumbing you have to personally get right.

**Fair steelman of Codex's SQLite choice** (because it's not baseless): the control plane may need to run **locally on your Mac**, next to the desktop apps that actually drive Claude Code and Codex, and off iCloud. SQLite is a single file with zero setup and no server — genuinely frictionless there. Two honest responses: (1) DBOS runs perfectly against a **local Postgres on the Mac** (one `brew install`, or a tiny Docker container) — you keep local-first without hand-writing the engine; (2) if you truly want zero-server-local, there are lighter embedded checkpointing patterns, but I would *still* not hand-roll CAS + replay + reconciliation from scratch. The DB being local is fine; the engine being homemade is the risk. **If you take one thing from this section: keep custody/evidence/routing as your code; let a library own crash-resume and exactly-once.**

### 2.2 Stage the build harder — get one task through end-to-end *first*

Codex's Phase 1 is a full custody kernel (7 tables, commands, callbacks, notifications, sentinel) *before anything runs*. For a solo operator that's a large amount to build before you feel a single win. My base design's **Phase 0 is a ~30-line supervisor that drives one real task to green and makes the bug disappear in a weekend.** I'd insist on that first: prove the loop survives a turn-boundary on *one* task, *then* add custody rigor around it. Value on day 2, not day 30. (Merged build order in §6.)

### 2.3 Watch the complexity budget

Seven tables, four message types (events/commands/callbacks/notifications), a sentinel on a separate substrate, and 20 fire drills before resuming dispatch — that's a lot of surface for one person to own. Most of it is *right* and belongs in the end state, but sequence it so each piece earns its place against a failure you've actually seen. I flag two specific candidates to **defer, not delete**: separate `callbacks` and `commands` tables can start as one table with a `direction` column; the sentinel can start as a cron that runs one SQL query, not a separate substrate. Grow them when a fire drill proves you need more.

---

## 3. New ideas — neither document has these yet (the creative part)

You asked for as many ideas as we can, and to be creative. Here's what falls out when you put both designs on the table and push further.

### 3.1 Level-triggered reconciliation — the deepest idea available, and it *generalizes both docs*

The single most important pattern neither doc named, borrowed from how Kubernetes runs the world's control planes:

- **Edge-triggered** = you act on *events*. Miss the event (the webhook that never arrives, the callback the model didn't receive) and you're stuck forever. **This is literally Codex's failure #6 and your original bug.**
- **Level-triggered** = you continuously **reconcile observed state toward desired state**. You don't *wait* for the CI-finished event; you have a desired state ("task 153 wants: CI green on SHA abc123") and a loop that repeatedly *observes* actual GitHub state and drives toward it. A missed event just means the next reconcile tick catches it. **Missing a signal delays progress; it never breaks the loop.**

The design rule: **treat every external event (webhook, hook, callback) as an *accelerant*, never as *ground truth*.** Ground truth is what a reconcile loop *observes* when it polls GitHub/Railway/the filesystem. Codex gestures at this ("Events provide speed; reconciliation provides truth" in §17 Phase 3) but buries it as a Phase-3 detail. **It should be the spine of the whole controller.** Concretely: the controller is a `while true: for task in nonterminal_tasks: observe_actual_state(task); if actual != desired: drive_toward(desired)` loop. This is why Kubernetes controllers survive dropped events, crashes, and restarts — and it's the cleanest possible answer to "the model never got the wake-up." You stop *needing* reliable wake-ups.

### 3.2 Make the continuation invariant physically unrepresentable to violate

Codex *measures* continuation coverage and targets 100%. Better than measuring is making the bad state **impossible to write to the database at all.** A nonterminal task missing a custodian, next-signal, deadline, or recovery-action shouldn't be a row you flag — it should be a row the database **rejects**:

```sql
-- A nonterminal task literally cannot exist without a continuation owner.
ALTER TABLE work_items ADD CONSTRAINT continuation_required CHECK (
    stage IN ('COMPLETE','PARKED')   -- terminal/parked states are exempt
    OR (custodian_type IS NOT NULL
        AND next_signal_type IS NOT NULL
        AND next_signal_deadline IS NOT NULL
        AND recovery_action IS NOT NULL)
);
```

Now "orphan time = 0" isn't a KPI you chase — it's a theorem the schema enforces. You cannot commit a transition that orphans a task; the write fails. This is the strongest possible version of Codex's invariant, and it costs one `CHECK` constraint.

### 3.3 Custody = a time-boxed lease with auto-revert (a dead-man's switch)

Unify Codex's four fields into one mechanism: **every custody is a lease with an expiry.** When the controller hands task 153 to GitHub-CI, it's a lease: "GitHub owns this until 11:42; if I don't see `check_suite.completed` by then, custody *automatically reverts to me* and I run the recovery action." Same for a worker (a build lease), same for you (a decision lease with a safe default on expiry).

Why this is powerful: **a task cannot be orphaned, because there is no state in which nobody is on the hook.** Every custodian is holding a stopwatch, and when it hits zero the task falls back to the controller by construction. This is the mechanical embodiment of "make attention disposable" — a worker (or a whole model) can vanish and the lease expiry *is* the wake-up. No heartbeat from the worker required; the *absence* of a renewal is the signal. DBOS durable timers implement this directly.

### 3.4 The "reconcile from ashes" test — survive total event loss

Add one fire drill that's stronger than any of Codex's 20, and design toward passing it:

> **Delete the entire `events` table. The controller must re-derive the correct state of every task purely by observing GitHub, Railway, and the filesystem.**

If you can pass that, you have proven that events are truly just an accelerant and the system's truth lives in reconcilable external reality — not in any log you could lose. This is the acid test for §3.1. It also happens to make your disaster recovery trivial.

### 3.5 Human decisions are just another lane — don't special-case Scott

Both docs treat human-in-the-loop as a distinct thing. Collapse it: **a task blocked on your decision has the exact same shape as a task blocked on CI** — custodian = `SCOTT`, wait-reason = `HUMAN_DECISION`, next-signal = "Scott's answer to decision D", deadline, and recovery-action = "apply safe default" or "escalate / re-notify." Now your decision queue, your CI waits, and your device-tests all flow through *one* mechanism and appear on *one* radar. Fewer moving parts, and it means the cockpit's "decision queue" is just a filtered view of the same work table (`WHERE custodian = 'SCOTT'`). You (`human:scott-device-test`) are a worker with a lane and a lease like any other.

### 3.6 Fresh-packet vs resume-session — resolve the tension explicitly

Here my base design and Codex's actually disagree, and it's worth naming so you decide deliberately:

- **I emphasized** `--resume <session_id>` for cheap continuity (don't re-pay context every call).
- **Codex §10 argues** *against* relying on waking the same attention — launch a **fresh** bounded turn with a **verified work packet** instead, because a re-verified packet is safer than hoping a stale conversation resumes correctly.

**Codex is right about the lifecycle; I'm right about the micro-step.** The synthesis:
- **Across lifecycle continuation** (the outer loop, after any wait/crash/CI cycle): **always fresh-packet.** Re-hydrate a new bounded turn from durable state — never depend on a specific prior session still being alive or coherent. Attention is disposable.
- **Within a single bounded assignment** that the controller is actively supervising in one sitting: `--resume` is a fine *optimization* to avoid re-sending context — but only when the controller holds the session id and the packet in the DB, so it can always fall back to fresh-packet if resume fails.

Rule: **resume is a cache, not a source of truth.** The verified packet in the registry is always able to reconstruct the turn from scratch.

### 3.7 Content-addressed, idempotent commands

Make a command's *identity* the hash of its meaningful content: `command_id = hash(work_item_id, objective, starting_sha, packet)`. Re-issuing the same logical command (after a crash, a retry, a duplicate wake) is then a natural no-op — the worker recognizes it has already acknowledged that exact command and doesn't redo the work. This gives you at-least-once delivery with exactly-once *effect* for free, without a separate dedupe table.

### 3.8 The event log is a black-box flight recorder

Since the `events` table is append-only and every transition is derivable from it, it *is* an aircraft black-box recorder: any incident is fully replayable after the fact. Pair this with fire drills and you can **replay a real production stall** deterministically to find the exact transition that failed — instead of guessing from narrative logs. Cheap to get (you're already keeping the log); enormously valuable the first time something weird happens at 2am.

### 3.9 Pull, don't push (kanban / Toyota) — the anti-collision mechanic

Codex mentions Toyota pull-flow in passing; make it explicit as *the* mechanism that stops tasks "crossing over each other," which was your original worry. The ready queue **never pushes work at a worker.** A worker (or a lane with a free WIP slot) **pulls** the next eligible task when it's ready. Backpressure is automatic: if nothing can safely start (locks held, WIP full, conflicts), nothing starts — the belt simply doesn't advance. This is structurally incapable of the "ten things running over each other" failure, because work only enters flight when a slot and all its locks are simultaneously free.

### 3.10 One-custodian invariant as the thing that keeps the radar honest

Tie it together with the rule that makes the whole cockpit trustworthy: **at every instant, every task has exactly one custodian — never zero (orphan), never two (conflict).** §3.2 enforces "never zero" at the schema level; resource locks + CAS enforce "never two." If those two hold, then the radar *cannot lie* — every task is always shown as owned by exactly one named party with a running clock. That single property is what turns the scorecard from a hopeful report into an air-traffic display you can actually trust.

---

## 4. Where Codex and I fully agree (worth stating, because agreement is signal)

- An LLM must never own custody of nonterminal work. **("Make attention disposable." / "Agent as a function, not a daemon.")**
- The state-transition kernel — not any model — owns the loop.
- Baggage-handling + air-traffic-control is the right mental model (custody handoffs + radar + radio).
- Priority + duration-aware routing, with shortest-job-first *only within* a priority class, and aging credit so big tasks don't starve.
- A live cockpit fed from the registry/event log — **never scraped from narrative result files.**
- Deterministic eligibility (hard constraints) separated from ranking (soft priorities).
- Claude as lead **planner/orientation**, not as the mechanical dispatcher.
- Don't build Kafka / a distributed platform / predictive scheduling before you have timing data.

Two independent designs converging this hard on the fundamentals is the strongest evidence that the fundamentals are right.

---

## 5. Answers to Codex's 10 adversarial questions (its §21)

**1. Is SQLite + one controller the correct lean substrate?**
Right instinct (one small local datastore, one controller), wrong conclusion. Don't hand-roll the engine on raw SQLite. Use **DBOS Transact on Postgres** — Sōken already runs Postgres, and DBOS is an embedded library, not a server, so it's just as "lean" operationally while giving you crash-resume, exactly-once steps, queues, durable timers, and send/recv for free. If local-on-Mac is a hard requirement, run a local Postgres; keep the *engine* off your hands regardless of the DB. (See §2.1.)

**2. Which bridge parts stay canonical vs. become projections?**
The registry (work items, events, custody) becomes the **single source of truth**. All Markdown/folder artifacts become **read-only projections** generated *from* the registry — never written back to as transactional state. Rule: if two views disagree, the registry wins, and the Markdown is regenerated. (This mirrors your existing "Drive wins" convention — here, "registry wins.")

**3. Is stage / wait-reason / custodian sufficient?**
Nearly. Add two fields per task: **`evidence_state`** (what's been mechanically verified so far — decouples "worker claims done" from "kernel confirmed done", per §1.2) and **`lease_expires_at`** as a first-class column (per §3.3, so custody is always a running clock). With those, yes.

**4. What continuation fields are missing from the invariant?**
The four are right. Harden rather than extend: (a) make it a **DB CHECK constraint** so it's unrepresentable to violate (§3.2); (b) add **`recovery_attempts`** + **`max_recovery_attempts`** so recovery itself can't loop forever — after N, it escalates to a `SCOTT` decision lane instead of retrying blindly. Recovery needs its own dead end.

**5. Smallest safe Claude Code worker-adapter interface?**
Four calls: **claim** (atomically take one command, write `COMMAND_ACKNOWLEDGED`), **run** (`claude -p` with the packet, `--output-format json --json-schema` for a typed `RESULT_CLAIMED`, `--allowedTools` scoped to the packet's `allowed_paths`), **report** (emit structured events; controller ACKs before the adapter may exit), **fail-safe** (adapter can die at any boundary; because custody is a lease, the controller reclaims on expiry). The adapter carries **no state** — everything it needs is in the packet, everything it produces is an event. That's the whole interface.

**6. How are Scott's phone decisions correlated and acknowledged?**
Every decision notification carries a **`decision_id`** and its **`work_item_id`**. Your answer (however it arrives — reply, tap, voice) is written to the **event ledger** keyed by `decision_id`, not interpreted from chat. The controller matches `decision_id`, validates the answer against the decision's allowed options, and transitions the task via CAS. Chat is an input channel, never the record. Treat the decision exactly like a CI callback (§3.5).

**7. Which scheduling weights are fixed vs. learned?**
Fixed policy (never auto-tuned): **hard eligibility** (dependencies, locks, authority, WIP) and **safety ordering** (P0/deadline outranks everything; security/auth/migration always gets independent review). Learned from metrics: **duration estimates** (historical medians by task-class × worker), **model routing** (by accepted-outcome rate and cost), and **aging-credit rate**. Rule: *safety and correctness weights are never learned; efficiency weights are.*

**8. Safest migration from the currently-activated CEC v2?**
Strangler-fig, read-only first. (a) Stand up the registry **in shadow mode** — it *observes* and records custody/events for real tasks but *drives nothing*. (b) Verify continuation-coverage and the cockpit match reality for a few real tasks. (c) Cut over **one low-stakes task class** to let the kernel actually dispatch. (d) Expand class by class. Keep CEC v2 able to run until the kernel has passed the fire drills for that class. Never a big-bang switch.

**9. Which fire drills are missing?**
Add: **(21) delete the entire events table — controller must re-derive all state from external reconciliation** (§3.4, the strongest test); **(22) a webhook is delivered twice, 10 minutes apart, after the task already advanced** (stale duplicate, not just fast duplicate); **(23) a lease expires while the worker is *actually still working* but silent** — recovery must be safe against a live-but-quiet worker (idempotent re-dispatch, not a second concurrent build).

**10. What should be deleted before implementation?**
Defer (don't delete): the separate **`callbacks` vs `commands`** tables (start as one table with a `direction` column); the **sentinel on a separate substrate** (start as a cron running one SQL query); **predictive/learned scheduling** (until you have timing data, use fixed medians). Delete now: any notion of **model-to-model live negotiation**, and any path where the **cockpit infers state from narrative files**. Keep the end-state vision; earn each component against a failure you've actually hit.

---

## 6. Unified build order (merging both designs)

Smallest-first, each phase independently useful, each removes a specific failure. This merges my Phase 0 pragmatism with Codex's custody rigor.

| Phase | Build | Failure it removes | From |
|---|---|---|---|
| **0. Prove the loop** | ~30-line supervisor drives **one** real task to green; `claude -p --json-schema`; "done" read from JSON | The turn-boundary orphan, on day 2 | Me §8 |
| **1. Durable it** | Move the loop into **DBOS workflow/steps** on Postgres; crash-resume for free | Loop dies on restart | Me §5 |
| **2. Custody kernel** | `work_items` with the **four-field invariant as a CHECK constraint**; stage/wait/custodian axes; event log; CAS versioning; **custody = lease with auto-revert** | Orphaned tasks (structurally impossible now) | Codex §4/§6 + me §3.2–3.3 |
| **3. Evidence completion** | `RESULT_CLAIMED → verify evidence → COMPLETE`; worker-exit never = done | **False-DONE** | Codex §9 |
| **4. Reconcile loop** | **Level-triggered** controller: observe GitHub/Railway/FS, drive toward desired; events are accelerants | Missed wake-ups / dropped callbacks | Me §3.1 (new) |
| **5. Belt + router + lanes** | Ready queue, deterministic `route()`, fast/slow lanes, **pull-based WIP**, resource locks | Tasks colliding / crossing over | Both |
| **6. Two-way radio** | Command/ACK protocol; `send()/recv()`; **decisions as a lane** (`custodian=SCOTT`) | Broken return-leg; human-in-the-loop | Codex §11 + me §3.5 |
| **7. Cockpit** | Radar from registry+log (never narrative); continuation coverage, orphan time, false-DONE | Flying blind | Both |
| **8. Sentinel** | Start as a cron with one query; grow to separate substrate | Controller dies silently | Codex §15 |
| **9. Fire drills** | Kill every LLM at every boundary; **+ delete the events table** | Everything above, proven | Codex §18 + me §3.4 |
| **10. Learned routing** | Metric-driven model/duration policy, once timing data exists | Wrong worker for the class | Codex §13 |

**The acceptance test for the whole system** (Codex's, sharpened): *Kill every model at every boundary — and delete the event log — and the controller still knows where every task is, who owns it, what comes next, and what fires if the signal never arrives.*

---

## 7. Bottom line

Codex's CEC v3 is a strong document and we agree on the fundamentals, which should give you confidence. Take from it: the **four-field continuation invariant**, **evidence-verified completion**, **custody-as-acknowledged-handoff**, the **stage/wait/custodian split**, the **sentinel**, and the **fire-drill discipline**. Steer away from its one risky call — **hand-building a durable-execution engine on SQLite** — and instead put those exact invariants on top of **DBOS + the Postgres you already run**, so the dangerous plumbing is a library's problem and your code is all custody, evidence, and routing. Then add the ideas neither of us had: **level-triggered reconciliation** as the spine (so you stop needing reliable wake-ups at all), **custody-as-lease** and the **schema-enforced invariant** (so orphaning is impossible, not merely measured), **decisions-as-a-lane**, and the **"reconcile from ashes" test**. Build it smallest-first: prove the loop on one task in a weekend, then wrap it in custody rigor phase by phase.

The whole thing still reduces to the sentence we both arrived at independently: **stop trying to keep a model awake; make the work survive the model.**
