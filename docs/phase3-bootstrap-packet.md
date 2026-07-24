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

---

## 7. Adjudicated resolutions (Codex memo, PR #2 comment; Claude reconciliation — 2026-07-23)

Codex's verdict: **approve with amendments**. Claude concurs on all points, including one concession. The contract below supersedes §2–§5 where they differ.

**Q1 — Worktree lifecycle: controller-owned, retention until ACK.** Controller creates the worktree pre-launch; records absolute path, base SHA, branch, and cleanup policy in durable event payload; destroys only after `COMPLETE` **and** notification acknowledgement (or a review-retention TTL) — never immediately on `COMPLETE`, because the diff/worktree is the review payload and deleting it early is evidence loss. The adapter receives a ready directory and owns no repo lifecycle.

**Q2 — Path enforcement: layered; verifier is the binding authority.** Adapter restricts the worker's surface where the CLI supports it; the verifier is authoritative over the *parsed Git diff* — never string-prefix checks on model output. The verifier must reject: symlink/traversal escapes; renames whose old **or** new path leaves the allow-list; binary diffs outside the allow-list; submodule changes; and untracked/new files unless the packet explicitly allows new files in a named path. Positive allow-list is the rule; `forbidden_paths` is secondary.

**Q3 — Result schema: extend, but never trust.** `files_changed: [string]` required for editing tasks, cross-checked against the parsed diff (mismatch = fail). Claim also carries `diff_sha256` and `test_output_sha256`; the verifier recomputes both. *The claim is an index into evidence, not evidence itself.*

**Q4 — Timing (locked numbers for this packet):** `estimated_duration_seconds` 600; initial lease now+20m; initial `next_signal_deadline` **now+2m** (Codex tightened Claude's 5m — correct, since vanished-attention is the failure being killed); observation cadence ~15s with the signal deadline re-extended to now+2m only while pid+start-time identity matches; recovery ceiling 3, then `ESCALATE_RECOVERY`. Unavailable observer ⇒ hold/escalate per locked A1/B1; never redispatch from uncertainty.

**Q5 — Controller shape: Claude's proposal rejected; Codex sustained.** Do **not** extend the Phase-2 one-task harness (a second one-off loop = migration debt where CEC historically failed). Build the **smallest generic service spine**: scan nonterminal/due items → enqueue per `work_item_id` through the existing partitioned queue → reconcile → execute only the actions D1 needs. No planner, dependency graph, resource-lock graph, multi-task routing, or GitHub/Railway mutation — generic spine, one-task capability. Claude concedes; this honors the locked build order better than the harness extension.

**Packet JSON amendments (accepted verbatim):** add `files_changed` to the result schema with exact-match verification; add recomputed `artifact_sha256` fields for both artifacts; add `new_files_allowed: false` (the D1 test file already exists); keep the two-path positive allow-list exactly as drafted; verifier must prove the diff is against the pinned `starting_ref`; both runtime artifact paths are git-ignored runtime-only.

**Standing scope boundary (reaffirmed):** the machine produces the D1 diff and evidence; it does **not** commit, push, open PRs, merge, or touch GitHub/Railway in this round. PR publication stays with Scott/Claude/Codex.

### Build authorization — **APPROVED by Scott, 2026-07-23** (relayed via Claude session; recorded here as the authoritative record)

Codex's final hand-ferried round builds exactly: worktree custody (create/retain/cleanup), code-change evidence verification (dual artifact + diff-vs-allow-list), editing-task result schema (`files_changed` + recomputed hashes), and the minimal generic service spine scoped to one seeded packet — then the D1 packet is seeded and **CEC dispatches its first build task itself**.

---

## 8. Scaffold review (Claude, 2026-07-23) — RATIFIED with one fix applied

Reviewed Codex's scaffold (`c5cde6f`) against §7. Verified in code: controller-owned worktree custody recorded in durable event payload (detached at pinned `starting_ref`, idempotent creation, retention-by-default — nothing deletes a worktree automatically; `remove_worktree` is invoked only by tests, so cleanup is manual-after-ACK for the bootstrap, the safe direction); layered enforcement with the verifier as authority over the *parsed Git diff* (`--name-status -z`, rename/copy checked on both old and new path, untracked files via `ls-files --others`, artifacts excluded, `new_files_allowed` enforced, `files_changed` exact-match, both hashes recomputed, diff proven against the pinned ref, empty diff rejected); locked Q4 numbers implemented exactly (20m lease, 2m signal deadline, heartbeat re-extends only on adapter-observed liveness which itself requires pid + start-time identity per B3); the spine is genuinely generic (scan nonterminal → reconcile per item) with no planner/deps/locks/routing and no GitHub/Railway mutation anywhere. A strong touch beyond the contract: the controller **regenerates the unified diff from the worktree itself** rather than trusting the worker's file. Suites re-run in review sandbox: 24/24 including worktree tests.

**E2 — fixed in-place (false-rejection risk on the maiden run).** The packet objective said only "write the unified diff to the diff artifact," but the controller overwrites that artifact with its canonical `git diff --no-ext-diff --src-prefix=a/ --dst-prefix=b/ <ref>` regeneration, and the verifier requires the worker's claimed `diff_sha256` to match the regenerated bytes — so any flag difference in the worker's diff would reject honest work. Fix: the objective now specifies the exact command and warns that the controller regenerates it. Tests re-run green.

**E1 — follow-up packet (not blocking):** the verifier does not yet explicitly reject a type-change that turns an allowed file into a symlink (`--name-status` doesn't expose file type). Confined by the allow-list + human diff review this round; a natural early self-dispatched packet is "harden the verifier: reject symlink/submodule type-changes via `git diff --raw` mode bits."

**E3 — note for multi-item (not blocking):** the service loop reconciles sequentially rather than through the DBOS partitioned queue named in Q5. Functionally identical for one item; wire the queue when the registry holds more than one nonterminal item.

**The scaffold is ratified. The machine is cleared for its first self-dispatched task.**

### Live-run amendments (Codex at the Mac terminal, ratified by Claude — 2026-07-23)

Three empirical fixes pushed during the maiden flight (`3e53220`, `182eab6`, `a3de5a8`), reviewed and ratified:

- **Observation cwd fix (real bug):** `observe()` resolves sidecars from cwd, but the controller sat at repo root while sidecars live in the worktree — a live worker would have read as `MISSING`. Fixed with a working-directory context around observation. (This was the adapter README's known production edge, now closed.)
- **Service targeting:** `due_items` filters to `task_class='CIRCUIT_BUILD'` with a `starting_ref`, so the bootstrap service ignores unrelated registry rows.
- **E4 — worker permissions widened for headless test-running (accepted with a recorded caveat):** the bootstrap worker runs with `permission_mode: bypassPermissions` and `allowed_tools: [Read, Edit, Write, Bash]` because headless `-p` mode cannot interactively approve the Bash pytest run. Unconfined Bash means worker actions outside the worktree would not appear in the verified diff. Accepted for this ROUTINE, human-reviewed, publication-gated bootstrap; **hardening packet queued:** scope Bash to worktree-rooted commands (allowed-tool patterns or macOS sandbox-exec) before any packet above ROUTINE authority runs.
