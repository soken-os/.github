#!/bin/sh
set -eu
phase3_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$phase3_dir/../../.." && pwd)
python_bin="$phase3_dir/../phase0/.venv/bin/python"
test -x "$python_bin" || { echo "Phase 3 gated: run Phase 0 proof first" >&2; exit 3; }
cd "$repo_dir"
"$python_bin" -c 'from reference.cec.phase1.gate import require_pass; require_pass()'
"$python_bin" -m pytest -q reference/cec/phase3/tests
echo "Phase 3 bootstrap scaffold tests passed."
