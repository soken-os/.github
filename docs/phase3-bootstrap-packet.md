# Phase 3 Bootstrap Packet — Design Draft

**Status:** Draft for three-way review (Scott / Claude / Codex), 2026-07-23.
**Purpose:** Design the first work packet that **CEC dispatches for itself** — satisfying the locked bootstrap criterion ("Phase 3 is not accepted unless CEC itself dispatched at least one of its own build tasks end-to-end"). This document is the packet's contract; Codex adjudicates, Scott approves, then the Mac seeds it and the machine takes it from there.

---

## 1. The first self-hosted task: the D1 fix (not all of Phase 3)

The bootstrap criterion requires *at least one* build task dispatched end-to-end. It does not require the first one to be big — and it shouldn't be. Proposed first packet: **implement finding D1** (Appendix F review note):

> `collect_result` stamps the claim's `lease_token`/`lease_epoch` from the *current* command built off the live row, not from the sidecar record of the run that actually produced the output. Fix: the claim must carry the epoch recorded at launch (from `<command_id>.process.json`) so a stale worker's late output is fenced by content, not by circumstance.

Why D1 is the right graduation exam:

- **Small and bounded** — one function path in `adapters.py` plus tests; fits comfortably in a single bounded worker turn with a 15-minute lease.
- **Real and unscripted** — an actual correctness fix the harnesses did not script, in the circuit's own code. Passing it proves the machine can do genuine work, not just replay fixtures.
- **Mechanically verifiable** — "tests pass, including two new tests proving the claim carries the launch-time epoch" is evidence a verifier can check without judgment.
- **Self-referential in the right way** — the circuit's first self-built change makes the circuit itself safer. If the machine fumbles, the fumble is the best bug report available, on a change trivially redone by hand.
- **Low blast radius** — no schema change, no gate change, no migration, no external system.

The rest of Phase 3 (pull queue, dependencies, resource locks, deterministic routing) then becomes the *second and subsequent* packets — by then dispatch-by-machine is proven, and those larger packets can flow through the same lane, decomposed task by task.

## 2. The packet (draft v1)

```json
{
  "task_class": "CIRCUIT_BUILD",
  "objective": "Fix finding D1 in reference/cec/adapters.py: collect_result must stamp WorkerResultClaim.lease_token and lease_epoch from the launch-time sidecar record (<command_id>.process.json), not from the current WorkerCommand. Read docs/executor-dispatch-decision.md Appendix F section 'D1' for the finding. Add two tests in reference/cec/phase2/tests/test_adapter_identity.py: (1) a claim carries the epoch recorded at launch even when the passed command has a newer epoch; (2) a claim from a sidecar with a mismatched command_id yields no claim. Run the full non-Postgres test suite and write its complete output to the artifact path. Do not modify any file outside allowed_paths.",
  "starting_ref": "<SHA of main at seed time>",
  "allowed_paths": [
    "reference/cec/adapters.py",
    "reference/cec/phase2/tests/test_adapter_identity.py"
  ],
  "forbidden_paths": [
    "docs/",
    "reference/cec/migrations/",
    "reference/cec/phase0/gate.py",
    "reference/cec/phase1/gate.py",
    "reference/cec/phase1/registry.py",
    "reference/cec/contracts.py"
  ],
  "artifact_path": "reference/cec/phase3/runtime/bootstrap-test-output.txt",
  "diff_artifact_path": "reference/cec/phase3/runtime/bootstrap-change.diff",
  "estimated_duration_seconds": 600,
  "priority_class": 60,
  "authority_class": "ROUTINE",
  "acceptance": {
    "tests": "all non-Postgres suites pass, including the two new D1 tests",
    "diff": "touches only allowed_paths",
    "evidence": ["file:artifact_path (passing test output)", "file:diff_artifact_path (the change as a unified diff)"]
  }
}
```

