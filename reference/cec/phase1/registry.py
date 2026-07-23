"""Atomic CAS transition + append-only event using a DBOS datasource."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping

from dbos import SQLAlchemyDatasource
from sqlalchemy import text

from ..phase0.gate import require_pass


class StaleTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class TransitionPatch:
    stage: str
    wait_reason: str
    custodian_type: str
    custodian_id: str
    lease_token: str
    lease_epoch_delta: int
    lease_expires_at: datetime
    next_signal_type: str
    next_signal_key: str
    next_signal_deadline: datetime
    recovery_action: Mapping[str, Any]
    recovery_attempt_delta: int = 0


@dataclass(frozen=True)
class TransitionResult:
    applied: bool
    duplicate: bool
    work_item_id: str
    version: int
    event_id: int


def _sqlalchemy_url(database_url: str) -> str:
    return database_url.replace("postgresql://", "postgresql+psycopg://", 1)


class Registry:
    def __init__(self, database_url: str) -> None:
        require_pass()
        self.datasource = SQLAlchemyDatasource.create(
            _sqlalchemy_url(database_url), schema="dbos"
        )

    def transition(
        self,
        *,
        work_item_id: str,
        expected_version: int,
        source: str,
        source_event_id: str,
        event_type: str,
        observed_at: datetime,
        patch: TransitionPatch,
        event_payload: Mapping[str, Any],
    ) -> TransitionResult:
        return self.datasource.run_tx_step(
            {"name": "cec_phase1_transition", "isolation_level": "SERIALIZABLE"},
            self._transition_tx,
            work_item_id,
            expected_version,
            source,
            source_event_id,
            event_type,
            observed_at,
            asdict(patch),
            dict(event_payload),
        )

    def _transition_tx(
        self,
        work_item_id: str,
        expected_version: int,
        source: str,
        source_event_id: str,
        event_type: str,
        observed_at: datetime,
        patch: Mapping[str, Any],
        event_payload: Mapping[str, Any],
    ) -> TransitionResult:
        session = self.datasource.sql_session()
        reserved = session.execute(
            text(
                """INSERT INTO cec.events
                (work_item_id,source,source_event_id,event_type,from_version,to_version,payload,observed_at)
                VALUES (:work_item_id,:source,:source_event_id,:event_type,:from_version,:to_version,
                        CAST(:payload AS jsonb),:observed_at)
                ON CONFLICT (source,source_event_id) DO NOTHING
                RETURNING id"""
            ),
            {
                "work_item_id": work_item_id,
                "source": source,
                "source_event_id": source_event_id,
                "event_type": event_type,
                "from_version": expected_version,
                "to_version": expected_version + 1,
                "payload": json.dumps(event_payload),
                "observed_at": observed_at,
            },
        ).first()
        if reserved is None:
            existing = session.execute(
                text(
                    """SELECT id,work_item_id,to_version FROM cec.events
                    WHERE source=:source AND source_event_id=:source_event_id"""
                ),
                {"source": source, "source_event_id": source_event_id},
            ).one()
            if existing.work_item_id != work_item_id:
                raise StaleTransition(
                    "source event ID already belongs to another work item"
                )
            return TransitionResult(
                False, True, work_item_id, existing.to_version, existing.id
            )

        updated = session.execute(
            text(
                """UPDATE cec.work_items SET
                    stage=:stage, wait_reason=:wait_reason,
                    custodian_type=:custodian_type, custodian_id=:custodian_id,
                    lease_token=CAST(:lease_token AS uuid),
                    lease_epoch=lease_epoch+:lease_epoch_delta,
                    lease_expires_at=:lease_expires_at,
                    next_signal_type=:next_signal_type,
                    next_signal_key=:next_signal_key,
                    next_signal_deadline=:next_signal_deadline,
                    recovery_action=CAST(:recovery_action AS jsonb),
                    recovery_attempts=recovery_attempts+:recovery_attempt_delta,
                    updated_at=clock_timestamp(), version=version+1
                WHERE id=:work_item_id AND version=:expected_version
                  AND recovery_attempts+:recovery_attempt_delta <= max_recovery_attempts
                RETURNING version"""
            ),
            {
                **patch,
                "recovery_action": json.dumps(patch["recovery_action"]),
                "work_item_id": work_item_id,
                "expected_version": expected_version,
            },
        ).first()
        if updated is None:
            raise StaleTransition("work item version or recovery ceiling changed")
        return TransitionResult(True, False, work_item_id, updated.version, reserved.id)
