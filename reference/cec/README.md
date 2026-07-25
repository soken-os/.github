# CEC reference — worker adapters

Reference implementation of the **worker-adapter layer** locked in
`../../docs/executor-dispatch-decision.md` §5. This is the piece that lets the
dispatcher "spin up any model": a uniform interface with one implementation per
worker, so adding a third model is one new class, not a rewrite.

These files are **drop-in reference for the Mac bridge**, not a package meant to
run inside this repo. Stdlib only, no third-party dependencies.

## Files

| File | What it is |
|---|---|
| `contracts.py` | The locked `WorkerAdapter` Protocol + data types (decision doc §5), transcribed so the adapters compile standalone. |
| `adapters.py` | `ClaudeCodeAdapter`, `CodexAdapter`, `ScriptAdapter` implementing that Protocol over the headless CLIs. |

## How it fits the design

The adapter is the **translation layer only**. It:
1. `launch()` — starts one bounded, headless worker turn (`claude -p …` /
   `codex exec …`) as a crash-surviving subprocess, writing sidecar files under
   `<cwd>/.cec/<command_id>.*` so a restarted controller can still observe it.
2. `observe()` — reports OS process state (`RUNNING` / `EXITED` / `MISSING`)
   from the process table + sidecars, never from model prose.
3. `collect_result()` — parses the CLI's **typed** output into a
   `WorkerResultClaim`. Empty or invalid output → `None` (no claim), so a silent
   worker can never become a false "done".
4. `terminate()` — stops a stale/fenced worker (SIGTERM→SIGKILL on the process
   group) before the controller redispatches.

The adapter **never** decides completion and **never** touches `work_items`. It
emits a *claim*; the controller verifies evidence and transitions state.

## Findings from the decision doc baked in

- **A3 (who renews):** adapters do not self-renew leases and expose no
  worker-side heartbeat. The controller renews from observed liveness. The
  worker's `lease_epoch` only rides along on the claim, for fencing.
- **A4 (secrets out-of-band):** `assert_no_secrets_in_packet()` rejects any
  packet field that looks like a credential. Provider keys
  (`ANTHROPIC_API_KEY`, `CODEX_API_KEY`, …) come from the environment the CLI
  inherits — never from the persisted packet.
- **A1 (no destructive inference):** `observe()` reports `MISSING` when it
  cannot tell whether a worker ran. The controller's `decide()` must treat
  `MISSING` / observer-error as *possibly alive* and must not redispatch on it.
  That gate lives in the controller, not here — see decision doc Appendix A1.

## Running on the Mac

Requires the `claude` and/or `codex` CLIs on `PATH` and their keys in the
environment. Example wiring (controller side, sketch):

```python
from cec.contracts import WorkerCommand, WorkerKind
from cec.adapters import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter()
handle = await adapter.launch(command)  # command built by the controller
obs = await adapter.observe(handle)  # poll from the reconcile loop
claim = await adapter.collect_result(handle, command)  # -> WorkerResultClaim | None
```

## Known productionization edges (called out, not hidden)

- Worker detachment uses `start_new_session=True`; a true double-fork /
  process-supervisor is a later hardening.
- Exit-code capture is best-effort (a reaper task). If the controller dies
  mid-run the exit file may be absent; `observe()` then falls back to
  `EXITED` (output present) or `MISSING` (nothing observable).
- `observe()` resolves sidecars relative to the current working directory. A
  production controller should pass the work item's `working_directory`
  explicitly rather than assuming `cwd`.
