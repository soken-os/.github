import hashlib

import pytest

from reference.cec.phase2.evidence import EvidenceRejected, verify_claim


def claim(path, digest):
    return {
        "status": "RESULT_CLAIMED",
        "evidence": [
            {"kind": "file", "path": str(path), "sha256": digest, "contains": "passed"}
        ],
    }


def test_verifies_typed_claim_and_file(tmp_path):
    artifact = tmp_path / "test-output.txt"
    artifact.write_text("14 passed in 0.42s\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    evidence = verify_claim(claim(artifact, digest), tmp_path)
    assert evidence["completion_verified"] is True


def test_rejects_claimed_hash_mismatch(tmp_path):
    artifact = tmp_path / "test-output.txt"
    artifact.write_text("14 passed\n", encoding="utf-8")
    with pytest.raises(EvidenceRejected, match="SHA-256"):
        verify_claim(claim(artifact, "0" * 64), tmp_path)


def test_rejects_artifact_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("14 passed\n", encoding="utf-8")
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    with pytest.raises(EvidenceRejected, match="escaped"):
        verify_claim(claim(outside, digest), tmp_path)
