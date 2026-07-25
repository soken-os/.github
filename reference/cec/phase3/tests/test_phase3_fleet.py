"""Fleet charter and packet validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator

from reference.cec.phase3.fleet import (
    CHARTERS,
    FINDING_SCHEMA,
    FLEET_FINDINGS_DIR,
    PROGRAM_CEC_REVIEW,
    _findings_path,
    _review_objective,
    fleet_packets,
)
from reference.cec.phase3.packet import FORBIDDEN_PATHS, PACKET_SCHEMA


def _repo_root() -> Path:
    root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return Path(root)


def test_exactly_ten_charters():
    assert len(CHARTERS) == 10


def test_charter_ids_sequential():
    ids = [c["id"] for c in CHARTERS]
    assert ids == [f"R{i:02d}" for i in range(1, 11)]


def test_charter_ids_unique():
    ids = [c["id"] for c in CHARTERS]
    assert len(ids) == len(set(ids))


def test_charter_groups():
    groups = {c["id"]: c["group"] for c in CHARTERS}
    assert all(groups[f"R{i:02d}"] == "core" for i in range(1, 6))
    assert all(groups[f"R{i:02d}"] == "security" for i in range(6, 8))
    assert all(groups[f"R{i:02d}"] == "extra" for i in range(8, 11))


def test_every_charter_has_required_fields():
    required = {"id", "lens", "group", "input_bundle", "objective", "out_of_scope"}
    for charter in CHARTERS:
        assert required <= set(charter), f"{charter['id']} missing fields"


def test_input_bundles_reference_existing_files():
    root = _repo_root()
    for charter in CHARTERS:
        for path in charter["input_bundle"]:
            assert (root / path).exists(), (
                f"{charter['id']} references missing file: {path}"
            )


def test_findings_output_not_in_forbidden_paths():
    for charter in CHARTERS:
        path = _findings_path(charter["id"])
        for forbidden in FORBIDDEN_PATHS:
            if forbidden.endswith("/"):
                assert not path.startswith(forbidden), (
                    f"{charter['id']} findings path {path} is in FORBIDDEN_PATHS"
                )
            else:
                assert path != forbidden, (
                    f"{charter['id']} findings path {path} is in FORBIDDEN_PATHS"
                )


def test_findings_paths_unique():
    paths = [_findings_path(c["id"]) for c in CHARTERS]
    assert len(paths) == len(set(paths))


def test_findings_paths_in_fleet_dir():
    for charter in CHARTERS:
        assert _findings_path(charter["id"]).startswith(FLEET_FINDINGS_DIR)


def test_finding_schema_is_valid():
    Draft202012Validator.check_schema(FINDING_SCHEMA)


def test_objectives_include_charter_details():
    for charter in CHARTERS:
        obj = _review_objective(charter)
        assert charter["id"] in obj
        assert charter["lens"] in obj
        assert charter["out_of_scope"] in obj
        for path in charter["input_bundle"]:
            assert path in obj


def test_packets_validate_against_schema():
    packets = fleet_packets()
    assert len(packets) == 10
    validator = Draft202012Validator(PACKET_SCHEMA)
    for packet in packets:
        validator.validate(packet)


def test_packets_use_review_program():
    for packet in fleet_packets():
        assert packet["program"] == PROGRAM_CEC_REVIEW


def test_packets_allow_only_findings_file():
    packets = fleet_packets()
    for charter, packet in zip(CHARTERS, packets, strict=True):
        expected = [_findings_path(charter["id"])]
        assert packet["allowed_paths"] == expected


def test_packets_allow_new_files():
    for packet in fleet_packets():
        assert packet["new_files_allowed"] is True
