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

---
<!-- Sections 5 (durable-execution engine choice), 6 (queue/router/lanes),
     7 (scorecard/radar + two-way radio), 8 (build phases), and 9 (recommendation)
     are appended after the deep-research passes complete. -->
