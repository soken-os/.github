"""DBOS per-work-item singleton reconciliation queue."""

from __future__ import annotations

from dbos import DBOS, SetEnqueueOptions
from dbos import error as dbos_error

RECONCILE_QUEUE = "cec-reconcile-by-work-item"


def register_reconcile_queue() -> None:
    DBOS.register_queue(RECONCILE_QUEUE, partition_queue=True, concurrency=1)


def enqueue_reconcile(work_item_id: str, trigger_id: str, reconcile_one):
    """Serialize per item and collapse duplicate triggers for one scan epoch."""
    with SetEnqueueOptions(
        queue_partition_key=work_item_id,
        # The trigger ID is stable for a redelivered event/timer tick, but new
        # level-triggered cycles get new IDs and are therefore not suppressed.
        deduplication_id=f"reconcile:{work_item_id}:{trigger_id}",
    ):
        try:
            return DBOS.enqueue_workflow(RECONCILE_QUEUE, reconcile_one, work_item_id)
        except dbos_error.DBOSQueueDeduplicatedError:
            return None
