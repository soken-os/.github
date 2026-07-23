# CEC Phase 1 — durable custody spine

Phase 1 is registry-only shadow mode. It cannot launch or terminate workers, mutate GitHub, deploy, notify, or dispatch live work.

It is hash-gated by the current Phase-0 migration and proof receipt. Run:

```sh
reference/cec/phase0/run-proof.sh
reference/cec/phase1/run-shadow-tests.sh
```

The first command emits an ignored `.phase0-proof.json` receipt only after all tests and all five controller-kill drills pass. Phase-1 bootstrap, registry construction, and the sentinel refuse to run without a matching receipt.

The registry transition uses one DBOS `SQLAlchemyDatasource` serializable transaction to reserve the unique source event, update `work_items` with an expected-version CAS, and append the immutable event atomically. The sentinel is read-only:

```sh
reference/cec/phase0/.venv/bin/python -m reference.cec.phase1.sentinel
```

Exit `0` means fresh; exit `2` means one or more nonterminal items are more than 60 seconds beyond a lease or signal deadline.
