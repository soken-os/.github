from reference.cec.phase3.packet import ALLOWED_PATHS, bootstrap_packet


def test_bootstrap_packet_locks_d1_scope():
    packet = bootstrap_packet()
    assert packet["task_class"] == "CIRCUIT_BUILD"
    assert packet["allowed_paths"] == ALLOWED_PATHS
    assert packet["new_files_allowed"] is False
    assert packet["estimated_duration_seconds"] == 600
    assert "collect_result" in packet["objective"]

