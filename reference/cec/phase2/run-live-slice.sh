#!/bin/sh
set -eu
phase2_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$phase2_dir/../../.." && pwd)
python_bin="$phase2_dir/../phase0/.venv/bin/python"
test -x "$python_bin" || { echo "Phase 2 gated: run Phase 0 and Phase 1 proofs first" >&2; exit 3; }
cd "$repo_dir"
"$python_bin" -c 'from reference.cec.phase1.gate import require_pass; require_pass()'
docker compose -f reference/cec/phase0/compose.yaml up -d --wait
"$python_bin" -m pip install -q -e reference/cec/phase0
"$python_bin" -m reference.cec.phase2.bootstrap
CEC_RUN_POSTGRES_TESTS=1 "$python_bin" -m pytest -q \
  reference/cec/phase0/tests reference/cec/phase1/tests reference/cec/phase2/tests
"$python_bin" -m reference.cec.phase2.kill_harness --worker SCRIPT
if [ "${CEC_PHASE2_SCRIPT_ONLY:-0}" = "1" ]; then
  echo "Phase 2 dry run passed; real Claude acceptance intentionally skipped."
  exit 0
fi
command -v claude >/dev/null 2>&1 || {
  echo "Phase 2 requires the authenticated Claude CLI for live acceptance" >&2
  exit 4
}
"$python_bin" -m reference.cec.phase2.kill_harness --worker CLAUDE_CODE
echo "Phase 2 accepted: real Claude worker completed with two controller deaths."
