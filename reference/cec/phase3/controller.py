"""Minimal generic service spine for the Phase-3 bootstrap packet."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from ..adapters import ClaudeCodeAdapter, ScriptAdapter
from ..contracts import (
    WorkerCommand,
    WorkerHandle,
    WorkerKind,
    WorkerProcessState,
    WorkerResultClaim,
)
from ..phase0.bootstrap import database_url
from ..phase1.registry import Registry, TransitionPatch
from ..phase2.notifications import deliver_pending
from .evidence import verify_code_change_claim
from .packet import BOOTSTRAP_RESULT_SCHEMA, REPO_ROOT
from .worktree import WorktreeRecord, create_worktree, write_unified_diff


CONTROLLER_ID = "phase3-bootstrap-controller"


def _now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _read_structured_output(command: WorkerCommand) -> Mapping[str, Any]:
    sidecar = command.working_directory / ".cec"
    candidates = [
        sidecar / f"{command.command_id}.last.json",
        sidecar / f"{command.command_id}.stdout",
    ]
    for path in candidates:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("structured_output"), dict):
            return doc["structured_output"]
        if isinstance(doc, dict):
            return doc
    return {}


def _claim_dict(claim: WorkerResultClaim, command: WorkerCommand) -> dict[str, Any]:
    structured = _read_structured_output(command)
    payload = {
        "command_id": claim.command_id,
        "work_item_id": claim.work_item_id,
        "worker_instance_id": claim.worker_instance_id,
        "lease_token": str(claim.lease_token),
        "lease_epoch": claim.lease_epoch,
        "status": claim.status.value,
        "summary": claim.summary,
        "evidence": [dict(item) for item in claim.evidence],
        "proposed_followups": [dict(item) for item in claim.proposed_followups],
    }
    for key in ("files_changed", "test_output_sha256", "diff_sha256"):
        if key in structured:
            payload[key] = structured[key]
    return payload


class BootstrapController:
    def __init__(
        self,
        *,
        repo_root: Path = REPO_ROOT,
        worktree_root: Path | None = None,
        bridge_outbox: Path | None = None,
        worker_kind: WorkerKind = WorkerKind.CLAUDE_CODE,
    ) -> None:
        self.repo_root = repo_root.resolve()
        os.chdir(self.repo_root)
        self.worktree_root = (
            worktree_root or self.repo_root / "reference" / "cec" / "phase3" / "worktrees"
        ).resolve()
        self.bridge_outbox = (
            bridge_outbox
            or self.repo_root / "reference" / "cec" / "phase3" / "runtime" / "bridge" / "outbox"
        ).resolve()
        self.worker_kind = worker_kind
        self.registry = Registry(database_url())
        self.adapter = (
            ClaudeCodeAdapter()
            if worker_kind is WorkerKind.CLAUDE_CODE
            else ScriptAdapter()
        )

    def due_items(self) -> list[str]:
        with psycopg.connect(database_url()) as conn:
            rows = conn.execute(
                """SELECT id FROM cec.work_items
                WHERE stage NOT IN ('COMPLETE','CANCELLED')
                AND task_class='CIRCUIT_BUILD'
                AND work_packet ? 'starting_ref'
                ORDER BY priority_class DESC, created_at ASC"""
            ).fetchall()
        return [str(row[0]) for row in rows]

    def load_item(self, work_item_id: str) -> dict[str, Any]:
        with psycopg.connect(database_url(), row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM cec.work_items WHERE id=%s", (work_item_id,)
            ).fetchone()
        if row is None:
            raise KeyError(work_item_id)
        return dict(row)

    @staticmethod
    def _patch(item: Mapping[str, Any], **changes: Any) -> TransitionPatch:
        values = {
            "stage": item["stage"],
            "wait_reason": item["wait_reason"],
            "custodian_type": item["custodian_type"],
            "custodian_id": item["custodian_id"],
            "lease_token": str(item["lease_token"]),
            "lease_epoch_delta": 0,
            "lease_expires_at": item["lease_expires_at"],
            "next_signal_type": item["next_signal_type"],
            "next_signal_key": item.get("next_signal_key") or "phase3",
            "next_signal_deadline": item["next_signal_deadline"],
            "recovery_action": item["recovery_action"],
            "recovery_attempt_delta": 0,
        }
        values.update(changes)
        return TransitionPatch(**values)

    def _transition(
        self,
        item: Mapping[str, Any],
        event_type: str,
        patch: TransitionPatch,
        payload: Mapping[str, Any],
    ) -> None:
        self.registry.transition(
            work_item_id=str(item["id"]),
            expected_version=int(item["version"]),
            source="phase3-bootstrap-controller",
            source_event_id=f"{item['id']}:{event_type}:{item['version']}",
            event_type=event_type,
            observed_at=_now(),
            patch=patch,
            event_payload=dict(payload),
        )

    def _worktree_record(self, item: Mapping[str, Any]) -> WorktreeRecord:
        packet = dict(item["work_packet"])
        state = dict(item.get("external_refs") or {})
        path = state.get("worktree_path")
        starting_ref = str(packet["starting_ref"])
        if isinstance(path, str):
            return WorktreeRecord(
                self.repo_root,
                Path(path),
                starting_ref,
                str(state.get("branch_name") or f"cec/bootstrap/{item['id']}"),
            )
        return create_worktree(
            repo_root=self.repo_root,
            worktree_root=self.worktree_root,
            work_item_id=str(item["id"]),
            starting_ref=starting_ref,
        )

    def _command(self, item: Mapping[str, Any]) -> WorkerCommand:
        packet = dict(item["work_packet"])
        record = self._worktree_record(item)
        packet_hash = hashlib.sha256(
            json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return WorkerCommand(
            command_id=str(item["next_signal_key"]),
            work_item_id=str(item["id"]),
            worker_kind=self.worker_kind,
            lease_token=UUID(str(item["lease_token"])),
            lease_epoch=int(item["lease_epoch"]),
            packet_hash=packet_hash,
            packet=packet,
            working_directory=record.worktree_path,
            result_schema=BOOTSTRAP_RESULT_SCHEMA,
        )

    def _handle(self, item: Mapping[str, Any]) -> WorkerHandle | None:
        command_id = str(item["next_signal_key"])
        record = self._worktree_record(item)
        path = record.worktree_path / ".cec" / f"{command_id}.process.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            return WorkerHandle(
                command_id,
                str(item["id"]),
                str(doc["worker_instance_id"]),
                int(doc["pid"]),
                None,
                datetime.fromisoformat(str(doc["started_at"])),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def reconcile_once(self, work_item_id: str) -> str:
        item = self.load_item(work_item_id)
        now = _now()
        if item["stage"] == "COMPLETE":
            deliver_pending(self.bridge_outbox)
            return "COMPLETE"

        if item["stage"] == "READY":
            packet = dict(item["work_packet"])
            record = create_worktree(
                repo_root=self.repo_root,
                worktree_root=self.worktree_root,
                work_item_id=str(item["id"]),
                starting_ref=str(packet["starting_ref"]),
            )
            command_id = f"phase3-{work_item_id}-command"
            self._transition(
                item,
                "WORKTREE_PREPARED",
                self._patch(
                    item,
                    stage="EXECUTING",
                    wait_reason="WORKER",
                    custodian_type="CONTROLLER",
                    custodian_id=CONTROLLER_ID,
                    lease_token=str(uuid4()),
                    lease_epoch_delta=1,
                    lease_expires_at=now + timedelta(minutes=20),
                    next_signal_type="LAUNCH_ACK",
                    next_signal_key=command_id,
                    next_signal_deadline=now + timedelta(minutes=2),
                    recovery_action={"action": "RECONCILE_PROCESS_SIDECAR"},
                ),
                {
                    "worktree_path": str(record.worktree_path),
                    "starting_ref": record.starting_ref,
                    "branch_name": record.branch_name,
                    "cleanup_policy": record.cleanup_policy,
                    "command_id": command_id,
                },
            )
            return "WORKTREE_PREPARED"

        if item["stage"] == "EXECUTING" and item["custodian_type"] == "CONTROLLER":
            command = self._command(item)
            handle = asyncio.run(self.adapter.launch(command))
            self._transition(
                item,
                "COMMAND_ACKNOWLEDGED",
                self._patch(
                    item,
                    custodian_type="WORKER",
                    custodian_id=handle.worker_instance_id,
                    lease_expires_at=now + timedelta(minutes=20),
                    next_signal_type="WORKER_HEARTBEAT",
                    next_signal_deadline=now + timedelta(minutes=2),
                    recovery_action={"action": "OBSERVE_WORKER"},
                ),
                {
                    "command_id": command.command_id,
                    "worker_instance_id": handle.worker_instance_id,
                    "pid": handle.pid,
                    "worktree_path": str(command.working_directory),
                },
            )
            return "COMMAND_ACKNOWLEDGED"

        if item["stage"] == "EXECUTING" and item["custodian_type"] == "WORKER":
            handle = self._handle(item)
            if handle is None:
                return "WORKER_UNOBSERVABLE"
            command = self._command(item)
            with _working_directory(command.working_directory):
                observation = asyncio.run(self.adapter.observe(handle))
            if observation.state is WorkerProcessState.RUNNING:
                self._transition(
                    item,
                    "WORKER_HEARTBEAT",
                    self._patch(
                        item,
                        next_signal_deadline=now + timedelta(minutes=2),
                    ),
                    {"command_id": command.command_id, "state": observation.state.value},
                )
                return "WORKER_RUNNING"
            claim = asyncio.run(self.adapter.collect_result(handle, command))
            if claim is None:
                return "RESULT_MISSING"
            record = self._worktree_record(item)
            write_unified_diff(record, Path(str(command.packet["diff_artifact_path"])))
            self._transition(
                item,
                "RESULT_CLAIMED",
                self._patch(
                    item,
                    stage="VERIFYING",
                    wait_reason="REVIEW",
                    custodian_type="CONTROLLER",
                    custodian_id=CONTROLLER_ID,
                    lease_expires_at=now + timedelta(minutes=5),
                    next_signal_type="CODE_EVIDENCE_VERIFICATION",
                    next_signal_deadline=now + timedelta(minutes=2),
                    recovery_action={"action": "VERIFY_CODE_CHANGE_ARTIFACTS"},
                ),
                {"claim": _claim_dict(claim, command)},
            )
            return "RESULT_CLAIMED"

        if item["stage"] == "VERIFYING" and item["wait_reason"] == "REVIEW":
            with psycopg.connect(database_url(), row_factory=dict_row) as conn:
                event = conn.execute(
                    """SELECT payload FROM cec.events WHERE work_item_id=%s
                    AND event_type='RESULT_CLAIMED' ORDER BY id DESC LIMIT 1""",
                    (work_item_id,),
                ).fetchone()
            if event is None:
                return "CLAIM_UNOBSERVABLE"
            packet = dict(item["work_packet"])
            record = self._worktree_record(item)
            evidence = verify_code_change_claim(
                event["payload"]["claim"],
                worktree=record.worktree_path,
                starting_ref=str(packet["starting_ref"]),
                packet=packet,
            )
            notification = (
                f"# CEC bootstrap complete: {work_item_id}\n\n"
                "The first self-dispatched build task completed with mechanical evidence.\n\n"
                f"- Files changed: `{', '.join(evidence['files_changed'])}`\n"
                f"- Test output: `{evidence['test_output_artifact']}`\n"
                f"- Diff artifact: `{evidence['diff_artifact']}`\n"
                f"- Diff SHA-256: `{evidence['diff_sha256']}`\n"
            )
            self._transition(
                item,
                "COMPLETE",
                self._patch(
                    item,
                    stage="COMPLETE",
                    wait_reason="NONE",
                    custodian_type="CONTROLLER",
                    custodian_id=CONTROLLER_ID,
                    evidence_state=evidence,
                    completed_at=now,
                ),
                {"evidence": evidence, "notification_markdown": notification},
            )
            return "COMPLETE_TRANSITION"

        return "NO_ACTION"


def run_scan_once(controller: BootstrapController) -> list[tuple[str, str]]:
    outcomes: list[tuple[str, str]] = []
    for work_item_id in controller.due_items():
        outcomes.append((work_item_id, controller.reconcile_once(work_item_id)))
    return outcomes
