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

**Awaiting Scott's approval of this order; then the P4 packet is authored, seeded (Codex-at-terminal), and built by the CEC lane with content review before publication.**
