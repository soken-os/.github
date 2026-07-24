# Phase 3 Packet Queue — next self-dispatched build tasks

**Status:** Draft contracts for Codex adjudication, 2026-07-24. Follows the bootstrap acceptance (decision doc Appendix G).
**Rule of the lane:** every packet below is *built by a CEC-dispatched worker*, per the bootstrap criterion. Until packet P2 lands, seeding still requires Codex-at-the-terminal (the known seam, named in the Appendix G precision amendment); P1+P2 exist precisely to close that seam.

## Ordering rationale

Gap-closers first, hardening second: P1 (always-on engine) and P2 (terminal-free seeding) remove the human "press start" from *every subsequent packet*, including the hardening ones. F1 rides immediately after because it's the one live defect the maiden flight surfaced. E4/E1 follow. Phase 3 proper (pull queue, dependencies, resource locks, routing) begins once the lane runs terminal-free.

---

## P1 — Always-on service (launchd)

**Objective:** the CEC service runs as a macOS `launchd` agent — starts at login, restarts on crash, no human-typed start command. Deliverables: a `launchd` plist template (labels, `KeepAlive`, `caffeinate`-equivalent assertions or `ProcessType` settings, log paths under `phase3/runtime/logs/`), an install/uninstall script pair, and a doc section describing status/stop commands. The service refuses to start without valid Phase 0/1 receipts (existing gates unchanged).
**Allowed paths:** `reference/cec/phase3/launchd/` (new files allowed in this path only), `reference/cec/phase3/README.md`.
**Evidence:** dual artifact (test output + diff); tests include plist XML validation and install-script dry-run.
**Risk note:** install/uninstall scripts touch `~/Library/LaunchAgents` — *outside* the worktree. The scripts are **produced** by the worker but **executed** only by a human (Scott/Codex) this round; the packet forbids the worker from running them. Verifier confinement unchanged.

## P2 — Terminal-free seeding (watched intake directory)

**Objective:** the service watches `reference/cec/phase3/intake/` in the *live clone*; a schema-valid packet JSON file dropped there is validated (existing `PACKET_SCHEMA` + `assert_no_secrets_in_packet`), seeded into the registry exactly once (content-addressed `work_item_id` from the packet hash — duplicate drops are no-ops), and the file is moved to `intake/accepted/` or `intake/rejected/` with a reason file. This makes seeding possible from anywhere that can commit a file (Scott's phone via GitHub, Claude's session) with `git pull` on the Mac as the only transport — and the service does the pulling on a timer (read-only `git pull --ff-only` of `main`; a conflict or non-ff simply skips the cycle and surfaces on the next notification).
**Allowed paths:** `reference/cec/phase3/` (service/controller/packet modules + new `intake.py` + tests). `new_files_allowed: true` scoped to `reference/cec/phase3/`.
**Evidence:** dual artifact; tests cover accept, reject (schema violation, secret hint), duplicate-drop idempotency, and the ff-only pull guard.
**Boundary preserved:** the machine still mutates nothing on GitHub — it only *reads* (`git pull`). Publication of results remains human-gated.

## P3 — F1: deterministic notification delivery tick

**Objective:** pending notification-outbox rows are delivered by a delivery tick that runs every service cycle *independent of work-item stage*, closing the defect where a terminal row's notification required a manual call after restart. Retried delivery stays idempotent; delivery failures increment an attempts counter and never block reconciliation.
**Allowed paths:** `reference/cec/phase2/notifications.py`, `reference/cec/phase3/service.py`, `reference/cec/phase3/controller.py`, plus tests in either phase's test dir.
**Evidence:** dual artifact; tests include "COMPLETE row with PENDING notification + fresh service start ⇒ delivered without touching the work item."

## P4 — E4: worktree-scoped worker Bash

