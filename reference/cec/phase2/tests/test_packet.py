from reference.cec.phase2.packet import load_packet


def test_hand_authored_packet_validates_and_materializes(tmp_path):
    packet = load_packet(tmp_path, "SCRIPT")
    assert packet["task_class"] == "LOW_RISK_TEST_REPORT"
    assert packet["argv"]
    assert packet["artifact_path"].endswith("live-slice-test-output.txt")
