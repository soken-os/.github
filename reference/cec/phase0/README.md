# CEC Phase 0 — substrate proof

No live task, model, GitHub mutation, Railway mutation, or bridge mutation occurs here.

Run on the Mac:

```sh
reference/cec/phase0/run-proof.sh
```

The script starts a Postgres 16 container bound only to `127.0.0.1:55432`, creates a local Python 3.12 environment, applies `../migrations/001_work_items.sql`, and runs both the constraint tests and the DBOS crash harness.

Pass criteria:

```text
continuation coverage=100%; orphan time=0; boundaries=5
Phase 0 substrate and continuation invariant proof passed.
```

The five forced-death boundaries are `REGISTERED`, `DISPATCHED`, `EXIT_OBSERVED`, `RECOVERY_PLANNED`, and `EVIDENCE_VERIFIED`. At every death, the harness queries the registry before restart and requires the in-flight item to retain its custodian, next signal, deadline, and recovery action.
