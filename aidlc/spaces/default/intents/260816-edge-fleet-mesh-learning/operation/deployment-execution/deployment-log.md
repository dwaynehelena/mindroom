# Deployment log — edge-fleet-mesh-learning

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** deployment-execution (4.3)
**Host:** Darwin `MacBook-Pro-3.local` (local launchd `chat.mindroom.local`)
**Interpreter:** `/Users/dwayne/mindroom/.venv/bin/python`
**Workspace:** `/Users/dwayne/mindroom` (editable install `mindroom-2026.8.79`)
**This-run envelope:** same live-safety policy as 4.1/4.2 — no live host restart, no live volume/service mount, no AWS/k8s provisioning, no remote Actions.

## Upstream consumed

| Artifact | Path |
|----------|------|
| `cd-config` | `operation/deployment-pipeline/cd-config.md` |
| `deployment-strategy` | `operation/deployment-pipeline/deployment-strategy.md` |
| `environment-inventory` | `operation/environment-provisioning/environment-inventory.md` |
| `build-test-results` | `construction/build-and-test/test-results.md` + `build-and-test-summary.md` |
| rollback | `operation/deployment-pipeline/rollback-runbook.md` + `docs/edge-fleet-rollback.md` |

## What 4.3 executed (this dispatch)

Local-only / dry-run apply-equivalent from `cd-config` / `deployment-strategy` / `pipeline-validation.md`:

1. Re-ran CD dry-run (constructor isolation, process env flag-off only; does not write live `.env`).
2. Observed live `GET /api/health` and route smoke **without** `launchctl kickstart`.
3. Inventoried SQLite `{storage_root}/edge_fleet.db` read-only (`integrity_check=ok`).
4. Confirmed live `~/.mindroom/.env` mtime unchanged and not written.
5. **Skipped** the documented live restart (`launchctl kickstart -k gui/501/chat.mindroom.local`) because it would recycle the live host (forbidden by this envelope, matching 4.1/4.2).

No GHCR push, no Kubernetes apply, no remote Actions, no `.env` mutation, no Tailscale ACL change, no OpenClaw/Hermes workers, no volume remount.

Evidence dir: `operation/deployment-execution/evidence-2026-08-20-local-safety/`

## Timeline (this dispatch)

| When (UTC) | Event |
|------------|-------|
| 2026-08-20T20:26:11Z | Observed launchd `chat.mindroom.local` already running pid `14783` (not restarted this run) |
| 2026-08-20T20:26:18Z | CD dry-run #1 → `cd-dry-run-ok` / `cd-dry-run-complete` **exit 0** |
| 2026-08-20T20:26:44Z | Live `GET /api/health` HTTP 200; C6 fragment present; SQLite integrity ok; `.env` mtime 2026-08-10T17:27:48.696565+00:00 |
| 2026-08-20T20:27:12Z | Read-only route smoke (no enroll token, no job, no workers) |
| 2026-08-20T20:27:42Z | CD dry-run #2 persisted to evidence dir; **exit 0** |

## Process (observed, not recycled this run)

| | This-run observation |
|--|----------------------|
| launchd | `chat.mindroom.local` running (`active count = 1`) |
| pid | `14783` (started Fri Aug 21 06:21:35 2026 local / elapsed ~05–06 min at observe) |
| listen | python 14783 `*:8765` |
| `edge_fleet.db` open | `True` (pid 14783 holds db + wal + shm) |
| `.env` mtime | `2026-08-10T17:27:48.696565+00:00` (unchanged after smoke) |
| kickstart this run | **not executed** |

Historical note (prior 4.3 artefacts, **not re-executed**): a previous 4.3 run recorded `launchctl kickstart -k gui/501/chat.mindroom.local` pid `35649` → `14783`. This dispatch must not repeat that kickstart.

## CD dry-run (apply-equivalent)

Command:

```sh
PYTHON=/Users/dwayne/mindroom/.venv/bin/python \
  bash scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh
```

Captured 2026-08-20T20:26:18Z and again 2026-08-20T20:27:42Z (evidence `cd-dry-run.log`):

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

Exit code: **0**. Constructor isolation sets `MINDROOM_EDGE_FLEET_ENABLED=false` in **process env only** before importing `mindroom.api.main`. Live `~/.mindroom/.env` was not written.

## Health JSON (observed live; no restart)

```json
{"status":"healthy","last_sync_time":"2026-08-20T20:26:14.824705+00:00","e2ee":{"decrypt_failures":0,"key_requests_sent":0,"notices_sent":0},"edge_fleet":{"enabled":true,"healthy_nodes":0,"status":"Edge fleet is mounted with 0 healthy nodes."}}
```

Quoted `edge_fleet.status`: `Edge fleet is mounted with 0 healthy nodes.`

This is an **observation** of the already-running process. It is **not** a mount performed by this dispatch.

## Live restart skip (required by envelope)

Canonical 4.3 as previously written would run:

```sh
launchctl kickstart -k gui/501/chat.mindroom.local
```

That recycles live `chat.mindroom.local`. **Not executed.** Evidence of skip:

- `did-not-run: launchctl kickstart` in `evidence-2026-08-20-local-safety/smoke.log`
- pid remained `14783` before and after observe/smoke (`evidence-2026-08-20-local-safety/pid.txt`)
- launchd `runs = 8` / `pid = 14783` at 2026-08-20T20:26:11Z; still `14783` at 20:27:42Z

## Rollback path (not executed)

Follow `docs/edge-fleet-rollback.md` / `operation/deployment-pipeline/rollback-runbook.md`:

1. Set `MINDROOM_EDGE_FLEET_ENABLED=false` (or unset) in `/Users/dwayne/.mindroom/.env`.
2. `launchctl kickstart -k gui/501/chat.mindroom.local` — **live restart; out of this envelope**.
3. Confirm `GET /api/health` `edge_fleet.status` quotes `Edge fleet is unmounted; production activation is not live.` and `POST /api/edge-fleet/enroll` is 404.
4. Do not `rm` `/Users/dwayne/.mindroom/mindroom_data/edge_fleet.db` while the process has the DB open.

This run did **not** execute rollback. Flag-off remains available. Dry-run already asserted missing/false flag unmounts (`flag-off-ok`, `flag-false-ok`).

## Explicitly not done

- Live host restart (`launchctl kickstart` / stop / start)
- Live `~/.mindroom/.env` write (mtime remains 2026-08-10T17:27:48.696565+00:00)
- OpenClaw / Hermes `python -m mindroom.edge_node` workers (`pgrep` → `no-edge-node-workers`)
- Tailscale ACL mutation (status-only: self `macbook-pro-3` online `100.80.168.13`)
- AWS / kubectl / terraform
- Remote GitHub Actions dispatch
- Full `tests/` collect (quality minor m2)
- `uv run` (onnxruntime wheel miss)

## Result

- **4.3 status:** DONE (local-safety envelope)
- **Verdict:** PASS
- **CD dry-run:** PASS exit 0 (136-gate not re-run; 3.7 already 136 passed / 3 skipped)
- **Live restart this run:** SKIPPED (documented)
- **Live C6 observation:** fragment present; mounted sentence quoted; pid 14783 unchanged