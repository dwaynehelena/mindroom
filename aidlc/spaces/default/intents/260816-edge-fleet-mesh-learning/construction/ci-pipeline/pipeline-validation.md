# Pipeline validation — edge-fleet-mesh-learning

**Date:** 2026-08-20  
**Interpreter:** `/Users/dwayne/mindroom/.venv/bin/python`  
**Host:** macOS `macosx_15_0_x86_64`

## Commands

```sh
python3 -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/edge-fleet-mesh-learning.yml').read_text()); print('workflow-yaml-ok')"
# workflow-yaml-ok

bash -n scripts/testing/run_edge_fleet_mesh_learning_ci.sh

PYTHON=/Users/dwayne/mindroom/.venv/bin/python \
  bash scripts/testing/run_edge_fleet_mesh_learning_ci.sh -p no:cacheprovider
```

## Result

```
python: /Users/dwayne/mindroom/.venv/bin/python
import-ok
136 passed, 3 skipped, 2 warnings in 27.93s
```

Matches `construction/build-and-test/test-results.md` combined counts (88 + 15 + 25 + 8). Skips are the three P9 bind-default tests. Warnings are quality minors m3 (HTTP 422) and m4 (asyncio mark).

Not run: `uv run` (onnxruntime wheel miss on this host), live Tailscale helper, live worker path, full `tests/` collect.

GitHub Actions job itself is not executed here (no `act` / no remote dispatch). Workflow YAML parses; runner is `ubuntu-latest` with `uv sync --locked --group dev`.