**Objective:** confine the bootstrap worker's Bash to worktree-rooted commands (allowed-tool patterns or macOS `sandbox-exec` profile), retiring the blanket `bypassPermissions` caveat. Gate: required before any packet above `ROUTINE` authority is seeded.

## P5 — E1: verifier type-change hardening

**Objective:** the evidence verifier rejects symlink/submodule type-changes via `git diff --raw` mode bits, closing the one hostile case the bootstrap verifier doesn't cover.

---

## Adjudication questions for Codex

1. P1: `launchd` agent vs. keeping `caffeinate` + manual start until P2 proves the intake loop — is always-on safe *before* Bash is scoped (P4)? Counter-proposal welcome on ordering P4 before P1/P2.
2. P2: content-addressed `work_item_id` from packet hash — confirm as the dedup key, or prefer an explicit `packet_id` field?
3. P2: the timer-driven `git pull --ff-only` makes the Mac clone a consumer of `main` — any custody concern with the service mutating its own working copy while worktrees are pinned at `starting_ref`? (Claude's position: none, since workers never run in the live clone and worktrees detach at a SHA.)
4. P3: deliver-tick placement — service loop vs. a separate `launchd` timer job?
5. Confirm P4-before-any-elevated-authority as a hard gate, and whether P4 should jump ahead of P2 entirely.

*After adjudication + Scott's approval, packets are seeded one at a time (Codex-at-terminal until P2 lands, then intake-file drops), each reviewed on content like `70abd62` before publication.*

---

## Adjudicated resolutions (Codex memo, PR #3 comment; Claude reconciliation — 2026-07-24)

**Locked order: P4 → P3 → P1 → P2 → P5.** Claude's gap-closers-first ordering is withdrawn; Codex's principle governs: *"the next leap is not more autonomy; it is safer autonomy."* Always-on (P1) and self-feeding (P2) expand unattended runtime, and neither may arrive while the worker still runs blanket `bypassPermissions` (P4) or while a known notification defect could go silent unattended (P3).

**Locked gates:**
- Until P4 lands: only ROUTINE packets may be seeded; no elevated-authority packet, no always-on install, no watched-intake seeding.
- P4 V1 scope is the *smallest enforceable control* that retires blanket permissioning (allowed-tool patterns or `sandbox-exec`), acceptance-tested both ways: worktree-rooted command succeeds, out-of-worktree read/write fails. Not a general sandbox.
- P1 install/uninstall scripts are worker-produced, dry-run-tested, human-executed this round; service refuses startup on invalid receipts.
- P2 identity is the canonical packet-content hash; optional `display_id` is for humans/notifications only and never identity — same `display_id` with different content is a distinct item plus a warning. Before any `git pull --ff-only`, the service verifies the live clone is clean and on the expected branch; otherwise skip the cycle and surface a recovery notification. The clone never edits/merges/rebases/commits/pushes/runs workers.
- P3 delivery tick runs inside the service loop under single-controller custody — never a second scheduler ("two lifecycle owners create race classes"). Acceptance: `COMPLETE` row + `PENDING` notification + fresh service start ⇒ delivered without touching the work item.
- P5 lands before any packet above ROUTINE authority but does not block P1/P2 once P4 is in.

**APPROVED by Scott, 2026-07-24** (relayed via Claude session; "locked decision"). The P4 packet is authored (`p4_packet()` in `reference/cec/phase3/packet.py`, seeded via `seed_p4.py`, work item `phase3-p4-bash-scoping`): the machine's second self-built change confines its own worker's Bash to the task worktree, with pre-named new-file paths so the verifier's exact-path allow-list stays enforceable, mechanism choice (allowed-tool patterns vs `sandbox-exec`) left to the worker per the adjudication, and acceptance tests required in both directions. It is, by design, the last packet whose worker carries `bypassPermissions`.

### P4 acceptance record (2026-07-24)

**P4 reached `COMPLETE` through the CEC lane** at 13:03:39 UTC — `lease_epoch=1`, `recovery_attempts=0` (no churn; contrast the bootstrap's five lease turnovers on an easier task). Evidence verified: exactly the four pre-named paths changed; test output `32 passed, 7 skipped` (SHA `f9680b69…`); diff SHA `51524bed…`. The worker **chose `sandbox-exec` and rejected allowed-tool patterns** with correct reasoning: patterns gate *prompting*, not execution, and are waived under `bypassPermissions` — so only an OS-level control actually retires the caveat. Completion notification row correctly `PENDING` (the known F1 defect; P3 next). Publication human-gated pending Claude's content review.

### P4 publication review (Claude, 2026-07-24) — APPROVED

Reviewed the machine's diff (`e8d8a2a`, applied verbatim; regenerated diff byte-matched the artifact SHA `51524bed…`). This got the series' sharpest read because it's security code. Findings:

- **The boundary is real, not cosmetic.** `deny file-write*` then re-`allow` only three named subtrees, relying correctly on SBPL last-match-wins. Writes outside the worktree are denied *by the kernel*, not by the CLI's honor system — which is exactly the point, since the worker runs `bypassPermissions` and the CLI's own gates are waived.
- **Child-process escape closed:** the sandbox wraps the whole CLI process and is kernel-inherited by every shell it spawns, so a Bash tool call cannot step outside it. This was my primary attack concern; it holds.
- **The canonical-path trap is handled:** every `-D` path is `realpath`'d before being passed, because the kernel evaluates canonical paths (`/var` → `/private/var`) — an un-resolved path would have silently matched nothing and left writes unconfined. Both the adapter and the tests resolve. This is the subtle bug that sinks most first-attempt macOS sandbox profiles; the worker caught it and documented why.
- **Tests prove enforcement, not assumption:** they run the *shipped* profile via the same wrapper the adapter composes, asserting a worktree write succeeds and an outside write is denied — with `home_state`/`proc_tmp`/`outside` as distinct siblings so "outside" is genuinely covered by no allow rule. Correctly skipped off macOS.
- **The limits doc is exemplary and load-bearing.** It states plainly what this does *not* do — reads/exec/network unconstrained (no exfiltration defense), `HOME_STATE`/`PROC_TMP` trusted, the linked-worktree gitdir deliberately outside the boundary so the *worker cannot `git commit`* (only the controller commits — a property that reinforces the custody model), and that this is defense-in-depth for *where writes land*, not a replacement for the controller's post-hoc allow-path diff check. Nothing hidden.

**Verdict: correct, honest, and scoped exactly to E4.** Suite: 32 passed on the Mac; 29 passed + 4 skipped (sandbox+Postgres) in the Linux review sandbox. Approved for merge. With this, every future worker's writes are OS-confined to its worktree, and the blanket-`bypassPermissions` caveat is retired — the P4 gate opens for P1/P2/elevated-authority packets.

---

## P3 failure + controller findings (Claude, 2026-07-24)

P3 (`phase3-p3-notification-tick`) did **not** reach COMPLETE. It exposed two real controller bugs and one worker-environment symptom. The failure is high-value: the machine hit the exact *live-but-silent worker* case its custody model exists for, and found the Phase-3 controller never wired in the reclaim logic the locked design (A1/B1, expired_lease_action) specified.

**What happened:** the worker ran ~20 min under the new P4 sandbox, emitted no stdout/stderr/diff/artifacts (live-but-silent). The controller observed it `RUNNING` and kept writing `WORKER_HEARTBEAT` transitions that advanced `next_signal_deadline` but **not** `lease_expires_at`. Once the initial 20-min lease expired, the next heartbeat's `updated_at=clock_timestamp()` fell past `lease_expires_at`, Postgres rejected the row on `continuation_deadlines_valid`, and the **whole service crashed**. Row left `EXECUTING` with an expired lease.

### Findings (fixed this round unless noted)

- **G1 — heartbeat renewed the signal but not the lease; no reclaim of a past-budget worker (fixed).** The `RUNNING` branch now renews **both** `lease_expires_at` and `next_signal_deadline` from observed liveness (the locked controller-renews-from-liveness model), *bounded* by a runtime budget (3× `estimated_duration_seconds`). A live worker past budget is fenced, terminated, and `_reclaim_to_recovery`'d into a human `PARKED` lane rather than heartbeat-renewed forever. A non-running worker whose lease has expired with no result is likewise reclaimed instead of spinning on `RESULT_MISSING`. This both eliminates the crash and gives the silent-worker case a real mechanical exit.
- **G2 — a single item's rejected transition crashed the entire controller (fixed).** `run_scan_once` now contains any `reconcile_once` exception (CHECK violation, stale CAS, adapter error) to that item, logs it, and continues the loop. The DB constraint is a backstop, not control flow; one wedged item must never take down custody of all others. *(This is the structural fix — it makes any future domain-logic bug non-fatal.)*
- **G3 — root cause of worker silence: NOT yet fixed, needs Mac investigation.** The worker produced nothing for 20 min — the first task to run under the P4 sandbox. Prime suspects, all Mac-only to confirm: (a) the sandbox denies the worktree's **gitdir** (`.git/worktrees/<name>/`, outside the worktree subtree), so a sandboxed worker cannot `git add` new files, and even index-stat refresh on `git diff` writes there; (b) the editable-installed `reference` package resolves to the **live clone** outside the worktree, so `__pycache__`/test writes land outside and are denied. P3 is the first task creating new files *and* running git/pytest under confinement. The fix likely grants the worktree's gitdir as a fourth `-D` subtree and reconciles new-file diff capture (see E4/new-file note below). Until G3 is understood, the controller fix ensures the silent worker is *reclaimed and escalated*, not crashed.

### P4 sandbox cross-review — union of three independent review passes

Two external Claude sessions plus this one reviewed the merged P4 confinement; all reached "boundary is real, land it," but the sharp findings appeared in only one pass each — a clean vindication of the union-not-winner protocol.

- **E4-a — `HOME_STATE` is a code-execution persistence surface (sharp; not fixed).** The profile grants writes to the *entire* `~/.claude` tree, which holds `settings.json` (hooks = arbitrary shell on next launch), `commands/`, `agents/`. A confined worker can plant a hook that runs **unconfined, full-privilege, on the next Claude launch** — a write-to-execute escape from the sandbox, landing *outside* the worktree so it never appears in the controller's diff. *Fix:* point a per-worker `CLAUDE_CONFIG_DIR` at a disposable state dir and grant only that; keep the real `~/.claude` read-only inside the sandbox.
- **E4-b — silent fail-open (not fixed).** If `sandbox_available()` is False on the Mac (profile missing, `sandbox-exec` removed — Apple has deprecated it), `sandbox_wrap` returns argv unchanged and the worker runs unconfined with no log. *Fix:* warn on any unconfined worker launch; let a packet mark confinement `required` (fail-closed) for sensitive task classes. (Distinct from the *canonicalization* path, which correctly fails **closed** — a bad `-D` matches nothing and denies all. The control fails closed on a bad param, open on a missing sandbox; both sentences belong in the doc.)
- **E4-c — per-adapter enforcement asymmetry, undisclosed (not fixed).** `sandbox_wrap()` is wired only into `ClaudeCodeAdapter`. `CodexAdapter` relies on its own `--sandbox workspace-write`; `ScriptAdapter` gets nothing. "Scope and limits" does not disclose that confinement is Claude-only. *Fix:* document the asymmetry, and decide whether the Codex/Script lanes need equivalent OS-level confinement before they run elevated-authority packets.
- **New-file / diff completeness (pre-existing, now urgent):** `write_unified_diff` uses a read-only `git diff <ref>` that omits untracked files, while `evidence.py` counts them via `ls-files --others`. New files thus appear in `files_changed` but not in the diff artifact. P4 forecloses the obvious `git add` fix (gitdir denied), so the two must be reconciled before any task legitimately creates new files — which is exactly P3's case.

### Consequence for the queue

**G1/G2 are a hand-built kernel fix** (a broken controller cannot safely dispatch its own repair — the bootstrap-circularity rule). Recorded here; committed with regression tests this round. **G3 + E4-a/b/c** are the next work and gate the lane: P3's own notification-tick task cannot be re-run to green until G3 (worker silence under the sandbox) is root-caused on the Mac, because the sandbox is what silenced the worker. Recommended order: land G1/G2 → root-cause G3 on the Mac → fix E4-a (narrow `CLAUDE_CONFIG_DIR`) + grant worktree gitdir + reconcile new-file diff capture as one bundle → re-seed P3.

### G1/G2 acceptance (Codex review, 2026-07-24)

**SHIP / no blockers.** Codex reviewed PR #4 head `dd8f70c` and verified all five points against real Postgres: G1 heartbeat renews both lease and signal; G1 bounded by `3× estimated_duration_seconds`; G1 expired/no-result worker reclaims to `PARKED`; the reclaim row satisfies `continuation_deadlines_valid` (`lease_expires_at > updated_at`, `next_signal_deadline >= updated_at`) confirmed by direct DB check; G2 per-item exception containment holds. Source suite `36 passed, 7 skipped`. Two reviewers concur (Claude built + unit-tested; Codex ran + DB-checked).

- **G4 — DB-backed expired-worker regression (tracked follow-up, deferred).** The shipped regression tests exercise transition construction and a stubbed controller, not a full DB-backed `reconcile_once` against an expired worker; Codex verified the constraint manually this round. Codify a Mac-only DB-backed test (seed an `EXECUTING` row with an expired lease → assert the old signal-only renewal is *rejected* by `continuation_deadlines_valid`, the new both-deadline renewal is *accepted*, and the reclaim `PARKED` row is accepted) alongside the other `*_postgres.py` suites. Non-blocking; do before the harness is leaned on for unattended multi-item runs.

### G3 root-caused + fixed (Claude, 2026-07-24)

Codex confirmed G3 on the Mac with an exact kernel denial: `deny file-write-create .../.git/worktrees/<name>/index.lock`. A linked worktree's git operations write `index`/`index.lock` into its *external* gitdir, outside `WORKTREE_ROOT`, so the P4 profile denied them and silenced the worker. (site-packages was *not* a natural failure — bytecode was cached — so per Codex's recommendation it is deliberately **not** granted.)

**Fix (this round), scoped tighter than a blanket gitdir grant:**
- `sandbox_wrap` adds a 4th `-D GITDIR_ROOT` = the worktree's own gitdir (`git rev-parse --absolute-git-dir`, realpath'd), and `worker.sb` grants that subtree. It grants **only** the worktree gitdir, **not** the shared object store (`.git/objects/`).
- Diff capture uses **intent-to-add** (`git add -AN`) in `write_unified_diff`, and the packet objectives instruct the worker to run the same `git add -AN` before its diff (so `diff_sha256` still matches byte-for-byte). `-N` writes only an index entry, never a blob — so it needs just the gitdir, and the object-store denial means the worker still **cannot commit content**. This simultaneously closes the pre-existing new-file diff-completeness gap (untracked new files now appear in the artifact) while preserving "only the controller commits."
- `worker-bash.md` limits section updated for honesty (gitdir now granted; object store still denied and *that* is what enforces the commit-custody property).

