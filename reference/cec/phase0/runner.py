"""One DBOS process used by the kill/restart harness."""

from __future__ import annotations

import os

from dbos import DBOS, DBOSConfig, SetWorkflowID

from .bootstrap import database_url, migrate
from .workflow import task_151_replay


def main() -> None:
    migrate()
    config: DBOSConfig = {
        "name": "cec-phase0",
        "system_database_url": database_url(),
        # Stable identity is required for self-hosted DBOS crash recovery.
        "executor_id": "cec-phase0-controller",
    }
    DBOS(config=config)
    DBOS.launch()
    with SetWorkflowID(os.environ["CEC_PHASE0_WORKFLOW_ID"]):
        handle = DBOS.start_workflow(task_151_replay)
    handle.get_result()
    DBOS.destroy(workflow_completion_timeout_sec=2)


if __name__ == "__main__":
    main()
