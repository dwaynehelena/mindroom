#!/usr/bin/env bash
# Intent-scoped CI for 260816-edge-fleet-mesh-learning (U1–U4).
# Commands match construction/build-and-test/test-results.md.
#
# GitHub Actions: uv sync --group dev, then this script via `uv run`.
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

"$PYTHON_BIN" -c "from mindroom.api.edge_fleet import create_edge_fleet_router; from mindroom.handshake_inspect import inspect_openclaw; print('import-ok')"

test -f docs/edge-fleet-rollback.md
test -f docs/edge-fleet.md

# U1 ∪ U2 listed (deduplicated) + U3 listed + U3 FR3.3/FR3.4 extras + U4 listed
"$PYTHON_BIN" -m pytest \
  tests/test_edge_fleet.py \
  tests/test_edge_node.py \
  tests/test_edge_tailscale.py \
  tests/test_p9_remediation.py \
  tests/api/test_edge_fleet_api.py \
  tests/api/test_edge_fleet_lifecycle.py \
  tests/api/test_edge_fleet_revocation_api.py \
  tests/api/test_edge_fleet_revocation_core.py \
  tests/api/test_edge_fleet_security.py \
  tests/api/test_edge_fleet_live.py \
  tests/api/test_api.py::test_health_check \
  tests/test_learning_promotion.py \
  tests/test_learning_capture.py \
  tests/test_learning_runtime.py \
  tests/test_learning_loop.py \
  tests/test_learning_publishers.py \
  tests/test_learning_stable_publishers.py \
  tests/test_learning_candidates.py \
  tests/test_handshake_inspect.py \
  --tb=short \
  "$@"