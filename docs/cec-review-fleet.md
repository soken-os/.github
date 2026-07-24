# CEC Review Fleet — whole-system adversarial audit

**Status:** Codified 2026-07-24; **executes when the lane converges** (a fresh worker runs a real task end-to-end without crash/silence/wedge). Running it mid-fix would audit a moving target.
**Purpose:** After the build is proven, turn a fleet of *independent, context-isolated* reviewers on the entire CEC to catch everything a single pass would miss — stalls, loopholes, backdoors, false-completion paths, and security holes.

---

## 1. Isolation model (the non-negotiable part)

Each reviewer is **blind to every other reviewer.** This is the design, not a nicety:

- **Context-isolated:** each agent receives only (a) its own charter and (b) a curated input bundle of the specific docs/code for its lens. It does **not** receive the other charters, the other agents' findings, the fact that other agents exist, or any prior review output. No shared memory, no cross-talk.
- **Read-only:** reviewers audit; they do not mutate. (Any reviewer that needs to *run* code to prove a finding gets its own throwaway git worktree so it cannot perturb the others.)
- **Independent discovery is the metric:** a finding counts on first *independent* surfacing. The three-way P4 review is the precedent — the sharpest finding (the `~/.claude` hooks write-to-execute escape) appeared in exactly one of three otherwise-agreeing passes. **We merge the union of findings; we never pick a "winner" pass.**
- **Adversarial stance:** every reviewer is instructed to *try to break it*, assume the build is guilty, and default to reporting a suspicion over staying silent. False positives are cheaper than a missed backdoor; the collation step filters them.

## 2. The roster