**Deliberately NOT bundled:** E4-a (`CLAUDE_CONFIG_DIR` narrowing) touches the worker's auth/state and needs its own careful validation; E4-b/E4-c are hardening. Bundling them into the P3 convergence run would confound the signal. They remain the next hardening round. This fix is the minimal, targeted change to unblock P3.

**Next:** review this G3 fix, then re-seed P3 — the convergence run (clean/doc-nit ⇒ through the spike, resume queue; crash-class ⇒ stop and dig).

### PR #5 correction after Codex Mac review (Claude, 2026-07-24)

Codex's DO-NOT-SHIP review was correct on both blockers; conceding cleanly.

- **My `git add -N` claim was wrong (conceded).** I asserted intent-to-add writes only an index entry, no blob. Codex proved on the Mac that `git add -AN` inside the sandbox fails with `fatal: cannot create an empty blob in the object database` — it *does* attempt an object-store write, and also touches root `.git/packed-refs.lock`. So the whole `git add -AN` new-file-capture mechanism is reverted, in `write_unified_diff` and all three packet objectives (back to plain `git diff <ref>`).
- **What survives and is correct (Codex-verified):** the `GITDIR_ROOT` grant itself — narrow to the worktree's own gitdir, object store still denied (`mkdir .git/objects/... → Operation not permitted`), commit-custody preserved. This is the piece that fixes the *actual* P3 silence, because a plain `git diff`'s index-stat refresh writes `index.lock` in the (now-granted) worktree gitdir. Kept.
- **P4 sandbox tests fixed (BLOCKER #5):** the helper now passes the 4th `-D GITDIR_ROOT` param (the profile requires it; a missing param made SBPL reject the rule with "expected pattern, got boolean"). Added a `gitdir` sibling to the fixture and a GITDIR_ROOT assertion to the argv test.

**D2 — new-file diff capture under confinement (OPEN, needs Mac-verified design).** Untracked new files still cannot enter the diff artifact without an object-store write, which the sandbox rightly denies. Candidate mechanism to verify *in isolation on the Mac before wiring in*: `git diff --no-ext-diff --no-index --src-prefix=a/ --dst-prefix=b/ /dev/null <file>` per sorted untracked file (a pure file diff, no `.git` writes), concatenated after the tracked `git diff <ref>`, run identically by worker and controller so `diff_sha256` still matches. **Process change to stop shipping blind:** sandbox-behavior mechanisms get a targeted Mac probe *before* being committed as the fix, not after.

**Effect on P3:** with `GITDIR_ROOT`, the P3 worker no longer goes silent and the run should COMPLETE (evidence verifies via `files_changed` + tracked-diff sha match). Its diff artifact will omit its two new files until D2 lands — a publication-completeness gap, not a lane failure. The convergence run still validates the lane end-to-end; publishing P3's new files waits on D2 (or a rescope of P3 to existing files only).

## P3 convergence run #2 — control plane converged; two non-crash gaps (Claude, 2026-07-24)

Ran on merged `main` (`9026f36`). Gates green (Phase 0: 9, coverage 100%, orphan 0; Phase 1: 24/24). **Read carefully: this is progress, not a regression — severity decayed from crash-class to two contained, fixable gaps.**

**What converged (the hard part):**
- **G3 fixed** — the worker was NOT silent; it ran and produced a typed claim. The startup strangle is gone.
- **G1/G2 fixed** — the controller did NOT crash; it contained everything and stayed up.
- **Evidence integrity held** — the worker returned `status: needs_input` with a 0-byte diff and zero-hash placeholders, and the controller **refused to mark it COMPLETE** (`worker did not claim a completed result`). No false completion. This is the core thesis working.

**Two remaining gaps, both non-crash:**

- **G5 — controller wedged a non-done claim in VERIFYING (fixed this round).** The worker's `NEEDS_INPUT` ("I'm blocked, help") was pushed through evidence verification and rejected, leaving the row stuck in `VERIFYING`. Fix: the EXECUTING branch now routes `NEEDS_INPUT`/`FAILED` claims straight to the human recovery lane (`_reclaim_to_recovery`), and a `CodeEvidenceRejected` in the VERIFYING branch reclaims instead of raising (which had wedged the row under the G2 catch). Regression tests added. **The already-stuck row will unwedge on the next controller pass after this deploys.**

- **G6 — sandbox still one grant too tight (open; needs Mac).** The worker hit `EPERM` on `/private/tmp/claude-501` — the Claude CLI's `/tmp/claude-<uid>` scratch/IPC dir, which `PROC_TMP` (`/var/folders/...`) doesn't cover. Codex couldn't reproduce it *outside* the sandbox (mkdir succeeded), confirming it's OUR profile denying it, not the OS. This is G3's sibling.

### Strategic change: enumerate the sandbox's needed grants ONCE (stop the per-run whack-a-mole)

G3, D2, and G6 are all the same shape: the P4 sandbox was built without a real editing worker exercising it, so we keep discovering one required grant per convergence run — an expensive round-trip each time. **Proposed one-time Mac discovery pass:** run a *complete* real worker turn under the shipped `worker.sb` with `log stream --predicate 'sender=="Sandbox"' --info` capturing **every** deny, producing the full list of paths a real `claude -p` turn needs. Then grant them all at once (each assessed for safety — e.g. `/tmp/claude-<uid>` is a narrow per-uid scratch dir, fine; the object store stays denied), and the sandbox is *correct* rather than iteratively approximated. This converts N convergence rounds into one discovery + one fix.

**Next:** (1) merge G5; (2) one Mac discovery pass to enumerate all sandbox denials for a full worker turn (G6 + anything else); (3) grant the enumerated set; (4) re-run P3. Expected then: COMPLETE (diff artifact still omits new files pending D2, a publication-only gap).

## G6 sandbox denial enumeration — one-pass discovery (2026-07-24)

The one-time discovery pass worked: instead of one grant per convergence run, Codex captured the FULL set of paths a real `claude -p` turn needs under the shipped `worker.sb`. Key finding: the profile blocks *before any worker Bash runs* — the first hard blocker is the CLI's per-turn Bash scratch dir `/private/tmp/claude-<uid>/.../<session-id>`, which `PROC_TMP` (`/var/folders/...`) doesn't cover.

**Full needed-write set, triaged by safety:**
- **Grant (ephemeral, no persist-to-execute):** `/private/tmp/claude-<uid>` (Bash scratch — the first blocker), `/private/tmp/zsh*`, `/private/tmp/xcrun_db-*`, `~/Library/Caches/claude-cli-nodejs`.
- **E4-a-coupled (config → potential write-to-execute via MCP-server defs):** `~/.claude.json` (+ `.lock`, `.tmp.*`). **Decision:** prefer redirecting `CLAUDE_CONFIG_DIR` to a disposable per-worker dir so the real `~/.claude.json` is *not* granted (closes the door and satisfies E4-a simultaneously); only if that breaks auth, grant the `.claude.json` family as a **ROUTINE-only** allowance (consistent with the P4 gate: nothing above ROUTINE until hardening). This folds G6 and E4-a into one fix.
- **Ignore (OS-layer noise, non-blocking; turn completed despite them):** `mach-lookup com.apple.*`, `sysctl-read *`, `forbidden-exec-sugid`, `/dev/dtracehelper`. These are the Claude.app's own sandbox layer, not `worker.sb`.

**Build ownership (inverted for this step):** the profile is SBPL that cannot be verified off the Mac, and Codex has a Mac-proven augmented profile. So **Codex writes the `worker.sb`/`sandbox_wrap` change and Claude reviews** it for scope/security (object store stays denied; out-of-worktree source boundary intact; prefer `CLAUDE_CONFIG_DIR` isolation over granting real config). The verifiable party holds the pen; the reviewer sets the guardrails. This is the fix for the "ship unverified sandbox change → bounced" cycle.

**After G6 merges:** re-run P3. Expected COMPLETE (control plane already converged: G1/G2/G3/G5). Diff artifact still omits new files pending D2 (publication-only gap).
