# Pipeline validation — deployment-pipeline (4.1)

**Date:** 2026-08-20  
**Interpreter:** `/Users/dwayne/mindroom/.venv/bin/python`  
**Host:** macOS `macosx_15_0_x86_64`

## Commands

```sh
python3 -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/edge-fleet-mesh-learning.yml').read_text()); print('workflow-yaml-ok')"
# workflow-yaml-ok

bash -n scripts/testing/run_edge_fleet_mesh_learning_ci.sh
bash -n scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh
python3 -m py_compile scripts/testing/edge_fleet_mesh_learning_cd_dry_run.py

PYTHON=/Users/dwayne/mindroom/.venv/bin/python \\
  bash scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh
```

## Result

```
python: /Users/dwayne/mindroom/.venv/bin/python
workflow-yaml-ok
bash-n-ok
docs-ok
flag-off-ok
flag-false-ok
unmounted-routes-ok
handshake-surface-absent-ok
openclaw-learning-false-ok
env-example-ok
cd-dry-run-ok
cd-dry-run-complete
```

Constructor isolation: the checker sets `MINDROOM_EDGE_FLEET_ENABLED=false` in **process env only** before importing `mindroom.api.main`, so composition-root mount cannot open the live `{storage_root}/edge_fleet.db`. Live `~/.mindroom/.env` was not written. Repo-root `.env` has no `MINDROOM_EDGE_FLEET_*` assignments.

## Explicitly not run

- Live `.env` mutation / process restart / production mount
- Live worker-over-Tailscale (OpenClaw or Hermes `EdgeNodeClient`)
- Remote GitHub Actions dispatch
- Full `tests/` collect (NFR5 minor m2)
- `uv run` on this host (onnxruntime wheel miss)
- AWS / k8s / GHCR promotion
