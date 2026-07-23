# CEC Phase 2 — one-task live slice

This slice runs one hand-authored, schema-validated, low-risk task: execute the CEC controller/action unit tests and save their output as a mechanically verified artifact. It makes no source edit and performs no GitHub or Railway mutation.

Prerequisites and run:

```sh
reference/cec/phase0/run-proof.sh
reference/cec/phase1/run-shadow-tests.sh
reference/cec/phase2/run-live-slice.sh
```

The final command first runs a deterministic `SCRIPT` dry run, then invokes the real authenticated Claude CLI through `ClaudeCodeAdapter`. In each run the harness kills the controller twice: once after acknowledged worker custody while the worker is still running, and once after `RESULT_CLAIMED` but before evidence verification.

Acceptance requires both runs to report:

```text
controller_kills=2; continuation coverage=100%; orphan time=0;
final=COMPLETE; notification=ACKNOWLEDGED
```

For code-only development, `CEC_PHASE2_SCRIPT_ONLY=1` skips the real-Claude run. That is a dry run and does not constitute Phase 2 acceptance.
