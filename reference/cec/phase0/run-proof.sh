#!/bin/sh
set -eu
phase0_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$phase0_dir/../../.." && pwd)
docker compose -f "$phase0_dir/compose.yaml" up -d --wait
/opt/homebrew/bin/python3.12 -m venv "$phase0_dir/.venv"
. "$phase0_dir/.venv/bin/activate"
python -m pip install -e "$phase0_dir"
cd "$repo_dir"
python -m reference.cec.phase0.bootstrap
CEC_RUN_POSTGRES_TESTS=1 pytest -q reference/cec/phase0/tests
python -m reference.cec.phase0.kill_harness
python -m reference.cec.phase0.gate
echo "Phase 0 substrate and continuation invariant proof passed."
