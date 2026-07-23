# The Executor: A Mechanical Dispatch Loop for AI Coding Agents

**Author:** Claude Code (research + design), for Scott Carlson
**Date:** 2026-07-23
**Status:** Design proposal for review (to be cross-reviewed by other models before a build session)

---

## 0. The one-sentence answer

> **The loop keeps breaking because you are asking an LLM to *be* the loop. An LLM cannot be the loop. The loop must be a small, dumb, deterministic program — a state machine backed by a database — that *calls* the LLM as a stateless function and never itself goes to sleep. Everything else in your instinct (the conveyor belt, the diverter, the air-traffic radar) is correct and maps directly onto a well-established engineering pattern called *durable execution*.**

You already diagnosed this correctly in your own words: *"the executor has to be more of a just a mathematical equation... this information handed to the right, this is this information handed to the left."* That is exactly right. This document turns that instinct into a concrete, buildable architecture.

---

## 1. Why the loop breaks (the real root cause)

Your symptom: the *outbound* leg works great — you hand a task to Claude Code or Codex, it runs, it does good work, it reports at the end. The *return* leg is where it dies: the result comes back, and then "something makes it think it's building," or "it thinks it's complete," or "it's asleep," and the loop stalls until a human pokes it.

Here is the mechanical reason, and it is not a bug you can prompt your way out of:

### An LLM turn is not a process. It is a function call that returns and disappears.

- **An LLM is stateless and turn-based.** It receives a context window, produces output, and then it is *done*. It has no background thread, no timer, no "wake me in 10 minutes," no ambient awareness that the world kept moving. When the turn ends, there is nothing left running. There is no "it" to be asleep or awake — the process simply returned.
- **"Thinking it's done" is not a mistake — it is the LLM behaving correctly.** A turn *is* supposed to end. The model finishing its turn is not the failure. The failure is that **nothing deterministic was holding the loop open to catch the result and fire the next step.** You were using the model's turn as if it were the control loop. It never was.
- **Feeding results back "into the same head" fights the context window.** Every time you route the result back to the *same* long-lived agent conversation, you are stuffing more into a finite context. Eventually it gets confused, drifts, or "decides" the work is complete. Conversations are a terrible place to store the state of a multi-task pipeline.

An industry phrase from the durable-execution world captures your exact problem:

> **"Loops which can't survive a restart aren't loops."** *(Inngest, "The Agent Loop Architecture")*

Your loop can't survive the end of an LLM turn. So it isn't a loop yet. It's a sequence of hopeful nudges.

### The fix, stated as a principle

**Separate the two jobs that are currently tangled together:**

| Job | Who should do it | Property required |
|---|---|---|
| **Own the loop** — remember what's queued, what's in-flight, what's done; decide what fires next; survive crashes, restarts, and hours of waiting | A deterministic supervisor program + a database | *Persistent, boring, never "decides" anything creatively* |
| **Do the task** — write the code, run the tests, reason about the change | The LLM (Claude Code / Codex), called as a one-shot worker | *Smart, disposable, stateless between calls* |

This is the "**agent as a function, not as a daemon**" principle. The agent is a function you call. The supervisor is the daemon. Today you have it backwards — you're treating the agent as the daemon, and there is no supervisor.

This is *exactly* your airport instinct: the **air traffic controller** (deterministic, always awake, watching the radar) is not one of the planes. The planes (the agents) fly their leg and land. The controller never "lands." Right now you have planes trying to also be the control tower.

---

## 2. Your mental model is the correct architecture — here is the mapping

You described the system three times, three ways, and every one maps cleanly onto a real component. This is worth making explicit, because it means we are not inventing anything exotic — we are naming parts you already intuited.

| Your words | The engineering component | What it actually is |
|---|---|---|
| "A conveyor belt, information coming in" | **The intake queue** | A durable, ordered, priority-ranked list of tasks in a database |
| "A camera reads the tag" | **The router / classifier** | A deterministic function that reads a task's metadata and decides its lane |
| "Diverts left / center / down the middle" | **Content-based routing to lanes** | Each lane = a destination (Claude Code, Codex, a human, a specific task-type worker) |
| "The diverter says 'I've diverted, I'm done, need info'" | **The worker callback / completion signal** | A structured event the worker emits: `done` / `needs-input` / `failed` |
| "Comes back to the dispatcher... dispatcher lets you know" | **The supervisor advances the state machine** | On each completion event, the supervisor records it and fires the next step |
| "Airport radar — see where every plane is" | **The status board / scorecard** | A live read model of every task's state, pushed to a dashboard |
| "Two-way radio to one specific plane" | **A signal to one running workflow** | The ability to send a message to a single in-flight task by its ID |
| "Priority, and timeline each task might take" | **Priority + duration-aware scheduling** | Queue ordering by priority, then by estimated cost/duration, with dependency ordering so tasks don't collide |

