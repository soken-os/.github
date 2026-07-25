# Session Handoff — 2026-07-25

Written mid-session, before a context window boundary, so nothing gets lost to
compaction. Read this first if you are picking this work up fresh — it is
self-contained; you should not need the full conversation history to continue.

**Read `docs/executor-dispatch-decision.md` (the locked decision + Appendices
A–H) as the canonical technical record.** This document is the *operational*
picture on top of it: what's merged, what's open, what Scott has decided, and
exactly what to do next.

---

## 0. The one-paragraph version

Two repos: **`soken-os/.github`** (CEC — the executor control plane, in
`reference/cec/`) and **`soken-os/soken`** (the Sōken product, plus the *live*
`soken_circuit` executor it's being replaced by). CEC's single-lane return leg
is proven (Appendix H). Today's work hardened it into **Option B: one CEC, one
shared registry, one controller process per program** — locked by Scott — and
proved multi-lane concurrency for real. `soken_circuit`'s three worst
loop-reliability bugs are fixed and merged. One PR (#18, metrics) is open and
unreviewed. The next hard gates are Mac-side. The 10-reviewer adversarial fleet
is queued behind those gates and **requires Scott's explicit go-ahead** before
firing — do not run it preemptively.

---

## 1. Cast of characters

- **Scott** — the human. Owns the repo-placement and architecture calls
  explicitly (see §2). Reachable via chat; not on his Mac at all times.
- **This session (Claude)** — builds in this container (Linux), no access to
  Scott's Mac, no access to a Codex channel directly.
- **Codex** — the adversarial reviewer, working from Scott's Mac. Reviews every
  PR, posts findings as PR comments (which arrive here automatically via GitHub
  webhook — see §6), and runs the things only the Mac can run (live Postgres at
  `127.0.0.1:55432`, macOS `sandbox-exec`, launchd, the phone-alarm drill).
- **The relay is one-directional.** Codex's comments reach this session
  automatically. Nothing goes from here to Codex automatically — Scott must
  paste a message to Codex himself. Every turn where Codex needs to act, give
  Scott an explicit **"paste to Codex"** block, not just a description.

---

## 2. Locked decisions (do not re-litigate these)

1. **Repo placement rule** (`CLAUDE.md` in both repos): never create a new
   GitHub repo, and never place a new product/system inside an existing
   product's repo, without Scott's *explicit* permission. This rule exists
   because CEC's placement in `soken-os/.github` was **never actually
   chosen** — it's where the session happened to open. Scott has explicitly
   *not* asked for a migration yet; CEC stays in `.github` **while it
   hardens**, and moves to its own repo **at cutover** (Scott's call, not yet
   exercised — see §7).
2. **Execution model: Option B — locked.** One CEC, one shared Postgres
   registry, **one controller process per program** (Sōken, Carlson OS
   Roofing, field capture, CEC itself, and any future build). Each program's
   controller serves its own repo; all speak to the one registry, so there is
   one place to see everything and one codebase to harden. Scott's own words:
   *"I think we should have one controller per program... that controller
   should all speak back to a main Executor."* Rejected alternatives: (A) one
   controller, all programs (blocked by the cwd-bound worker sidecar path,
   would need custody-identity surgery); (C) cloned CEC per program (rejected
   — N divergent forks, no global view, contradicts "one place to diagnose
   failure").
3. **Performance metrics are a first-class requirement.** Scott: *"we can't
   make this world-class unless we have performance metrics."* Built as a
   read-only reporting layer (see §5, PR #18) derived entirely from the
   existing event ledger — no new instrumentation.
4. **Evidence-verified completion, always.** A model's prose is a *claim*
   (`RESULT_CLAIMED`), never proof. The controller mechanically re-verifies
   (re-hash the diff, re-hash every new file, re-run the gate) before
   `COMPLETE`. This is why Appendix H had to be corrected (see §4, H1) —
   Codex caught this session overstating what had actually run.
5. **The 10-reviewer adversarial fleet is gated on Scott's go-ahead.**
   Multiple explicit confirmations this session: fire it only after (a) the
   "CEC build finished" gates below are met, AND (b) Scott has been shown the
   evidence and says go. Do not self-trigger it.

---

## 3. Repo map

| Repo | What's there | Notes |
|---|---|---|
| `soken-os/.github` | CEC: `reference/cec/{phase0,phase1,phase2,phase3}`, `docs/executor-dispatch-decision.md` (canonical record, Appendices A–H) | Has its own CI now (`ci/cec`, PR #16) — full proof chain incl. Postgres, ~55–65s |
| `soken-os/soken` | The Sōken product (`services/api`, `apps/web`) + the **live** `soken_circuit` at `tools/circuit/` | Full CI (`ci/python`, `ci/typescript`, `ci/cross-tenant-isolation`); circuit checks ride inside `ci/python`, ~53 min for a circuit-only change — a known, accepted cost until cutover |

**Do not confuse `soken_circuit` (live, in `soken-os/soken`) with CEC (the
reference build, in `soken-os/.github`).** They are different systems. CEC has
never touched the product repo.

---

## 4. What's merged (both repos), in order

### `soken-os/.github` (CEC)
| PR | What | Status |
|---|---|---|
| #12 | G8 — skip capability-blocked tests inside the worker sandbox instead of failing them | merged |
| #13 | D2/G8-3 — bind new files' bytes into verified evidence (controller re-hashes from worktree) | merged |
| #14 | Appendix H — P3/F1 acceptance record | merged, **amended once** (see H1 below) |
| #16 | `ci/cec` — CEC's first CI gate, full Postgres proof chain | merged |
| #17 | `.gitignore` fix for root-level `__pycache__` | merged |
| #15 | Multi-program routing (Option B foundation) + multi-lane concurrency proof | merged, went through **two rounds** of real findings (see below) |

**Appendix H / finding H1 (Codex, corrected by this session):** the record
originally said "the F1 fix was dispatched end-to-end." Wrong — what ran was
**the packet that builds F1**, not F1 itself. At COMPLETE, the notification
outbox row was still `PENDING`; the diff was never committed/pushed. Amended to
separate **(a) the lane itself — proven** from **(b) F1 as shipped behavior —
NOT proven**, still needing publication + a live restart-delivery proof. This
is the canonical record now; don't re-claim F1 is done.

**PR #15 findings (Codex, both rounds — all fixed):**
- **M1** — `service.py` had no `--program` flag; every process defaulted to
  CEC, so Option B was unreachable in practice. Fixed: `--program`, factored
  `build_controller()`.
- **M2** — the guard checked `work_items.program` but not `work_packet.program`
  — a CEC-routed row could carry a roofing packet. Fixed: `reconcile_once`
  refuses on `PACKET_PROGRAM_MISMATCH` before any side effect. Legacy
  no-program packets still inherit the row.
- **M3** (took two rounds) — `unserved_programs()` computed the orphan
  condition but **nothing called it in production**; a typo'd program was
  still silently invisible. Fixed with `reference/cec/phase3/sentinel.py`: a
  real CLI that takes an *explicit* configured-program set, exits 22 +
  notifies on orphaned work, and **fails closed (exit 2)** if given no config.
  It flagged real residue on its first run.
- **M4** — "one controller per program" was a comment, not a mechanism. Fixed
  with `reference/cec/phase3/singleton.py`: an OS advisory file lock per
  program (acceptable for V1 while everything's on one Mac; Codex explicitly
  flagged that multi-host would need registry-held ownership instead). Minor
  hardening: sanitized lock filenames could collide (`a/b` and `a?b` both →
  `a_b`) — fixed with a sha256 digest suffix.
- **M5** — the cross-program guard test re-implemented the predicate instead
  of driving the real controller. Fixed: real `BootstrapController` with
  `create_worktree`/`adapter.launch`/`registry.transition` all rigged to raise
  if touched.
- **The multi-lane concurrency proof** (`test_phase3_multilane.py`): 3
  programs × 2 lanes, driven concurrently against the real Postgres registry,
  real CAS/leases, real worktrees, real `verify_code_change_claim`. One lane
  deliberately poisoned (`needs_input`). Proves: no double-dispatch, no
  orphan, exactly one dispatch per lane (M4 under real concurrency), 
  independent custody, and **no cross-lane fate-sharing** — the poisoned lane
  parks while every healthy lane (including its own program's sibling)
  reaches verified COMPLETE.
- **CI-only bug the proof found**: passed locally in 2.8s, failed in CI at
  124s with a lane stuck `EXECUTING`. Root cause: concurrent controllers on
  the SERIALIZABLE registry hit Postgres pivot cancellations
  ("might succeed if retried") and **nothing retried them** — the item
  self-healed on the next tick, but each conflict silently burned a whole
  scan tick. Fixed: bounded jittered retry (3 attempts) on
  `psycopg.errors.SerializationFailure` in `run_scan_once`, deliberately in
  `phase3/controller.py` and **not** `phase1/registry.py` (which is on
  `FORBIDDEN_PATHS` — the locked CAS boundary stays untouched). **Lesson: a
  proof that only runs on one machine cannot find contention bugs** — this is
  why the live N-real-worker Mac run still matters even after this proof is
  green.

Final Codex verdict on #15 (after all fixes): **PASS**, merged. Mac
verification: Phase 0 9/9, continuation coverage 100%/orphan time 0; Phase 1
19/19; targeted Postgres group 17/17 in 2.76s; direct retry probe confirmed
transient success, persistent failure still surfaces, zero retry on unrelated
exceptions.

### `soken-os/soken` (live circuit)
| PR | What | Status |
|---|---|---|
| #268 | Item #2 — `find_task` gate-artifact poison-pill fix + per-task scan isolation | merged |
| #270 | Repo-placement rule (companion to `.github`'s) | merged |
| #269 | Item #1 — liveness observer (dead-man heartbeat + inbox-staleness + launchd) | merged, went through **four rounds** of real findings |

**PR #269 findings (Codex, all fixed):**
- **L1** — inbox stale alarms were **permanently suppressed** after the first
  confirmed notification (dedup key never expired). A task that went stale,
  got claimed, then went stale *again* was invisible forever. Fixed: episode
  re-arm — the alarm clears when the condition is observed to end (task left
  the inbox or was refreshed), not on any timer.
- **L2** — the promised durable `NOTIFICATION_FAILED` event was never
  actually written on transport failure. Fixed: `check_liveness` takes an
  optional `DurableStore` and writes it.
- **L3** — a naive `KeepAlive=true` launchd plist would tight-restart a
  one-shot checker and page on every restart. Fixed: shipped
  `tools/circuit/launchd/com.soken.circuit-liveness.plist` using
  `StartInterval` (periodic, not `KeepAlive`), plus
  `--alarm-cooldown-seconds` (default 1800s) inside the checker itself as
  defence in depth.
- **L4** (final round) — the *scheduler* cooldown had the exact same bug as
  L1: `last-alarm.json` was never cleared, so it suppressed **across**
  distinct outages, not just within one. A second, genuinely new outage
  inside the 30-minute window was silently swallowed. Fixed: a fresh beat
  clears the cooldown marker and appends `SCHEDULER_LIVENESS_CLEARED`.

Final Codex verdict on #269: **PASS**, merged. Codex is now (per its last
comment) proceeding to **install the observer and run the real two-outage
Mac acceptance drill**, restoring the scheduler afterward. Watch for the
result — this is the liveness drill gate closing.

### CI flake diagnosis (found 2026-07-25, fixed in PR #20)

**Root cause:** a timing bug in `test_phase3_multilane.py`'s own test
harness, not in the CEC control plane. Each controller driver thread runs
with a 300-second internal deadline (`time.time() + 300`), but
`_drive_until_terminal` joined those threads with `thread.join(timeout=150)`
— half the internal deadline. Under GitHub Actions' slower/more contended
runners, a controller thread could still be legitimately working past the
150s join window; `join()` returned anyway, the thread kept running in the
background, and the test asserted on incomplete state — hence "poisoned lane
should park, got EXECUTING."

**Evidence:** confirmed via CI job logs on both failing runs — same failure
shape both times (`test_a_failing_lane_does_not_stop_the_others`, "got
EXECUTING"), at 154.53s and 124s respectively, both exceeding or approaching
the 150s join window. PR #19 failing is the tell — it's a doc-only PR, so
the flakiness was already on merged `main`'s test suite, not introduced by
either PR.

**Fix (PR #20):** raise `thread.join(timeout=)` from 150s to 330s (300s
deadline + 30s buffer), and add hung-thread detection — if any driver thread
is still alive after join, it surfaces as an explicit test error rather than
silently asserting on partial results.

---

## 5. What's open right now

**PR #20** (`soken-os/.github`) — fix for the CI flake above. One-file
change, no CEC logic touched. Must merge first to unblock #18 and #19.

**PR #18** (`soken-os/.github`) — `reference/cec/phase3/metrics.py`: a
strictly read-only Executor roll-up (lead time, first-pass yield, estimate
accuracy, park rate, custody churn, per-stage dwell), derived entirely from
`work_items`/`events` with zero new instrumentation and zero writes. Answers
Scott's metrics requirement. **Draft, CI blocked by the flake above** — once
#20 merges and #18 rebases, CI should pass. Not yet reviewed by Codex. Tests
include an explicit "writes nothing" check and a ground-truth check against
the real Appendix-H P3 row (skips cleanly if that row isn't in the local
registry).

**PR #19** (`soken-os/.github`) — this handoff doc. Doc-only, also blocked
by the flake.

**If you're picking this up fresh:** check whether PR #20 has merged. If
yes, rebase #18 and #19 and re-run CI. If #20's CI is still running, wait
for it.

---

## 6. Working patterns established this session (keep using these)

1. **GitHub webhooks are one-directional.** When you open a PR (or someone
   asks you to watch one), this session auto-subscribes and Codex's comments,
   CI failures, merges, etc. arrive automatically as
   `<github-webhook-activity>` messages — you often see Codex's review before
   Scott even pastes it. But **nothing goes from here to Codex
   automatically** — Scott has to paste. So: post detailed review-request
   context as a **PR comment** (not just in chat), then give Scott a short
   **"FOR YOU TO DO — paste to Codex"** block that just points at the PR
   and summarizes the ask. Never make Scott relay a wall of technical detail
   by hand.
2. **Verify against live code/CI before asserting anything.** Multiple times
   this session, an assumption ("it should have 55 tests") was wrong until
   checked against the actual CI log (`mcp__github__get_job_logs`). Always
   check `get_check_runs` / `get_job_logs` rather than trusting a local run to
   generalize to CI (see the SERIALIZABLE retry bug — passed locally, failed
   in CI).
3. **Local Postgres setup for CEC tests, in this container:** Postgres 16 is
   installed but **not started by default**, and the data dir sometimes needs
   permission fixes after container reuse. The working incantation:
   ```bash
   SCRATCH=/tmp/claude-0/-home-user--github/667d3b61-91f9-5a57-a16c-12de9524a4ac/scratchpad
   for d in /tmp/claude-0 /tmp/claude-0/-home-user--github \
            /tmp/claude-0/-home-user--github/667d3b61-91f9-5a57-a16c-12de9524a4ac "$SCRATCH"; do
     chmod o+x "$d" 2>/dev/null
   done
   su postgres -s /bin/bash -c \
     "PATH=/usr/lib/postgresql/16/bin:\$PATH pg_ctl -D '$SCRATCH/pgdata' \
      -o '-p 55432 -c listen_addresses=127.0.0.1' -l '$SCRATCH/pgdata/log' start"
   ```
   Then `CEC_RUN_POSTGRES_TESTS=1 python3 -m pytest reference/cec -q` runs the
   full suite (78 passed, 7 skipped as of this writing — the skips are the
   macOS `sandbox-exec` tests, provable only on the Mac).
4. **`cec.events` is append-only** — a Postgres trigger rejects `DELETE`. Test
   harnesses that seed rows must clean up by transitioning to `CANCELLED`
   (exempt terminal stage), never by deleting. Learned the hard way building
   the multi-lane proof.
5. **Untracked files get lost across branch switches.** Commit early on any
   branch you might switch away from — this bit the multi-lane harness once
   (files had to be rewritten from memory).
6. **Don't guess at repo-relative import paths for worker subprocesses launched
   from inside a worktree.** A worktree checked out at `starting_ref` does not
   contain files added after that commit — invoke harness-only helper scripts
   by absolute path, not `python -m`.

---

## 7. Remaining gates — in priority order

**"CEC build finished" (Scott's gate for the 10-reviewer fleet) requires ALL
of:**

1. ✅ P3/F1 single-lane return leg proven (Appendix H, corrected).
2. ✅ Multi-program routing + multi-lane concurrency proven (PR #15).
3. ⬜ **Live N-real-worker multi-lane run on Scott's Mac** — the thread-based
   proof in #15 exercises registry-level concurrency; it does NOT prove
   separate OS processes / real Claude Code workers concurrently. This is the
   Mac-side analogue of the original P3 acceptance run, scaled to N lanes.
4. ⬜ **F1 publication + live restart-delivery proof** — per the H1
   correction, F1's actual notification-delivery behavor was never exercised
   live. Needs: commit/push the worker's diff, then prove a pre-existing
   `COMPLETE`/`PENDING` row becomes `DELIVERED` after a service restart
   without the work item being reprocessed.
5. ✅ **Liveness acceptance drill** (product side, PR #269) — passed. Mac
   drill ran twice: outage 1 detected at 312s, scheduler recovered, outage 2
   detected at 322s (still inside original cooldown). Both alarms produced
   durable events and successful ntfy receipts with matching iMessage records.
   No model was involved in detecting either outage. Scheduler and observer
   both healthy post-drill, heartbeat advancing, alarm marker cleared and
   re-armed.
6. ⬜ PR #18 (metrics) merged — blocked on #20 (CI flake fix) merging first.
7. ⬜ Lint cleanup on CEC (`ruff`/`ruff format`/`mypy` gates) — bounded
   follow-up Codex explicitly asked for after #14/#15 cleared. Not yet
   started. Tree had 22 ruff findings + 14 files needing format + a mypy
   module-path collision as of the last check; re-verify current state
   before starting, since #15's rebase may have changed the count.
8. ⬜ `soken_circuit` items **#3** (result-file validator — flags placeholder
   verdicts / false archive claims / missing `Status:` line) and **#4**
   (notification self-test / canary ping — largely subsumed by #269's
   liveness work, verify overlap before building) and **#5** (concurrency
   model — was resolved as a design question by locking Option B; verify
   whether `soken_circuit` itself needs any change or if this item is now
   fully answered by the CEC-side work).

**Then, and only then:** show Scott the consolidated evidence for all of the
above and get his explicit go-ahead before merging PR #10 (the 10-reviewer
fleet) and firing it.

---

## 8. Open design question, parked (not blocking)

**CEC's eventual repo home.** Current: stays in `soken-os/.github` while
hardening (correct per Scott's own reasoning — no migration mid-flight).
Scott was mid-`AskUserQuestion` on "own repo at cutover" vs "own repo now" vs
"stay in `.github` indefinitely" when the conversation moved on — he **did
not select an option** (dismissed to give other instructions instead). Do not
assume an answer; ask again if it becomes decision-relevant (e.g. at actual
cutover time), and note his `.github`-repo critique already on record (org
metadata repo is a strange home for production control-plane code) if you do.

---

## 9. Quick orientation commands for a fresh session

```bash
# CEC repo state
cd /home/user/.github && git log --oneline -15 origin/main

# soken repo state (add via add_repo + clone to /workspace/soken if not present)
cd /workspace/soken && git log --oneline -15 origin/main

# Open PRs needing attention
# (use mcp__github__list_pull_requests or pull_request_read per-PR)

# Canonical technical record
cat docs/executor-dispatch-decision.md   # read Appendices, esp. G and H

# This handoff doc
cat docs/SESSION-HANDOFF-2026-07-25.md
```
