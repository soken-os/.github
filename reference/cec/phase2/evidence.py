"""Mechanical verification; worker prose never determines completion."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class EvidenceRejected(RuntimeError):
    pass


def verify_claim(claim: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    if claim.get("status") != "RESULT_CLAIMED":
        raise EvidenceRejected("worker did not claim a completed result")
    evidence = claim.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != 1:
        raise EvidenceRejected("exactly one artifact is required")
    item = evidence[0]
    if not isinstance(item, Mapping) or item.get("kind") != "file":
        raise EvidenceRejected("artifact evidence must be a file")
    artifact = Path(str(item.get("path", ""))).resolve()
    allowed_root = workspace.resolve()
    if not artifact.is_relative_to(allowed_root):
        raise EvidenceRejected("artifact escaped the Phase-2 workspace")
    if not artifact.is_file():
        raise EvidenceRejected("claimed artifact does not exist")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != item.get("sha256"):
        raise EvidenceRejected("artifact SHA-256 does not match the claim")
    text = artifact.read_text(encoding="utf-8")
    if "passed" not in text or " failed" in text.lower():
        raise EvidenceRejected("artifact does not prove a passing test run")
    return {"completion_verified": True, "artifact": str(artifact), "sha256": digest}
