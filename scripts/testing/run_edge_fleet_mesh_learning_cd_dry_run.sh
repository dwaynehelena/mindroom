#!/usr/bin/env bash
# Dry-run CD validation for 260816-edge-fleet-mesh-learning (operation 4.1).
# Does not mount the fleet, edit live .env, start workers, or mutate Tailscale.
#
# Local macosx_15_0_x86_64: PYTHON=.venv/bin/python (uv run is blocked by
# onnxruntime 1.25.1 having no macosx_15_0_x86_64 wheel).

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
elif [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3)"
fi

echo "python: ${PYTHON_BIN}"

"$PYTHON_BIN" -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/edge-fleet-mesh-learning.yml').read_text()); print('workflow-yaml-ok')"

bash -n scripts/testing/run_edge_fleet_mesh_learning_ci.sh
bash -n scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh
echo "bash-n-ok"

test -f docs/edge-fleet-rollback.md
test -f docs/edge-fleet.md
test -f aidlc/spaces/default/intents/260816-edge-fleet-mesh-learning/operation/deployment-pipeline/cd-config.md
test -f aidlc/spaces/default/intents/260816-edge-fleet-mesh-learning/operation/deployment-pipeline/deployment-strategy.md
test -f aidlc/spaces/default/intents/260816-edge-fleet-mesh-learning/operation/deployment-pipeline/rollback-runbook.md
echo "docs-ok"

"$PYTHON_BIN" scripts/testing/edge_fleet_mesh_learning_cd_dry_run.py

echo "cd-dry-run-complete"
