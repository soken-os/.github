"""Level-triggered controller for exactly one low-risk Phase-2 work item."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
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
from .evidence import verify_claim
from .notifications import deliver_pending
from .packet import RESULT_SCHEMA


def _now() -> datetime:
    return datetime.now(UTC)


def _claim_dict(claim: WorkerResultClaim) -> dict[str, Any]:
    return {
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


class OneTaskController:
    def __init__(
        self, workspace: Path, worker_kind: WorkerKind, bridge_outbox: Path
    ) -> None:
        self.workspace = workspace.resolve()
        os.chdir(self.workspace)  # Adapter sidecars are resolved from controller cwd.
        self.worker_kind = worker_kind
        self.bridge_outbox = bridge_outbox
        self.registry = Registry(database_url())
        self.adapter = (
            ClaudeCodeAdapter()
            if worker_kind is WorkerKind.CLAUDE_CODE
            else ScriptAdapter()
        )

    def load_item(self, work_item_id: str) -> dict[str, Any]:
        with psycopg.connect(database_url(), row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM cec.work_items WHERE id=%s", (work_item_id,)
            ).fetchone()
        if row is None:
            raise KeyError(work_item_id)
        return dict(row)

    def _command(self, item: Mapping[str, Any]) -> WorkerCommand:
        packet = dict(item["work_packet"])
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
            working_directory=self.workspace,
            result_schema=RESULT_SCHEMA,
        )

    def _handle(self, item: Mapping[str, Any]) -> WorkerHandle | None:
        command_id = str(item["next_signal_key"])
        path = self.workspace / ".cec" / f"{command_id}.process.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            return WorkerHandle(
                command_id,
                str(item["id"]),
                str(record["worker_instance_id"]),
                int(record["pid"]),
                None,
                datetime.fromisoformat(str(record["started_at"])),
            )
        except (
            FileNotFoundError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return None

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
            "next_signal_key": item.get("next_signal_key") or "phase2",
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
            source="phase2-controller",
            source_event_id=f"{item['id']}:{event_type}:{item['version']}",
            event_type=event_type,
            observed_at=_now(),
            patch=patch,
            event_payload=dict(payload),
        )

    def reconcile_once(self, work_item_id: str) -> str:
        item = self.load_item(work_item_id)
        now = _now()
        if item["stage"] == "COMPLETE":
            deliver_pending(self.bridge_outbox)
            return "COMPLETE"

        if item["stage"] == "READY":
            command_id = f"phase2-{work_item_id}-command"
            self._transition(
                item,
                "LAUNCH_INTENT",
                self._patch(
                    item,
                    stage="EXECUTING",
                    wait_reason="WORKER",
                    custodian_type="CONTROLLER",
                    custodian_id="phase2-controller",
                    lease_token=str(uuid4()),
                    lease_epoch_delta=1,
                    lease_expires_at=now + timedelta(minutes=15),
                    next_signal_type="LAUNCH_ACK",
                    next_signal_key=command_id,
                    next_signal_deadline=now + timedelta(minutes=2),
                    recovery_action={"action": "RECONCILE_PROCESS_SIDECAR"},
                ),
                {"command_id": command_id, "worker_kind": self.worker_kind.value},
            )
            return "LAUNCH_INTENT"

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
                    lease_expires_at=now + timedelta(minutes=15),
                    next_signal_type="WORKER_EXIT",
                    next_signal_deadline=now + timedelta(minutes=10),
                    recovery_action={"action": "OBSERVE_WORKER"},
                ),
                {
                    "command_id": command.command_id,
                    "worker_instance_id": handle.worker_instance_id,
                    "pid": handle.pid,
                },
            )
            return "COMMAND_ACKNOWLEDGED"

        if item["stage"] == "EXECUTING" and item["custodian_type"] == "WORKER":
            observed_handle = self._handle(item)
            if observed_handle is None:
                return "WORKER_UNOBSERVABLE"
            handle = observed_handle
            observation = asyncio.run(
                self.adapter.observe(handle, working_directory=self.workspace)
            )
            if observation.state is WorkerProcessState.RUNNING:
                return "WORKER_RUNNING"
            command = self._command(item)
            claim = asyncio.run(self.adapter.collect_result(handle, command))
            if claim is None:
                return "RESULT_MISSING"
            claim_payload = _claim_dict(claim)
            self._transition(
                item,
                "RESULT_CLAIMED",
                self._patch(
                    item,
                    stage="VERIFYING",
                    wait_reason="REVIEW",
                    custodian_type="CONTROLLER",
                    custodian_id="phase2-controller",
                    lease_expires_at=now + timedelta(minutes=5),
                    next_signal_type="EVIDENCE_VERIFICATION",
                    next_signal_deadline=now + timedelta(minutes=2),
                    recovery_action={"action": "VERIFY_CLAIMED_ARTIFACT"},
                ),
                {"claim": claim_payload},
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
            evidence = verify_claim(event["payload"]["claim"], self.workspace)
            notification = (
                f"# CEC task complete: {work_item_id}\n\n"
                f"Mechanical evidence verified.\n\n- Artifact: `{evidence['artifact']}`\n"
                f"- SHA-256: `{evidence['sha256']}`\n"
            )
            self._transition(
                item,
                "COMPLETE",
                self._patch(
                    item,
                    stage="COMPLETE",
                    wait_reason="NONE",
                    custodian_type="CONTROLLER",
                    custodian_id="phase2-controller",
                    evidence_state=evidence,
                    completed_at=now,
                ),
                {"evidence": evidence, "notification_markdown": notification},
            )
            return "COMPLETE_TRANSITION"
        return "NO_ACTION"
