import os

import psycopg
import pytest

from reference.cec.phase0.bootstrap import database_url, migrate

pytestmark = pytest.mark.postgres


def test_nonterminal_item_cannot_be_orphaned():
    if not os.environ.get("CEC_RUN_POSTGRES_TESTS"):
        pytest.skip("set CEC_RUN_POSTGRES_TESTS=1 after docker compose up")
    migrate()
    with (
        psycopg.connect(database_url(), autocommit=True) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        conn.execute("""INSERT INTO cec.work_items
              (id,program,title,task_class,priority_class,stage,wait_reason,work_packet,authority_class)
              VALUES ('orphan','x','x','x',1,'READY','NONE','{}','ROUTINE')""")