(Schema-validated like the Phase 2 packet; `starting_ref` pinned at seed time so the worker's base is deterministic.)

## 3. What must be built *around* the packet before it can run

Three deliberate, small extensions to the Phase 2 slice — this is the human-built scaffolding that makes the first self-dispatch possible, and it is the **last** human-ferried build work:

**3a. Editing workers run in a git worktree, not the live clone.** The Phase 2 worker was read-only. An editing worker gets a fresh `git worktree` at `starting_ref`; `allowed_paths`/`forbidden_paths` are enforced by the verifier against the produced diff (and optionally pre-flight by the adapter's `--allowedTools` scoping). The live clone and the controller's own code are never the worker's canvas. This is also the natural home of the `repo:cec` resource lock.

**3b. Evidence verifier learns "code change" evidence.** Phase 2's verifier proves one passing-test artifact. The D1 packet needs two artifacts: the passing test output **and** the unified diff. Verification: recompute both SHA-256s; parse the diff and reject any hunk outside `allowed_paths` (this is the mechanical enforcement of the path sandbox); require the two new test names to appear as passed in the output.

**3c. The controller runs as a service, not a harness.** The Phase 2 runner exits after one task. The bootstrap needs the small always-on loop from the locked design (§6 of the decision doc): scan nonterminal items every ~15s, reconcile each through the partitioned queue. Started manually on the Mac once (`caffeinate -i python -m reference.cec.phase3.service` or a `launchd` plist); from then on, seeding a packet is the only human act.

**D1's fix itself is deliberately NOT in this scaffolding** — that's the point. The scaffolding is built by Codex/Claude by hand; the D1 fix is built by the machine.

## 4. End-to-end flow (who does what)

| Step | Actor | Act |
|---|---|---|
| 1 | Codex (hand-built, last time) | Build 3a–3c + their tests; Claude reviews |
| 2 | Scott, at the Mac (~2 min) | `git pull`; start the controller service; run the seed script (`python -m reference.cec.phase3.seed_bootstrap`) |
| 3 | **CEC** | Claims the READY item, leases it, launches `claude -p` in the worktree with the packet |
| 4 | **CEC** | Observes the worker (pid + start-time), collects the typed claim, transitions `RESULT_CLAIMED` |
| 5 | **CEC** | Verifies evidence mechanically (tests green incl. the two D1 tests; diff confined to allowed_paths) → `COMPLETE` → notification to the bridge outbox |
| 6 | Scott, from the phone | Reads the notification; the diff artifact is the review payload |
| 7 | Claude/Codex | Independent review of the machine-produced diff; a human (or Claude, from here) commits it to a PR — commit/PR automation is Phase 3 proper, not bootstrap |

Acceptance line (same convention as prior phases):
`bootstrap=SELF_DISPATCHED; worker=CLAUDE_CODE; evidence=VERIFIED; diff_confined=true; final=COMPLETE`

Plus the locked criterion: at least one controller kill mid-run during the bootstrap execution is **encouraged but not required** — the kill-survival property is already proven; the new thing under test is unscripted real work.

## 5. Open questions for Codex's adjudication

1. **Worktree lifecycle:** who creates/destroys it — the adapter at `launch()` (cleaner custody) or the controller before dispatch (simpler adapter)? Proposal: controller creates, records the path in the event payload, destroys only after `COMPLETE` + notification ACK.
2. **Path enforcement layers:** verifier-checks-diff is the mechanical guarantee; should the adapter *also* pass `--add-dir` / restrict `--allowedTools` so the worker is prevented, not just caught? Proposal: yes, both — prevention is cheap, but the verifier remains the authority.
3. **Result schema for editing tasks:** extend Phase 2's `RESULT_SCHEMA` with a required `files_changed: [string]` field so the claim itself names its touches (cross-checked against the diff)?
4. **Lease/deadline numbers for a 10-minute model task:** proposal 20-minute lease, 5-minute signal deadline with controller renewal on observed liveness — confirm or amend.
5. **Does the bootstrap packet run with the service loop's generic `decide()` or the Phase-2-style one-task controller extended?** Proposal: extend the one-task controller minimally (it already handles the full stage machine) and defer the generic multi-item service to Phase 3 proper — smallest diff that satisfies the criterion.

## 6. What stays out of scope for the bootstrap

No GitHub mutation by the machine (commit/PR of the produced diff is done by a human or by Claude in-session this one time). No Railway. No multi-task queueing. No intake/planner — the packet is hand-authored by design (Phase 3.5 owns planner-authored packets). No change to locked schema or gates.

---

*Once Codex adjudicates §5 and Scott approves, step 1 is Codex's final hand-ferried round, and everything after step 2 is the machine's.*
