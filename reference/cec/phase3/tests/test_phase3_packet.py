from reference.cec.phase3.packet import (
    ALLOWED_PATHS,
    P4_ALLOWED_PATHS,
    bootstrap_packet,
    p4_packet,
)


def test_bootstrap_packet_locks_d1_scope():
    packet = bootstrap_packet()
    assert packet["task_class"] == "CIRCUIT_BUILD"
    assert packet["allowed_paths"] == ALLOWED_PATHS
    assert packet["new_files_allowed"] is False
    assert packet["estimated_duration_seconds"] == 600
    assert "collect_result" in packet["objective"]


def test_p4_packet_locks_confinement_scope():
    packet = p4_packet()
    assert packet["task_class"] == "CIRCUIT_BUILD"
    assert packet["allowed_paths"] == P4_ALLOWED_PATHS
    # New files are permitted, but only at paths the contract pre-names —
    # the verifier's exact-path allow-list is what makes that enforceable.
    assert packet["new_files_allowed"] is True
    assert "reference/cec/phase3/sandbox/worker.sb" in packet["allowed_paths"]
    assert "sandbox-exec" in packet["objective"]
    assert "outside the worktree fails" in packet["objective"]
    assert packet["authority_class"] == "ROUTINE"

