# Validation report — environment-provisioning (4.2)

**Date:** 2026-08-20  
**Intent:** 260816-edge-fleet-mesh-learning  
**Interpreter:** `/Users/dwayne/mindroom/.venv/bin/python`  
**Host:** Darwin `MacBook-Pro-3.local` 24.6.0 x86_64

## Verdict

**PASS** — local-SQLite environment is inventoried; AWS/k8s cloud provisioning is correctly skipped; CD dry-run remains green. This stage did **not** write live `~/.mindroom/.env` and did **not** restart `chat.mindroom.local`.

## What was validated

### 1. Upstream contract

- `cd-config` (`operation/deployment-pipeline/cd-config.md`) names the local process + `{storage_root}/edge_fleet.db` as the only target and forbids AWS/CodePipeline.
- `infrastructure-specification` is absent (`construction/infrastructure-design/` missing). Skip of cloud provision is the valid 4.2 outcome, not a defect.

### 2. Local paths exist

| Check | Result |
|-------|--------|
| LaunchAgent `chat.mindroom.local` | running, pid 35649 |
| `MINDROOM_STORAGE_PATH` | `/Users/dwayne/.mindroom/mindroom_data` |
| `MINDROOM_CONFIG_PATH` | `/Users/dwayne/.mindroom/config.yaml` |
| Config-adjacent `.env` | `/Users/dwayne/.mindroom/.env` readable |
| Fleet SQLite | `/Users/dwayne/.mindroom/mindroom_data/edge_fleet.db` integrity_check=ok |
| Repo `.env.example` fleet keys | commented; no `MINDROOM_MESH_ENROLLMENT` |
| k8s values | no `MINDROOM_EDGE_FLEET_*` |

### 3. CD dry-run (required 4.2 validation)

Command:

```sh
PYTHON=/Users/dwayne/mindroom/.venv/bin/python \
  bash scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh
```

Expected tokens (same as 4.1 `pipeline-validation.md`): `workflow-yaml-ok`, `bash-n-ok`, `docs-ok`, `flag-off-ok`, `flag-false-ok`, `unmounted-routes-ok`, `handshake-surface-absent-ok`, `openclaw-learning-false-ok`, `env-example-ok`, `cd-dry-run-ok`, `cd-dry-run-complete`.

Captured 2026-08-21 06:04:14 local:

```
python: /Users/dwayne/mindroom/.venv/bin/python
workflow-yaml-ok
bash-n-ok
docs-ok
2026-08-21 06:04:14 [info     ] Edge fleet is disabled — no routes mounted
flag-off-ok
flag-false-ok
2026-08-21 06:04:14 [info     ] Edge fleet is disabled — no routes mounted
unmounted-routes-ok
handshake-surface-absent-ok
openclaw-learning-false-ok
env-example-ok
cd-dry-run-ok
cd-dry-run-complete
```

Constructor isolation: the checker sets `MINDROOM_EDGE_FLEET_ENABLED=false` in **process env only** before importing `mindroom.api.main`. Live `~/.mindroom/.env` was not written (mtime remains 2026-08-10T17:27:48Z). Repo-root `.env` has no `MINDROOM_EDGE_FLEET_*` assignments.

### 4. Live process (observe only — not a mount claim)

```sh
curl -sS -m 8 http://127.0.0.1:8765/api/health
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

Observed 2026-08-20T19:54Z:

- Listen: python 35649 on `*:8765`
- Health JSON: `{"status":"healthy", ...}` **without** an `edge_fleet` key
- `lsof -p 35649` did not list `edge_fleet.db`
- stderr recent window had no "Edge fleet enabled" / "routes mounted" lines

Interpretation: the running process started 2026-08-20 16:01:34 and is **not** a demonstrated live fleet mount. Pre-existing `.env` `MINDROOM_EDGE_FLEET_ENABLED=true` (mtime 2026-08-10) is inventoried, not activated by 4.2. 4.3 may restart if it needs C6 health; 4.2 will not.

### 5. Secrets / Parameter Store audit (NFR6 mapping)

There is no AWS Secrets Manager or SSM Parameter Store for this intent. Secrets that would otherwise live there are local env keys in `~/.mindroom/.env` (mode `0600`). 4.2 did not print secret values and did not rotate them.

OpenClaw `learning:` remains false in repo `config.yaml`. Handshake remains `surface_absent` (dry-run).

## Explicitly not run

- Write/truncate of `~/.mindroom/.env`
- `launchctl kickstart` / process restart / production mount
- Live worker-over-Tailscale (`python -m mindroom.edge_node`)
- Remote GitHub Actions dispatch
- `uv run` (onnxruntime wheel miss on this host)
- AWS / kubectl / terraform apply
- Full `tests/` collect (NFR5 minor m2 from quality)

## Residual notes (do not block 4.2)

- Live health payload missing `edge_fleet` is a **running-build vs workspace-code** observation. Workspace `src/mindroom/api/main.py` includes the C6 fragment. Confirm after a restart in 4.3; do not restart here.
- Pre-existing enable keys in live `.env` predate this stage. Rollback remains `docs/edge-fleet-rollback.md` / `operation/deployment-pipeline/rollback-runbook.md`.
- Quality minors m1–m4 unchanged.