Every one of these is a solved problem. The reason it feels impossible is that you've been trying to make one LLM conversation do all eight jobs at once. Split them apart and each becomes simple.

---

## 3. The architecture (the "mechanical two-way device")

Here is the shape. Read it as a factory floor, because that's how you think about it and the mapping is exact.

```
                                   ┌───────────────────────────────────────────┐
                                   │   THE SUPERVISOR  (deterministic, always   │
   You  ──── new task ───────────► │   awake, owns the loop, no LLM here)       │
   Claude (planning) ── tasks ───► │                                            │
                                   │   • intake queue   (priority + est. time)  │
                                   │   • router         (which lane?)           │
                                   │   • state machine  (per task: queued →     │
                                   │       dispatched → running → blocked →      │
                                   │       done / failed)                       │
                                   │   • all state in Postgres (survives crash) │
                                   └───────┬───────────────┬───────────────┬────┘
                                           │ dispatch       │ dispatch      │ dispatch
                                           ▼                ▼               ▼
                                   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
                                   │  LANE: Claude│ │ LANE: Codex  │ │ LANE: HUMAN  │
                                   │  Code (exec) │ │ (codex exec) │ │  (you)       │
                                   │  headless,   │ │  headless,   │ │  a question  │
                                   │  one-shot    │ │  one-shot    │ │  in your     │
                                   │  worker      │ │  worker      │ │  inbox       │
                                   └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
                                          │ structured     │ structured    │ structured
                                          │ result +       │ result +      │ answer
                                          │ "done/blocked" │ "done/blocked"│
                                          ▼                ▼               ▼
                                   ┌───────────────────────────────────────────┐
                                   │   COMPLETION HANDLER  (part of supervisor) │
                                   │   • record result   • advance state machine│
                                   │   • enqueue next / unblock dependents      │
                                   │   • emit status event → dashboard + you    │
                                   └───────────────────────────────────────────┘
                                          │
                                          ▼
                                   ┌───────────────────────────────────────────┐
                                   │   THE RADAR  (status board / scorecard)    │
                                   │   live view of every task's state,         │
                                   │   pushed to your screen; you can radio      │
                                   │   any single in-flight task by its ID       │
                                   └───────────────────────────────────────────┘
```

The three unbreakable rules that make the return-leg reliable:

1. **The supervisor is not an LLM and never sleeps waiting on a "thought."** It's a plain program. Its only states are database rows. If the machine reboots, it reads the rows and continues. There is no "it got confused."
2. **A worker call is a *function call with a structured return*, not a conversation.** Claude Code / Codex are invoked in **headless mode**, told to emit a **machine-readable result** (JSON), and the supervisor reads that JSON. "Done" is a field in a JSON object, not a vibe the model has to feel and you have to guess at.
3. **The next step is fired by the completion *event*, not by hope.** When a worker's process exits, that exit *is* the wake-up. The supervisor is triggered by the worker finishing — it does not have to "remember to check." (This is the difference between event-driven and "poking it.")

---

## 4. The crux: how the return-leg actually works (concrete mechanisms)