Ten charters, grouped. Run the **core 5** (whole-system) at minimum; add **security 2** and the **extra 3** for a full audit. Each charter lists its lens, sharpest questions, input bundle, and — critically — what is **out of scope for it** (so lenses don't overlap and every reviewer digs deep in one place instead of shallow everywhere).

### Group A — Whole-system integrity (the core 5)

**R1 — State-machine & custody integrity.**
*Can a work item reach an inconsistent, orphaned, or permanently stuck state?* Hunt: continuation-invariant holes; CAS gaps; stages with no exit (the `VERIFYING` wedge we already found — find its siblings); transitions that can strand `custodian`/`lease`/`next_signal`; the `PARKED`→? paths; states the DB `CHECK`s don't actually cover.
*Bundle:* decision doc (schema §4, invariant), `phase1/registry.py`, `phase3/controller.py`, `phase1/actions.py`, migrations.
*Out of scope:* sandbox internals, evidence-content forgery.

**R2 — Information-passing & handoff fidelity** *(Scott's core worry: "build stalls because information wasn't passed correctly").*
*At every handoff, can a message be dropped, malformed, mis-hashed, or silently lost?* Boundaries: controller→worker (packet, command_id, lease), worker→claim→controller (typed output, hashes), claim→evidence, controller→notification→human, event append↔work-item CAS atomicity, packet→worktree. Hunt: at-least-once vs at-most-once gaps, dedup keys that collide or miss, the `source_event_id` scheme, ordering assumptions, a signal whose loss strands the item.
*Bundle:* decision doc §5–7, adapters (`launch`/`observe`/`collect_result`), `registry.transition`, `notifications.py`, `worktree.py`, packet objectives.
*Out of scope:* crash-recovery timing (R4), lease races (R5).

**R3 — Evidence & verification integrity** *(attack the anti-false-DONE thesis).*
*Can a worker get a false `COMPLETE`? Can forged or partial evidence pass?* Hunt: ways to satisfy `verify_code_change_claim` without the real change; hash/`files_changed`/allow-list checks that can be fooled (symlink, rename, `--no-index`, path-normalization, the new-files/D2 gap); the "passing test output" string check; a claim that lies about `diff_sha256`; the ROUTINE overlay letting a worker alter what gets verified.
*Bundle:* `phase3/evidence.py`, `worktree.write_unified_diff`, adapters `collect_result`, decision doc §2 completion rule, the D1/D2 findings.
*Out of scope:* whether the worker *can* escape the sandbox (R6).

**R4 — Crash, recovery & durability** *(the fire-drill lens).*
*Kill at every boundary — does custody survive, exactly once?* Hunt: crash points that orphan or double-dispatch; the reaper/exit-code gaps; DBOS-recovery assumptions that don't hold; a side-effect that runs twice on replay; the `run_scan_once` G2 catch masking a *persistent* crash-loop; receipt/gate state after a crash mid-migration.
*Bundle:* decision doc §7 + fire drills §18, `phase0` harness, DBOS workflow/steps, `gate.py` files, `run_scan_once`, adapters lifecycle.
*Out of scope:* content correctness of evidence (R3).

**R5 — Concurrency, leases & fencing.**
*Can two things act on one item? Can a stale actor win?* Hunt: lease-race and fencing-token gaps (`lease_epoch`), the live-but-silent worker (G1/G3 territory — find the next), the reconcile-loop singleton gap (A2), two controllers, PID-reuse (B3), TOCTOU on the process/worktree, a redispatch that races a still-alive worker.
*Bundle:* decision doc §2B custody + Q4/A1, `controller.py` reconcile/lease logic, adapters process-identity (`_process_identity_matches`, `terminate`), `queueing.py`.
*Out of scope:* the OS sandbox (R6).

### Group B — Security (2)

**R6 — Sandbox & privilege boundary (external attacker / worker escape).**
*Can a worker escape confinement or write where it shouldn't?* Hunt: object-store denial holes, `~/.claude` / `.claude.json` overlay abuse, path canonicalization/symlink/TOCTOU, child-process escape, the fail-open path (`sandbox_available()` False → unconfined, no log), the unused `PRIVATE_TMP_ROOT`, non-Claude adapters (Codex `--sandbox`, Script = none — E4-c), regex-rule bypass in `worker.sb`, a malicious packet widening its own grants.
*Bundle:* `adapters.py` (sandbox_wrap, overlay, argv, `_env`), `worker.sb`, `worker-bash.md`, E4-a/b/c + G3/G6 records, sandbox tests.
*Out of scope:* trust/insider model (R7).

**R7 — Insider threat, trust boundaries, persistence & DoS** *(Scott's "employee on the inside," backdoors, "hundreds of requests to break through").*
*Who/what is trusted, and can that trust be abused?* Hunt: a malicious **packet author** (packet objective/paths/authority as an attack surface); a compromised worker planting **persistence** (hooks, MCP config, the ROUTINE `.claude.json` overlay, git config); **gate/receipt forgery** (proof-receipt hash bypass, stale-receipt reuse); **event-ledger tampering** vs the append-only trigger; **secret handling** (A4 — can a key reach durable state or logs?); **resource-exhaustion / persistence-attack DoS** — can volume (hundreds of packets/events/retries) exhaust the queue, blow the recovery ceiling, wedge leases, or fill the ledger to break the loop? The `untrusted_external_data` boundary (GitHub comments, CI logs) reaching a decision.
*Bundle:* decision doc (gates, authority_class, A4, invariant), `gate.py` files, `requeue.py`, `seed_*.py`, `packet.py` (validation), event/notification migrations, controller decision logic.
*Out of scope:* pure OS-sandbox mechanics (R6).

### Group C — Extra lenses (3, Claude-added)

**R8 — Prompt-injection & model boundary.**
*The worker is an LLM.* Can a poisoned packet objective, malicious content in a file the worker reads, or a crafted observation subvert the worker (exfiltrate, escalate, sabotage the diff) or make the controller mis-transition? Where does untrusted text reach a model or a decision?
*Bundle:* packet objectives, adapters (how prompts are composed), decision-table `decide()`, evidence text checks, any external-data ingestion.
*Out of scope:* non-LLM concurrency (R5).

**R9 — Silent-failure & observability.**
*The whole thesis is "nothing gets lost."* Find every path where the system fails **without a trace** — no event, no notification, no log. Hunt: the F1 delivery gap's siblings, the G2 catch swallowing errors as bare `ERROR:Type`, unlogged `NONE`/no-op paths that hide a stuck item, metrics/scorecard blind spots, a reclaim that never notifies.
*Bundle:* `run_scan_once`, `notifications.py`, controller no-op/return paths, decision doc cockpit/scorecard §, sentinel query.
*Out of scope:* the correctness of a transition that *did* fire (R1).

**R10 — Economic runaway & cost.**
*"Attention is disposable" means workers cost tokens/money.* Can a bug or attacker cause **unbounded cost**? Hunt: infinite dispatch/retry loops, the runtime-budget and recovery-ceiling actually bounding spend, a requeue loop, redispatch storms, a worker relaunched forever, lease-renewal that never terminates a hung worker.
*Bundle:* controller dispatch/reclaim/requeue, budget/ceiling logic, `requeue.py`, decision doc Q4 timing.
*Out of scope:* security intent (R6/R7) — this is about accidental runaway too.

## 3. Finding format

Every reviewer returns findings in the project's convention so they collate:

```
<ID>  (reviewer-assigned, e.g. R3-a)
severity:  CRITICAL | HIGH | MEDIUM | LOW
title:     one line
where:     file:line (or doc §)
scenario:  concrete inputs/state → wrong outcome (a repro, not a worry)
fix:       the smallest change that closes it
```

No finding without a **concrete failure scenario** — "this feels risky" is filtered; "packet X with paths Y makes the verifier accept Z" is kept.

## 4. Collation & adjudication

1. **Fan-out (parallel, isolated):** all selected reviewers run at once, each blind, each returning structured findings.
2. **Dedup (Claude, mechanical):** merge by (file, scenario); keep the union; note which lens found each (independence signal).
3. **Adversarial verify (Codex):** each surviving finding gets an independent "try to refute this" pass — real, or false positive? Majority-refute kills it (the pattern from every round of this build).
4. **Rank & synthesize (Claude):** severity-ordered list, each with the smallest fix, recorded in the decision doc appendix under the finding-ID convention (R1-a…). CRITICAL/HIGH become packets; the lane fixes them.
5. **The loop eats its own findings:** where a fix is a bounded code change, it becomes a self-dispatched CEC packet — the system repairs what its own audit found.

## 5. Execution mechanism

A single fan-out workflow: **Phase 1** spawns the selected reviewers in parallel (context-isolated by construction — each is a fresh agent with only its charter+bundle); **Phase 2** dedups; **Phase 3** runs the adversarial-verify pass per finding; **Phase 4** synthesizes the ranked report. Core-5 is the minimum viable audit (~5 agents); full is 10 + verify fan-out. Scale the verify pass to the finding count.

**Trigger:** when the P3-class convergence run reaches `COMPLETE` (or cleanly PARKs) and the lane is declared healthy — then, and not before.
