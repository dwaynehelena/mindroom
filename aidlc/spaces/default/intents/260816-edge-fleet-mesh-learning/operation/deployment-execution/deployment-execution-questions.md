# Deployment execution questions — edge-fleet-mesh-learning

Standing approvals from `@dwayne:localhost` are in force. Answers are taken from NFR6, `cd-config`, `deployment-strategy`, `environment-inventory`, `rollback-runbook`, and live host observations. No new human Q&A turn. This dispatch uses the 4.1/4.2 live-safety envelope: no live host restart.

## Q1 — Pre-deployment checks passing?

**Question:** Are all pre-deployment checks passing?

**Answer:** Yes for the local-activation / dry-run path. 4.1 dry-run PASS. 4.2 verdict PASS. This-run CD dry-run `PYTHON=/Users/dwayne/mindroom/.venv/bin/python bash scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh` → `cd-dry-run-ok` / `cd-dry-run-complete` **exit 0** (2026-08-20T20:26:18Z and 20:27:42Z). Live `GET /api/health` is `healthy` with C6 fragment present (observed; process not recycled). SQLite `{storage_root}/edge_fleet.db` `integrity_check=ok`. Construction quality PASS-WITH-MINORS; ci-pipeline 3.7 PASS (136 passed / 3 skipped, not re-run). Workspace is an editable install (`mindroom-2026.8.79`). Live `.env` already contains enable + enrollment key + allowlist (not printed, not written; mtime `2026-08-10T17:27:48.696565+00:00`).

## Q2 — Database migrations required and tested?

**Question:** Are database migrations required and tested?

**Answer:** No separate migration job. C2 store is SQLite `edge_fleet.db`. Schema is created/opened by `EdgeFleet.open()` when the process mounts the fleet. 4.2 inventoried the existing file (integrity ok). This 4.3 run re-checked integrity (`ok`, wal, tables present) **without** restarting the process and **without** `rm` of the store. Do not `rm` the store while a process has it open (`rollback-runbook.md`).

## Q3 — Dependent services available and healthy?

**Question:** Are dependent services available and healthy?

**Answer:** The only required dependency is the local MindRoom process + config-adjacent `.env` + `{storage_root}/edge_fleet.db`. Process observed healthy (pid 14783, listen `*:8765`). Tailscale/OpenClaw/Hermes gateways were inventoried in 4.2 and are **not** 4.3 deploy targets. AWS/Secrets Manager/k8s are NFR6 out of scope and were not provisioned.

## Q4 — Deployment window?

**Question:** What is the deployment window?

**Answer:** Immediate local window on 2026-08-20 under standing approvals, **constrained** to the 4.1/4.2 live-safety envelope. Canonical strategy is feature-flag activation + process restart (`deployment-strategy.md`). **This run did not restart** `chat.mindroom.local` because kickstart would recycle the live host. Apply-equivalent executed: CD dry-run + read-only health/smoke. Rollback remains `docs/edge-fleet-rollback.md` (flag-off + restart; do not execute unless after-health fails; restart itself is out of envelope).

## Assumptions & Open Questions

None that block 4.3 under the local-safety envelope. Live worker-over-Tailscale and a further live host restart remain out of this stage.

## Positions

None.