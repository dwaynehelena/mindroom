# Environment inventory — edge-fleet-mesh-learning

**Date:** 2026-08-20  
**Intent:** 260816-edge-fleet-mesh-learning  
**Stage:** environment-provisioning (4.2)  
**NFR6:** local single-user install only. AWS / Kubernetes / Secrets Manager were not provisioned.

## Purpose

Inventory the **actual** environments this intent can run in. Construction skipped `infrastructure-design`, so `infrastructure-specification` is **absent**. This stage does **not** invent VPCs, EKS, or Parameter Store. Delivery target is the operator local MindRoom process plus existing SQLite under `{storage_root}` as already defined in `cd-config`.

## Upstream inputs

| Artifact | Path / status | How 4.2 uses it |
|----------|---------------|-----------------|
| `infrastructure-specification` | **absent** — `construction/infrastructure-design/` does not exist (SKIP: local install, existing SQLite) | Do not provision AWS. Treat skipped infra as the specification: no cloud env to create. |
| `cd-config` | `operation/deployment-pipeline/cd-config.md` | Promotion matrix, flags, dry-run, and the rule that 4.2 must not auto-mount |
| NFR6 | `inception/requirements-analysis/requirements.md` | Local single-user install only |
| C2 | `inception/contract-design/contract-summary.md` | SQLite `edge_fleet.db` is the fleet store |

## Environments (actual)

| Environment | What it is | Provisioned? | In 4.2 scope |
|-------------|------------|--------------|--------------|
| **Unmounted local** (ship default) | launchd `chat.mindroom.local` → `/Users/dwayne/mindroom/.venv/bin/mindroom run` | Yes — already running | Inventory only |
| **CI candidate** | GitHub Actions job in `.github/workflows/edge-fleet-mesh-learning.yml` | Yes — workflow exists from 3.7 | Do not dispatch remote Actions |
| **Mounted local** (activation) | Same process after operator env + restart | Env file already contains enable keys from **2026-08-10** (pre-4.2). Running process does **not** show a live fleet mount | Do not write `.env`, do not restart |
| **AWS / VPC / EKS / RDS** | Cloud infra | **Not present** and **not created** | Skip |
| **Hosted / k8s / GHCR** | Product charts under `cluster/k8s/` | Charts exist for the product; they contain **zero** `MINDROOM_EDGE_FLEET_*` keys | Out of scope (NFR6) |

## Local install (the only target)

| Item | Value |
|------|-------|
| Host | Darwin `MacBook-Pro-3.local` 24.6.0 x86_64 (`macosx_15_0_x86_64`) |
| Interpreter | `/Users/dwayne/mindroom/.venv/bin/python` (CPython 3.13.12) |
| LaunchAgent | `/Users/dwayne/Library/LaunchAgents/chat.mindroom.local.plist` |
| PID (observed 2026-08-20T19:54Z) | `35649` started Thu Aug 20 16:01:34 2026 |
| Listen | `*:8765` (IPv4) |
| Config | `MINDROOM_CONFIG_PATH=/Users/dwayne/.mindroom/config.yaml` |
| Storage root | `MINDROOM_STORAGE_PATH=/Users/dwayne/.mindroom/mindroom_data` |
| Config-adjacent env | `/Users/dwayne/.mindroom/.env` |
| Repo-root `.env` | exists; **no** `MINDROOM_EDGE_FLEET_*` assignments |
| `.env.example` | commented keys only; no `MINDROOM_MESH_ENROLLMENT` |

LaunchAgent `EnvironmentVariables` set config + storage + PATH only. They do **not** set `MINDROOM_EDGE_FLEET_*`. Flag resolution is therefore config-adjacent `.env` via `RuntimePaths` (process env, then `.env`).

## SQLite and related stores

| Store | Path | 4.2 observation |
|-------|------|-----------------|
| Fleet (C2) | `/Users/dwayne/.mindroom/mindroom_data/edge_fleet.db` | Exists, 45056 bytes, mtime 2026-08-10T21:34:08Z, `PRAGMA integrity_check=ok`, journal_mode=wal. Tables: `edge_node` (10), `edge_job` (6 completed), `edge_enrollment_nonce` (19), `edge_request_nonce` (24), `edge_fleet_audit` (0). PID 35649 did **not** hold this file open. |
| Flight recorder (C7) | `/Users/dwayne/.mindroom/mindroom_data/tracking/flight_recorder.db` | Exists; `flight_record` count 1991 |
| Learning loop SQLite | `{storage_root}/**/learning_loop.db` | **Absent** (in-process U3 publishers use other runtime roots; not a 4.2 provision gap) |
| Backup copy | `~/.mindroom/backups/20260817T231200Z-pre-upgrade-2026.8.79/` | Contains a copied `edge_fleet.db`; not the live store |

Default path when `MINDROOM_EDGE_FLEET_PATH` is unset: `{storage_root}/edge_fleet.db`. Live env does not set the path override.

## Feature flags (local env, not Secrets Manager)

Inspected `/Users/dwayne/.mindroom/.env` **read-only**. Values are not copied here.

| Key | Live file (2026-08-20 inspect) | 4.2 action |
|-----|--------------------------------|------------|
| `MINDROOM_EDGE_FLEET_ENABLED` | present, nonempty, literal `true` | **Not written**. Pre-existing (file mtime 2026-08-10T17:27:48Z; backup `.env.bak-pre-edge-fleet-20260806-203839`) |
| `MINDROOM_EDGE_FLEET_ENROLLMENT_KEY` | present; URL-safe Base64 decodes to 48 bytes (≥32) | Not written |
| `MINDROOM_EDGE_FLEET_NODE_ALLOWLIST` | present; 2 comma-separated node ids | Not written |
| `MINDROOM_EDGE_FLEET_PATH` | unset | Default store path applies |
| `MINDROOM_MESH_ENROLLMENT` | absent | Correct (C8 `surface_absent`) |

`cd-config` still forbids auto-enable. 4.2 does not treat the pre-existing `true` as a mount performed by this stage.

## Adjacent local processes (not provisioned by 4.2)

| Process | launchd | 4.2 |
|---------|---------|-----|
| MindRoom | `chat.mindroom.local` running pid 35649 | Inventory only |
| OpenClaw gateway | `ai.openclaw.gateway` running (port 18789) | Not a fleet worker; do not enroll |
| Hermes gateway | `ai.hermes.gateway` running | Not a fleet worker; do not enroll |
| Tailscale | `/usr/local/bin/tailscale` — this host `macbook-pro-3` 100.80.168.13; peer `macbook-pro` active | Connectivity exists; **no** live worker-over-Tailscale was started |

Repo `config.yaml` agent `openclaw.learning` is `false`. Live `~/.mindroom/config.yaml` has no OpenClaw-named agent; every listed agent has `learning: false`.

## Cloud / k8s skip evidence

- No `construction/infrastructure-design/` directory.
- `cluster/k8s/{runtime,instance,platform}/values.yaml` contain no `MINDROOM_EDGE_FLEET_*` and no `EDGE_FLEET` tokens.
- No AWS CLI provisioning, no Terraform apply, no kubectl apply, no Secrets Manager / Parameter Store writes.

## What 4.2 will not claim

- Live env mutation or production deploy.
- Running-process fleet mount (health JSON from `GET http://127.0.0.1:8765/api/health` had **no** `edge_fleet` fragment; store file was not open).
- Live worker-over-Tailscale.
- Full `tests/` verification.
