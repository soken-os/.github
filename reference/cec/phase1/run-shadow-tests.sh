#!/bin/sh
set -eu
phase1_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$phase1_dir/../../.." && pwd)
python_bin="$phase1_dir/../phase0/.venv/bin/python"
test -x "$python_bin" || { echo "Phase 1 gated: run reference/cec/phase0/run-proof.sh first" >&2; exit 3; }
cd "$repo_dir"
"$python_bin" -c 'from reference.cec.phase0.gate import require_pass; require_pass()'
"$python_bin" -m reference.cec.phase1.bootstrap
CEC_RUN_POSTGRES_TESTS=1 "$python_bin" -m pytest -q \
  reference/cec/phase0/tests reference/cec/phase1/tests