This is the part that has been failing you, so it gets the most detail. There are two levels, and you want both: a **hard** signal (the process exited) and a **rich** signal (what it did / whether it's blocked).

### 4.1 Drive the agents in HEADLESS mode — never in an interactive chat you have to babysit

Interactive Claude Code / Codex sessions are for *you*. For the *loop*, you invoke them non-interactively, one shot per task, and read their structured output.

**Claude Code (headless / "print" mode):**
- `claude -p "<task>"` runs a single task non-interactively and exits. This is the worker call.
- `--output-format stream-json` (or `json`) makes it emit **machine-readable** events/results instead of prose, so the supervisor can parse "what happened" instead of guessing.
- `--resume <session_id>` / `--continue` lets a later task pick up a prior session's context *deliberately*, when you actually want continuity — controlled by the supervisor, not left to the model.
- The **process exit code** is your hard "it's done" signal. When `claude -p` returns, the task turn is *definitively* over. The supervisor was `await`-ing that process; the return is the wake-up.
- The **Claude Agent SDK** (Python and TypeScript) is the same engine as a library: `query(prompt, options)` runs the full agent loop and streams every message back into *your* code, ending with a result message that carries the `session_id`. This is the cleanest way to make "call the agent as a function" literally true in Python (your backend is FastAPI/Python).

**Claude Code hooks — the "diverter reports back" mechanism, built in:**
- Claude Code fires **lifecycle hooks**: `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, `SessionEnd`, and more. A hook is deterministic code *you* control that runs at a defined moment — "the deterministic layer around your agent."
- The **`Stop` hook** fires exactly when the agent thinks it's finished — i.e. at the precise moment your loop has been dying. You can use it two ways:
  - **As a completion beacon:** the Stop hook POSTs "task X finished, here's the result" to your supervisor. Now the return-leg is an *event you receive*, not a state you have to poll for.
  - **As a forced-continuation guard:** a Stop hook that exits with **code 2**, or prints `{"decision": "block", "reason": "..."}` on exit 0, *forces the agent to keep working* instead of stopping — e.g. "tests still failing, keep going." **Critical detail:** check the `stop_hook_active` flag to avoid an infinite loop, and note exit-2 and JSON-on-exit-0 are two *different* channels (Claude only reads the JSON on exit 0; on exit 2 the JSON is ignored). This is how you stop the model from "deciding it's done" prematurely.

**Codex CLI (headless / exec mode):**
- `codex exec "<task>"` is the non-interactive equivalent: runs one session to completion, streams events, exits when done.
- `codex exec --json` emits a **JSONL event stream** (one JSON object per line); the *final agent message* goes to stdout, progress to stderr.
- `--output-schema <schema.json>` **constrains the final answer to a JSON Schema you define** — so you can *require* the worker to return exactly `{"status": "done|blocked|failed", "summary": "...", "next": "..."}`. This is the single most useful flag for a reliable return-leg.
- `--full-auto` / `--sandbox workspace-write` control autonomy so it can actually make edits unattended in CI-style runs.

**The takeaway:** with `--output-schema` (Codex) or `--output-format json` + a `Stop` hook (Claude Code), the phrase *"I don't know if it's done"* stops being a judgment call. "Done" becomes a field in a JSON object that a five-line parser reads. The ambiguity you've been fighting is eliminated at the source.

### 4.2 Three ways to know a worker finished — use all three, in order of trust

1. **Process exit (most trusted).** The supervisor launched the worker as a subprocess and is awaiting it. Exit = done, full stop. This alone fixes 90% of your stalls, because it does not depend on the model "reporting" anything.
2. **Structured final output (richest).** Parse the JSON result for `status`, a summary, follow-up tasks, and whether it's `blocked` needing your input.
3. **Lifecycle hook / callback (earliest + most flexible).** The `Stop` hook (Claude) or a JSONL `--json` event (Codex) can notify the supervisor *and* carry a payload, even mid-run.

Because the supervisor owns the subprocess, **there is no "return leg to lose."** The result doesn't have to travel back through a fragile chain of conversational nudges — it comes back as the return value of a function the supervisor called. That is the whole trick.

### 4.3 Continuity without trusting the transcript (re-seeding state on resume)

Two facts make the calls *continuous* without asking the model to remember anything:

- **`--resume <session_id>` (Claude) / `codex exec resume <id>` (Codex)** replay the *conversational* context deliberately, when the supervisor wants it. Capture the id from the first call's JSON (`.session_id`) and hand it back on the next call. Continuity is a handle the supervisor holds — not a memory the model has to keep alive.
- **The `SessionStart` hook (Claude) re-injects durable state on every resume.** Its output is added to context before the first prompt — so the supervisor can re-seed "current branch, what's already done, the head of the queue" on each wake. This is how you compensate for the model's statelessness: the *control* state always lives in your database, and gets re-served fresh each turn, so nothing important ever lives only in a transcript that can drift or compact.

**Exact completion signals to build against:**
- Claude `claude -p` ends its stream with a `result` message (final text + cost + session id); a clean `SIGTERM` shutdown exits **143**; `--json-schema` forces a typed `structured_output` field so `status` is machine-read, not scraped.
- Codex `codex exec` returns meaningful non-zero exit codes on failure, and its **`notify` hook** fires an `agent-turn-complete` JSON payload (with `last-assistant-message`) at turn end — the Codex analog of Claude's Stop hook, and the cleanest way for Codex to *push* "I'm done" to your supervisor. (Defensive note: a known Codex bug can exit 0 with empty stdout when stdio is detached from a TTY on a long prompt — have the supervisor treat empty output as a failure to retry.)

---
## 5. Who owns the loop: use a *durable execution* engine — don't hand-roll it

The "small, dumb, deterministic program that never sleeps" has a formal name in engineering: a **durable execution** engine. This is a program whose progress is checkpointed to a database step-by-step, so if the process crashes, restarts, or is redeployed *mid-run*, it automatically resumes from the last completed step and runs to completion **exactly once**. The loop's position lives in a database, not in RAM — which is precisely the property your loop is missing today.

The governing principle these engines are built around is the one you need, verbatim: **"the orchestrator owns the loop; workers are stateless."** The orchestrator stores state, manages queues, tracks timers, and decides what runs next. Workers just pull a task, do one step, report back, and forget everything. If a worker dies mid-step, the orchestrator re-dispatches it. **Do not write the survive-and-resume logic yourself** — that is the entire value of adopting an engine, and it is the exact logic that has been failing you.

### The four hard things these engines give you for free

These are the four things a plain script (or an LLM) can't do reliably, and all four are things your loop needs:

1. **Retries** — a failed step (agent crashed, network blip) is retried automatically, re-running *only* that step, not the whole pipeline.
2. **Timeouts** — "this agent run may take up to 2 hours, then time out" is a durable deadline that survives a reboot.
3. **Long human-in-the-loop waits** — a workflow can *park* for hours or days waiting for you to answer a question, without holding a running process open. When you answer, it wakes and continues.
4. **Signals / callbacks** — an external event (the agent finished; you clicked "approve") delivers a payload to one specific paused workflow and wakes it. **This is your "two-way radio to one plane."**

### The recommendation: DBOS Transact (lightest thing that genuinely owns the loop)

For a solo/small Python + FastAPI + Postgres shop — which is exactly Sōken — the standout is **[DBOS Transact](https://github.com/dbos-inc/dbos-transact-py)**, and the reason is decisive: it is the only option that is *all* of (a) a real durable-execution engine, not just a task queue; (b) **embedded in your FastAPI process with zero extra servers — Postgres is the only dependency** (you already run Postgres for Sōken, so this adds *nothing* to your infrastructure); (c) first-class Python; and (d) shipped with every primitive the dispatcher needs.

Your dispatcher, in DBOS terms:
- A `@DBOS.workflow()` **is** the dispatch loop — it iterates over queued tasks and never loses its place, even across a reboot.
- Each `@DBOS.step()` shells out to `claude -p` or `codex exec` and records the structured result. If the box reboots mid-agent-run, the workflow resumes at the next un-checkpointed step.
- A **DBOS queue** enforces "one agent at a time" (or N), with priority, rate-limiting, and dedup — no separate message broker to run.
- **`DBOS.sleep()`** gives durable backoff/scheduling (survives restarts, can be days).
- **`DBOS.recv()` / `DBOS.send()`** is the human-in-the-loop and callback channel: a workflow waiting on your answer parks on `recv()`; an HTTP handler (or the agent's Stop hook) calls `send()` to wake it. Postgres-backed, exactly-once.

DBOS uses **explicit checkpointing** (each step writes its result to Postgres as it finishes) rather than Temporal-style **deterministic replay**. That distinction matters for you: replay engines forbid non-deterministic code in the orchestrator (no wall-clock, no random, no direct I/O outside steps), and interleaving LLM/agent subprocesses is exactly the kind of thing that trips that wire. DBOS's weaker determinism requirement means far less footgun for an agent-driving loop.

### The alternatives, and when they'd win

| Engine | Extra server to run? | Python | Durability model | When to pick it |
|---|---|---|---|---|
| **DBOS Transact** | **No** — library + your Postgres | First-class | Explicit checkpoint | **Default. Best fit for Sōken.** |
| Restate | Yes (single self-contained binary) | Yes | Journal + replay | Want a standalone engine, don't mind one binary; strong AI-agent integrations |
| Hatchet | Yes (+ Postgres) | Yes | Postgres event log + replay | Want a standalone engine explicitly built for AI agents |
| Inngest | Yes (or managed cloud) | Yes (3.10+) | Step memoization/replay | Your model is event-driven rather than a linear loop |
| Temporal | Yes (a real cluster + DB) | First-class | Event sourcing + replay | Only if this grows into a multi-service *platform* and you'll run a cluster |
| AWS Step Functions | Managed, AWS-only | Lambda only (orchestration is JSON, not Python) | Managed | Only if you're all-in on AWS |
| Procrastinate / PgQueuer | No (Postgres) | Yes | Durable *queue* only — **no** survive-and-resume orchestration | Only if you deliberately want to hand-build the state machine |

The trap to avoid: a plain task queue (Procrastinate, PgQueuer, Celery, RQ) gives you durable *enqueueing* but **not** durable *orchestration* — you'd hand-roll the "which step are we on, resume after crash" state machine, which is the exact thing that's been breaking. Since DBOS gives you real durable execution with the *same* "just Postgres, no server" footprint, a queue-only library is a false economy here.

> **Sources:** [DBOS Transact (Python)](https://github.com/dbos-inc/dbos-transact-py) · [Durable workflows in Postgres with DBOS (Supabase)](https://supabase.com/blog/durable-workflows-in-postgres-dbos) · [DBOS vs Temporal (2026)](https://www.tiarebalbi.com/en/blog/dbos-vs-temporal-postgres-durable-execution) · [Restate — What is Durable Execution](https://www.restate.dev/what-is-durable-execution) · [Hatchet](https://github.com/hatchet-dev/hatchet) · [Inngest — Durable Execution](https://www.inngest.com/platform/durable-execution) · [Temporal self-hosted guide](https://docs.temporal.io/self-hosted-guide) · [Inngest — The Agent Loop Architecture ("loops which can't survive a restart aren't loops")](https://www.inngest.com/blog/agent-loop-architecture)

## 6. The conveyor + scanner + diverter (queue, router, lanes)

This is the intake side of your factory floor. The good news: if you adopt DBOS, its **built-in queues run on the same Postgres**, so the belt and the loop share one engine — nothing extra to operate. The patterns below are how you shape that belt (and are exactly how you'd build it by hand on raw Postgres if you ever skip the engine).

### 6.1 The belt: a durable, ordered, priority-ranked queue

The belt is a Postgres-backed queue. The core primitive that makes a database table behave as a proper concurrent queue is **`SELECT … FOR UPDATE SKIP LOCKED`**: each worker atomically claims a row no one else has locked, so a job runs exactly once with no two workers colliding on the same item. DBOS's queues use this under the hood; if you ever hand-roll it, the whole "grab the next item and mark it in-flight" step is one statement:

```sql
UPDATE tasks
SET state = 'running', worker_id = $1, started_at = now()
WHERE id = (
    SELECT id FROM tasks
    WHERE state = 'queued' AND run_at <= now()
      AND lane = $2                        -- restrict to this diverter's lane
    ORDER BY priority DESC, est_duration_s ASC  -- priority, then shortest-job-first
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

Because it's your existing Postgres, enqueue is **transactional** — you can create a task in the *same* database transaction as a business write, so a task is never "half-created." This kills a whole class of the ghosts you've been chasing.

### 6.2 The scanner: a single, deterministic router function

The camera-reads-the-tag step is the **Content-Based Router** pattern: one pure, deterministic function that reads a task's metadata and returns which lane it belongs in. Keep it single-purpose and testable — the router is the *only* thing that assigns lanes; workers never re-route themselves. That single-writer discipline is what makes the system's behavior reproducible and debuggable.

```python
def route(task: Task) -> str:                 # returns a lane name
    if task.task_type in CODEX_TASKS:   return "codex.fast"
    if task.est_duration_s > 120:       return f"{task.agent_target}.slow"
    return f"{task.agent_target}.fast"
```

As routing rules grow, promote this from `if/elif` into a small **config-driven rules table** (`match → lane` rows in Postgres or YAML) rather than sprawling code — the router is a frequent point of change, so make it data, not logic.

### 6.3 The diverter: lanes that stop tasks from "crossing over each other"

You explicitly want tasks that "don't cross over each other" and priority/duration-aware scheduling. Four distinct collisions, four mechanisms — this is the heart of what you asked for:

| The collision you described | The fix | How |
|---|---|---|
| A 45-min job starves ten 5-sec jobs behind it (head-of-line blocking) | **Duration-class lanes** | Tag each task with `est_duration_s`; route to `*.fast` vs `*.slow`; give each lane its **own workers** so a long job *cannot* block short ones. This is the single most effective fix, and it's your "divert left vs right" made literal. |
| Two jobs that touch the same resource must run one-at-a-time | **Per-lane serialization (lock)** | A lock key (e.g. `repo:sokenapp`) — only one job with that key runs at a time; others wait. In DBOS/procrastinate this is a first-class option. |
| The same work gets queued twice | **Queueing lock (dedup)** | A uniqueness key on the *queued* state so a duplicate can't enter the belt. |
| B must not start until A finishes | **Dependency ordering** | Store `depends_on`; a task only becomes `queued` once all its deps are `done`. For genuinely branching multi-step flows, this is where the durable-execution workflow (§5) *is* the DAG — you don't hand-roll a resolver. |

Priority itself is just `ORDER BY priority DESC` in the claim, with `est_duration_s ASC` as the tiebreaker so short jobs drain first (shortest-job-first). That directly encodes your instinct to "queue based on the timeline each task might take."

A concrete task envelope (the "tag" the scanner reads):

```json
{
  "task_type": "code_review",
  "agent_target": "claude_code",
  "priority": 70,
  "est_duration_s": 8,
  "lane": "claude_code.fast",
  "depends_on": ["task_123"]
}
```

## 7. The radar + the two-way radio (scorecard / observability)

This is your air-traffic-control screen: see every in-flight task at a glance, and be able to "radio" any single one. It has two halves — a *broadcast* feed (radar) and a *targeted* channel (radio) — and they use different mechanisms on purpose.

### 7.1 The radar: an append-only event log, pushed to a live board

The durable, debuggable pattern is: **every task emits a structured event on each state transition to an append-only log; the dashboard subscribes to that log.** Never let the dashboard poll the live workers — poll the log.

```
queued → dispatched → running → (blocked ⇄ running) → done | failed | cancelled
```

```python
class TaskEvent:          # one append-only row per transition — the radar's data source
    task_id: str
    lane: str
    agent_target: str
    state: str            # queued | dispatched | running | blocked | done | failed
    ts: datetime
    detail: dict          # error, step name, tokens, cost, etc.
```

The current state of the whole fleet is one query (`SELECT DISTINCT ON (task_id) … ORDER BY task_id, ts DESC`). You also get a full audit trail for free — *why did task 123 fail?* is answerable forever, which is exactly the "metrics/scorecard we can just see" you've been asking for.

**Push to the screen with Server-Sent Events (SSE), not WebSockets.** The radar feed is one-directional (server → screen), and SSE is the 2025 consensus for that: a single long-lived HTTP stream that **the browser auto-reconnects** if it drops — essential for an always-on board. In FastAPI it's a few lines with `sse-starlette` streaming from Postgres `LISTEN/NOTIFY`. Reserve WebSockets for the *interactive* controls only (the radio, below).

**The gauges to put on the board** (per lane and system-wide):
- **Queue depth / backlog** — waiting tasks; if it climbs, intake is outrunning the agents. Your single most important alarm.
- **In-flight count** — running now, against your concurrency caps.
- **Oldest-waiting age** — catches starvation (the "short job stuck behind a long one" symptom) directly.
- **Throughput** — completed per hour, per lane.
- **Failure rate** — failed ÷ completed, per lane and per task-type/agent.
- Per task: **time-in-state**, total latency, retry count, and — because these are AI agents — **token usage & cost**.

**For the deep "why" of one agent run,** instrument with **OpenTelemetry's GenAI conventions**: an `invoke_agent` span with child `chat` (each LLM call) and `execute_tool` (each tool call) spans, carrying token counts and model. Propagate the same `task_id` as the trace id, so clicking a task on the radar jumps straight to its full internal trace. The event-log board answers "what's happening across the fleet now"; OTel answers "what happened inside task 123."

### 7.2 The two-way radio: signaling one specific in-flight task

This is the **"signal"** primitive from durable execution — an asynchronous message delivered to one running workflow by its id, to change its flow (approve / redirect / cancel / "here's the context you asked for"). In DBOS this is exactly **`DBOS.send(workflow_id, message)`** on one side and the workflow parking on **`DBOS.recv()`** on the other — Postgres-backed and exactly-once, so even if the worker restarts between receiving and processing, the signal isn't lost. That durability is the difference between a real radio and one that drops half its calls.

This closes your loop in both directions:
- **Human-in-the-loop:** a task that hits an ambiguity parks on `recv()` (consuming *no* running process, for hours or days if needed) and emits a `blocked` event so it lights up on the radar. You answer from the dashboard; a `send()` wakes it and it continues. This is the "diverter says: I'm done, need info" → "dispatcher tells you" → "you answer" → "it resumes" cycle, made mechanical and durable.
- **Control:** cancel or redirect any in-flight task by its id, from the board.

## 8. How to build it (phased, smallest-first)

Don't build the whole factory at once. Each phase is independently useful, and each one removes a specific way your loop currently dies. Do them in order.

### Phase 0 — Prove the return-leg with a 30-line supervisor (a weekend)

Before any database or dashboard, prove the core principle with a shell script or a small Python file. **The point of this phase is to feel the bug disappear.** The supervisor owns the `while` loop; the agent is a function it calls; "done" is read from JSON, not guessed.

```bash
#!/usr/bin/env bash
# The SUPERVISOR owns the loop. The agent is a function it calls. Nothing here sleeps.
set -euo pipefail
sid=""; next="Start: implement feature X per SPEC.md"
SCHEMA='{"type":"object","properties":{"status":{"enum":["done","needs_input","failed"]},"next":{"type":"string"}},"required":["status"]}'

while :; do
  if [[ -z "$sid" ]]; then
    out=$(claude -p "$next" --output-format json --allowedTools "Read,Edit,Bash" --json-schema "$SCHEMA")
    sid=$(jq -r '.session_id' <<<"$out")
  else
    out=$(claude -p "$next" --resume "$sid" --output-format json --allowedTools "Read,Edit,Bash" --json-schema "$SCHEMA")
  fi
  status=$(jq -r '.structured_output.status' <<<"$out")   # a TYPED result, not a vibe
  case "$status" in
    done)        echo "workflow complete"; break ;;
    failed)      echo "agent failed"; exit 1 ;;
    needs_input) next=$(compute_next_step "$out") ;;       # deterministic code decides next
  esac
done
```

The invariant that fixes your bug: **the `while`, the `sid`, and the decision to call again all live in the supervisor — never in the model.** A turn ending is just this script getting a return value. Run the same thing against Codex with `codex exec --json --output-schema schema.json` and `codex exec resume <id>`.

### Phase 1 — Durable it (adopt DBOS; the loop now survives crashes)

Move the Phase-0 loop into a `@DBOS.workflow()` and make each agent call a `@DBOS.step()`. Now if the machine reboots mid-run, the loop resumes at the next un-checkpointed step. State lives in Postgres. This is the phase where the loop stops being mortal. No new infrastructure — it's your existing Postgres.

### Phase 2 — The belt + diverter (queue, router, lanes)

Add the `tasks` table (or DBOS queues), the deterministic `route(task) -> lane` function, and fast/slow lanes with dedicated workers. Now you can drop many tasks in at once, they route themselves, and short jobs stop colliding with long ones. You (and a planning-mode Claude) enqueue tasks; the supervisor drains them by priority.

### Phase 3 — The radar (event log + SSE board)

Add the append-only `task_events` table and a FastAPI SSE endpoint that tails it via `LISTEN/NOTIFY`. Build the simplest possible board: one row per task, its current state, time-in-state, and the lane gauges (queue depth, in-flight, oldest-age, failure rate). This is the scorecard you keep asking for — now you can *see* the whole fleet.

### Phase 4 — The two-way radio (signals + human-in-the-loop)

Wire `DBOS.send()`/`recv()`: a task that needs your input parks, lights up `blocked` on the radar, and waits (consuming nothing) until you answer from the board. Add cancel/redirect for any in-flight task by id. Now the loop is fully closed in both directions and the human is *optional*, not load-bearing.

### Phase 5 — Deep tracing (OpenTelemetry, optional)

Instrument agent runs with OTel GenAI spans keyed by `task_id`, so clicking a task on the radar opens its full internal trace (every LLM call, tool call, token count, cost). This is the "look at one specific plane in detail" capability.

## 9. The whole thing in one picture, and the rules that keep it alive

**The unified stack (lightest that does everything you described):**

| Layer | Your metaphor | The build | Runs on |
|---|---|---|---|
| **Loop owner** | Air traffic controller | **DBOS Transact** durable workflow — the supervisor that never sleeps | Your existing Postgres |
| **Belt + diverter** | Conveyor + camera + diverter | DBOS queues (or `SKIP LOCKED` table) + a deterministic `route()` + fast/slow lanes | Same Postgres |
| **Workers** | The planes | `claude -p --output-format json --json-schema` and `codex exec --json --output-schema`, called as one-shot functions | Subprocesses / Agent SDK |
| **Completion signal** | "Diverted, done, need info" | Process exit + typed JSON + Stop hook (Claude) / `notify` hook (Codex) | Built into the CLIs |
| **Radar** | ATC radar screen | Append-only `task_events` + FastAPI **SSE** dashboard | Same Postgres |
| **Two-way radio** | ATC radio to one plane | `DBOS.send()` / `recv()` signals by task id | Same Postgres |
| **Deep trace** | Zoom in on one plane | OpenTelemetry GenAI spans, keyed by `task_id` | OTel collector (optional) |

Notice the right-hand column: **almost the entire system runs on the Postgres you already have for Sōken.** That's not a coincidence — it's *why* this is the lightest design. You're not adding a distributed system; you're adding a table, a workflow decorator, and a router function.

**The five rules that keep the loop alive** (print these on the wall):

1. **An LLM is a function you call, never a daemon that runs.** The moment you expect the model to "keep going on its own," you've reintroduced the bug.
2. **The loop, and all control state, lives in the supervisor + Postgres — never in a transcript.** Transcripts drift and compact; database rows don't.
3. **"Done" is a field in a JSON object, not a judgment.** Use `--json-schema` / `--output-schema` so the supervisor reads a typed status. Never scrape prose to decide if work finished.
4. **The next step fires on the completion *event*, not on hope.** Process-exit, Stop hook, or `notify` hook is the wake-up. Nothing polls; nothing sleeps except the always-on supervisor.
5. **A worker crash is a re-dispatch, not a dead loop.** That's the durable-execution engine's whole job — let it do it; don't hand-roll survive-and-resume.

**Why *you* keep having to poke it, stated once more, plainly:** today the human *is* the supervisor. You are the always-on process holding the loop open, reading the result, and deciding the next step. Everything above is just moving those three jobs — hold, read, decide — out of your head and into a small deterministic program backed by the database you already run. When that program exists, the loop survives the end of every turn on its own, and you get promoted from "the thing keeping the loop alive" to "the air traffic controller watching the radar and occasionally picking up the radio."

---

## Appendix: exact flags and primitives referenced

**Claude Code (headless):** `claude -p "<task>"` · `--output-format json|stream-json` (stream needs `--verbose`) · `--json-schema '<schema>'` → typed `structured_output` · `--resume <session_id>` / `--continue` · `--allowedTools` · `--permission-mode acceptEdits|dontAsk` · `--max-turns` · `--bare` (reproducible CI) · clean `SIGTERM` → exit 143.
**Claude Code hooks:** `Stop` (exit 2 or `{"decision":"block","reason":"..."}` forces continuation; guard with `stop_hook_active`; or `curl` a webhook to wake the supervisor) · `SessionStart` (re-inject durable state via `additionalContext`) · `PreToolUse`/`PostToolUse`/`SessionEnd`.
**Claude Agent SDK (Python 3.10+ / TS):** `claude-agent-sdk` · `query(prompt, options)` async iterator ending in `ResultMessage` · `ClaudeAgentOptions(resume=..., hooks=..., allowed_tools=...)` — the cleanest way to make "agent as a function" literal in your FastAPI backend.
**Codex CLI (headless):** `codex exec "<task>"` · `--json` (JSONL events) · `--output-schema <file>` (typed final answer) · `--output-last-message <file>` · `codex exec resume <id>` / `--last` · `--full-auto` / `--sandbox workspace-write` · `notify` hook → `agent-turn-complete` payload.
**Durable execution (DBOS Transact):** `@DBOS.workflow()` (the loop) · `@DBOS.step()` (each agent call) · DBOS queues (lanes, priority, concurrency, dedup) · `DBOS.sleep()` (durable backoff) · `DBOS.send()`/`recv()` (two-way radio, human-in-the-loop). Only dependency: Postgres.
**Queue/router (if hand-rolled):** `SELECT … FOR UPDATE SKIP LOCKED` claim · `ORDER BY priority DESC, est_duration_s ASC` · duration-class lanes with dedicated workers · lock keys for serialization · `depends_on` for ordering.
**Radar:** append-only `task_events` · `SELECT DISTINCT ON (task_id)` for current state · FastAPI **SSE** (`sse-starlette`) over Postgres `LISTEN/NOTIFY` · gauges: queue depth, in-flight, oldest-age, throughput, failure rate · OpenTelemetry GenAI spans keyed by `task_id`.
