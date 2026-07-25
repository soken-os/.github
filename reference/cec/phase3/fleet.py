"""CEC Review Fleet: 10 context-isolated adversarial reviewers.

Spec: docs/cec-review-fleet.md

Each charter defines one reviewer's lens, input bundle (what to read),
and objective (what to hunt for). Packets are standard CIRCUIT_BUILD items
seeded into the registry; the reviewer writes structured findings to a
file in its worktree, which the controller verifies as evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg

from ..phase0.bootstrap import database_url
from ..phase2.bootstrap import migrate as phase2_migrate
from .packet import FORBIDDEN_PATHS, REPO_ROOT, build_packet, current_ref

PROGRAM_CEC_REVIEW = "CEC-REVIEW"
FLEET_DIR = "reference/cec/phase3/runtime/fleet"

FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reviewer_id", "findings"],
    "additionalProperties": False,
    "properties": {
        "reviewer_id": {"type": "string", "pattern": "^R(0[1-9]|10)$"},
        "lens": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "severity", "title", "where", "scenario", "fix"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^R\\d{2}-[a-z]$"},
                    "severity": {"enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                    "title": {"type": "string", "minLength": 1},
                    "where": {"type": "string", "minLength": 1},
                    "scenario": {"type": "string", "minLength": 1},
                    "fix": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


def _findings_path(charter_id: str) -> str:
    return f"{FLEET_DIR}/{charter_id}-findings.json"


CHARTERS: list[dict[str, Any]] = [
    {
        "id": "R01",
        "lens": "State-machine & custody integrity",
        "group": "core",
        "input_bundle": [
            "docs/executor-dispatch-decision.md",
            "reference/cec/phase1/registry.py",
            "reference/cec/phase1/actions.py",
            "reference/cec/phase3/controller.py",
            "reference/cec/contracts.py",
            "reference/cec/queueing.py",
        ],
        "objective": (
            "Hunt for states a work item can reach that are inconsistent, "
            "orphaned, or permanently stuck. Check: continuation-invariant "
            "holes; CAS gaps; stages with no exit (the VERIFYING wedge was "
            "already found — find its siblings); transitions that strand "
            "custodian/lease/next_signal; the PARKED→? paths; states the DB "
            "CHECK constraints do not actually cover."
        ),
        "out_of_scope": "Sandbox internals, evidence-content forgery.",
    },
    {
        "id": "R02",
        "lens": "Information-passing & handoff fidelity",
        "group": "core",
        "input_bundle": [
            "docs/executor-dispatch-decision.md",
            "reference/cec/adapters.py",
            "reference/cec/phase1/registry.py",
            "reference/cec/phase2/notifications.py",
            "reference/cec/phase3/worktree.py",
            "reference/cec/phase3/packet.py",
            "reference/cec/contracts.py",
        ],
        "objective": (
            "At every handoff, find where a message can be dropped, malformed, "
            "mis-hashed, or silently lost. Boundaries to check: controller→worker "
            "(packet, command_id, lease), worker→claim→controller (typed output, "
            "hashes), claim→evidence, controller→notification→human, event "
            "append↔work-item CAS atomicity, packet→worktree. Hunt: at-least-once "
            "vs at-most-once gaps, dedup keys that collide or miss, the "
            "source_event_id scheme, ordering assumptions, a signal whose loss "
            "strands the item."
        ),
        "out_of_scope": "Crash-recovery timing (R04), lease races (R05).",
    },
    {
        "id": "R03",
        "lens": "Evidence & verification integrity",
        "group": "core",
        "input_bundle": [
            "docs/executor-dispatch-decision.md",
            "reference/cec/phase3/evidence.py",
            "reference/cec/phase3/worktree.py",
            "reference/cec/adapters.py",
            "reference/cec/phase3/packet.py",
        ],
        "objective": (
            "Attack the anti-false-DONE thesis. Find ways a worker can get a "
            "false COMPLETE. Hunt: ways to satisfy verify_code_change_claim "
            "without the real change; hash/files_changed/allow-list checks that "
            "can be fooled (symlink, rename, --no-index, path-normalization); "
            "the 'passing test output' string check; a claim that lies about "
            "diff_sha256; the ROUTINE overlay letting a worker alter what gets "
            "verified."
        ),
        "out_of_scope": "Whether the worker can escape the sandbox (R06).",
    },
    {
        "id": "R04",
        "lens": "Crash, recovery & durability",
        "group": "core",
        "input_bundle": [
            "docs/executor-dispatch-decision.md",
            "reference/cec/phase0/bootstrap.py",
            "reference/cec/phase3/controller.py",
            "reference/cec/phase3/service.py",
            "reference/cec/phase0/gate.py",
            "reference/cec/phase1/gate.py",
            "reference/cec/adapters.py",
        ],
        "objective": (
            "Kill at every boundary — does custody survive, exactly once? "
            "Hunt: crash points that orphan or double-dispatch; the "
            "reaper/exit-code gaps; a side-effect that runs twice on replay; "
            "the run_scan_once G2 catch masking a persistent crash-loop; "
            "receipt/gate state after a crash mid-migration."
        ),
        "out_of_scope": "Content correctness of evidence (R03).",
    },
    {
        "id": "R05",
        "lens": "Concurrency, leases & fencing",
        "group": "core",
        "input_bundle": [
            "docs/executor-dispatch-decision.md",
            "reference/cec/phase3/controller.py",
            "reference/cec/adapters.py",
            "reference/cec/phase3/singleton.py",
            "reference/cec/queueing.py",
            "reference/cec/phase1/registry.py",
        ],
        "objective": (
            "Find where two things can act on one item, or a stale actor wins. "
            "Hunt: lease-race and fencing-token gaps (lease_epoch), the "
            "live-but-silent worker, the reconcile-loop singleton gap, two "
            "controllers, PID-reuse, TOCTOU on the process/worktree, a "
            "redispatch that races a still-alive worker."
        ),
        "out_of_scope": "The OS sandbox (R06).",
    },
    {
        "id": "R06",
        "lens": "Sandbox & privilege boundary",
        "group": "security",
        "input_bundle": [
            "reference/cec/adapters.py",
            "reference/cec/phase3/sandbox/worker.sb",
            "reference/cec/phase3/sandbox/worker-bash.md",
            "reference/cec/phase3/tests/test_phase3_sandbox.py",
            "reference/cec/contracts.py",
        ],
        "objective": (
            "Find worker escape routes. Hunt: object-store denial holes, "
            "~/.claude / .claude.json overlay abuse, path canonicalization/"
            "symlink/TOCTOU, child-process escape, the fail-open path "
            "(sandbox_available() False → unconfined, no log), the unused "
            "PRIVATE_TMP_ROOT, non-Claude adapters (Codex --sandbox, "
            "Script = none), regex-rule bypass in worker.sb, a malicious "
            "packet widening its own grants."
        ),
        "out_of_scope": "Trust/insider model (R07).",
    },
    {
        "id": "R07",
        "lens": "Insider threat, trust & persistence",
        "group": "security",
        "input_bundle": [
            "docs/executor-dispatch-decision.md",
            "reference/cec/phase0/gate.py",
            "reference/cec/phase1/gate.py",
            "reference/cec/phase3/packet.py",
            "reference/cec/phase3/requeue.py",
            "reference/cec/phase3/seed_bootstrap.py",
            "reference/cec/phase1/registry.py",
            "reference/cec/adapters.py",
        ],
        "objective": (
            "Find trust boundary abuses. Hunt: a malicious packet author using "
            "packet objective/paths/authority as an attack surface; a compromised "
            "worker planting persistence (hooks, MCP config, the ROUTINE "
            ".claude.json overlay, git config); gate/receipt forgery "
            "(proof-receipt hash bypass, stale-receipt reuse); event-ledger "
            "tampering vs the append-only trigger; secret handling (can a key "
            "reach durable state or logs); resource-exhaustion DoS — can volume "
            "(hundreds of packets/events/retries) exhaust the queue, blow the "
            "recovery ceiling, wedge leases, or fill the ledger. The "
            "untrusted_external_data boundary (GitHub comments, CI logs) "
            "reaching a decision."
        ),
        "out_of_scope": "Pure OS-sandbox mechanics (R06).",
    },
    {
        "id": "R08",
        "lens": "Prompt-injection & model boundary",
        "group": "extra",
        "input_bundle": [
            "reference/cec/phase3/packet.py",
            "reference/cec/adapters.py",
            "reference/cec/phase3/controller.py",
            "reference/cec/phase3/evidence.py",
            "reference/cec/contracts.py",
        ],
        "objective": (
            "The worker is an LLM. Find where a poisoned packet objective, "
            "malicious content in a file the worker reads, or a crafted "
            "observation can subvert the worker (exfiltrate, escalate, sabotage "
            "the diff) or make the controller mis-transition. Map every path "
            "where untrusted text reaches a model or a decision."
        ),
        "out_of_scope": "Non-LLM concurrency (R05).",
    },
    {
        "id": "R09",
        "lens": "Silent-failure & observability",
        "group": "extra",
        "input_bundle": [
            "reference/cec/phase3/controller.py",
            "reference/cec/phase3/service.py",
            "reference/cec/phase2/notifications.py",
            "reference/cec/phase3/sentinel.py",
            "reference/cec/phase3/metrics.py",
        ],
        "objective": (
            "Find every path where the system fails without a trace — no event, "
            "no notification, no log. Hunt: delivery-gap siblings, the G2 catch "
            "swallowing errors as bare ERROR:Type, unlogged NONE/no-op paths "
            "that hide a stuck item, metrics/scorecard blind spots, a reclaim "
            "that never notifies."
        ),
        "out_of_scope": "Correctness of a transition that did fire (R01).",
    },
    {
        "id": "R10",
        "lens": "Economic runaway & cost",
        "group": "extra",
        "input_bundle": [
            "reference/cec/phase3/controller.py",
            "reference/cec/phase3/requeue.py",
            "reference/cec/phase3/packet.py",
            "reference/cec/queueing.py",
            "docs/executor-dispatch-decision.md",
        ],
        "objective": (
            "Find unbounded cost paths. Hunt: infinite dispatch/retry loops, "
            "the runtime-budget and recovery-ceiling actually bounding spend, a "
            "requeue loop, redispatch storms, a worker relaunched forever, "
            "lease-renewal that never terminates a hung worker."
        ),
        "out_of_scope": "Security intent (R06/R07) — this covers accidental runaway too.",
    },
]


def _review_objective(charter: dict[str, Any]) -> str:
    findings_path = _findings_path(charter["id"])
    input_files = "\n".join(f"  - {p}" for p in charter["input_bundle"])
    return (
        f"You are reviewer {charter['id']} — {charter['lens']}.\n\n"
        f"Stance: adversarial. Assume the build is guilty. Default to "
        f"reporting a suspicion over staying silent.\n\n"
        f"{charter['objective']}\n\n"
        f"Input files (read these):\n{input_files}\n\n"
        f"Out of scope: {charter['out_of_scope']}\n\n"
        f"Write your findings as JSON to {findings_path} using this schema:\n"
        f"{json.dumps(FINDING_SCHEMA, indent=2)}\n\n"
        f"Every finding MUST have a concrete failure scenario — specific "
        f"inputs/state leading to a wrong outcome. 'This feels risky' is "
        f"filtered; 'packet X with paths Y makes the verifier accept Z' is "
        f"kept. Use finding IDs like {charter['id']}-a, {charter['id']}-b, etc.\n\n"
        f"The test gate for this review: validate your findings JSON against "
        f"the schema above using jsonschema and write 'validation passed' to "
        f"the artifact path."
    )


def fleet_packets(
    repo_root: Path = REPO_ROOT,
    starting_ref: str | None = None,
) -> list[dict[str, Any]]:
    ref = starting_ref or current_ref(repo_root)
    runtime = repo_root / "reference" / "cec" / "phase3" / "runtime" / "fleet"
    packets = []
    for charter in CHARTERS:
        cid = charter["id"]
        packets.append(
            build_packet(
                program=PROGRAM_CEC_REVIEW,
                objective=_review_objective(charter),
                allowed_paths=[_findings_path(cid)],
                artifact_path=runtime / f"{cid}-validation.txt",
                diff_artifact_path=runtime / f"{cid}-change.diff",
                repo_root=repo_root,
                starting_ref=ref,
                forbidden_paths=FORBIDDEN_PATHS,
                new_files_allowed=True,
                acceptance={
                    "review": (
                        f"Reviewer {cid} produced structured findings with "
                        f"concrete failure scenarios in {_findings_path(cid)}"
                    ),
                    "evidence": [
                        f"file:{_findings_path(cid)} (findings JSON)",
                        "file:artifact_path (schema validation output)",
                        "file:diff_artifact_path (diff adding findings file)",
                    ],
                },
            )
        )
    return packets


def seed_fleet(starting_ref: str | None = None) -> list[str]:
    phase2_migrate()
    packets = fleet_packets(starting_ref=starting_ref)
    now = datetime.now(UTC)
    ids = []
    with psycopg.connect(database_url(), autocommit=True) as conn:
        for charter, packet in zip(CHARTERS, packets, strict=True):
            work_item_id = f"fleet-review-{charter['id'].lower()}"
            existing = conn.execute(
                "SELECT id FROM cec.work_items WHERE id=%s", (work_item_id,)
            ).fetchone()
            if existing:
                ids.append(work_item_id)
                continue
            conn.execute(
                """INSERT INTO cec.work_items
                (id,program,title,task_class,priority_class,estimated_duration_seconds,
                 stage,wait_reason,custodian_type,custodian_id,lease_token,lease_epoch,
                 lease_expires_at,next_signal_type,next_signal_key,next_signal_deadline,
                 recovery_action,work_packet,authority_class)
                VALUES (%s,%s,%s,'CIRCUIT_BUILD',60,600,
                        'READY','NONE','CONTROLLER','fleet-controller',%s,0,%s,
                        'DISPATCH_READY',%s,%s,%s::jsonb,%s::jsonb,
                        'ROUTINE')""",
                (
                    work_item_id,
                    PROGRAM_CEC_REVIEW,
                    f"Fleet review {charter['id']}: {charter['lens']}",
                    uuid4(),
                    now + timedelta(minutes=20),
                    f"fleet-seed-{charter['id'].lower()}",
                    now + timedelta(minutes=2),
                    json.dumps({"action": "LAUNCH_FLEET_REVIEW"}),
                    json.dumps(packet),
                ),
            )
            ids.append(work_item_id)
    return ids
