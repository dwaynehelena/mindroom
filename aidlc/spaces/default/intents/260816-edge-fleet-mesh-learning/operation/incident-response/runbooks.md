# Runbooks - edge-fleet-mesh-learning (4.5)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** incident-response (4.5)
**Envelope:** local tabletop / documentation. No SSM Automation documents. No live host restart. No paging.

## Purpose

Give the single local operator a library of **manual** runbooks that consume 4.4 `dashboards.md` and `alarms.md`. Canonical 4.5 SSM Automation (ECS restart, RDS failover, Lambda redeploy) is **not** created: NFR6 is local-install only, Construction skipped `nfr-design` and `infrastructure-design`, and `infrastructure-specification` is absent. `reliability-design` and `security-design` are likewise absent; fail-closed (NFR1) and NFR2/NFR3 from `inception/requirements-analysis/requirements.md` plus `construction/build-and-test/nfr-validation-matrix.md` stand in.

Product procedures already written (do not duplicate as AWS docs):

- `docs/edge-fleet.md` (C6 quote, local activation, worker start)
- `docs/edge-fleet-rollback.md` (FR6.1 flag-off + FR6.2 cleanup)
- `operation/deployment-pipeline/rollback-runbook.md` (4.1 dry-run residual)

## Upstream (consumed; several absent by design)

| Artifact | Path / status | How these runbooks use it |
|----------|---------------|---------------------------|
| `dashboards` | `operation/observability-setup/dashboards.md` | Panel 1 health quote; Panel 2 process/saturation; Panel 4 log tail |
| `alarms` | `operation/observability-setup/alarms.md` | A1-A8 map to FM1-FM9; A7 is idle not an incident |
| `reliability-design` | **absent** - `construction/nfr-design/` skipped | Fail-closed SLO analogue from `nfr-validation-matrix.md` / 4.4 `slo-config.md` |
| `security-design` | **absent** - same skip | Allowlist / tailnet / revoke are the security response, not WAF/IAM |
| `infrastructure-specification` | **absent** - `construction/infrastructure-design/` skipped | No AWS account, no SSM document ARN |

## RB-DETECT - observe-only detection (executed this run)

When: any A1-A8 symptom, or before claiming idle.

Do **not** `launchctl kickstart`. Do **not** write `~/.mindroom/.env`. Do **not** start `python -m mindroom.edge_node`.

Steps (read-only):

1. Quote `GET http://127.0.0.1:8765/api/health` (dashboards Panel 1 / C6).
2. Confirm pid via `lsof -nP -iTCP:8765 -sTCP:LISTEN` and `ps -p <pid>`.
3. `launchctl print gui/501/chat.mindroom.local` excerpt only (state, pid, runs). Not kickstart.
4. `pgrep -lf mindroom.edge_node` (A7 idle if empty and `healthy_nodes=0`).
5. SQLite URI `mode=ro` `PRAGMA integrity_check` on `{storage_root}/edge_fleet.db`.
6. Log queries from `operation/observability-setup/log-queries.md` (Q-MOUNT / Q-ALLOW / Q-TAILNET / Q-REVOKE).

This-run result (UTC `2026-08-20T20:41:16.658675+00:00`):

- HTTP **200**, `status=healthy`
- Quoted `edge_fleet.status`: `Edge fleet is mounted with 0 healthy nodes.`
- Pid **14783** unchanged from 4.3/4.4; launchd `runs=8`, `state=running`
- Workers: none (`no-edge-node-workers`)
- SQLite integrity `ok`, journal `wal`, `edge_fleet_audit=0`
- `.env` mtime still `2026-08-10T17:27:48.696565+00:00`
- A7 idle **expected**. Do not page.

Evidence: `evidence-2026-08-20-local-safety/observe.log`, `health.json`, `pid.txt`.

## RB-ROLLBACK - FR6 flag-off (documented, not executed)

When: FM6 unexpected worker traffic, FM7 key leak / allowlist too wide, or operator wants ship-default unmount.

Follow `docs/edge-fleet-rollback.md` and `operation/deployment-pipeline/rollback-runbook.md` **exactly** in an authorized later window:

1. Set `MINDROOM_EDGE_FLEET_ENABLED=false` (or unset) in the local env file.
2. Restart the MindRoom process.
3. Confirm routers gone: enroll/lease 404. Quote unmounted sentence: `Edge fleet is unmounted; production activation is not live.`
4. Stop operator-started OpenClaw / Hermes `EdgeNodeClient` processes. MindRoom does not supervise them.
5. With the process stopped, inspect then remove `{storage_root}/edge_fleet.db` and WAL/SHM siblings.

**This run did not execute steps 1-5.** They would write `.env` and recycle pid 14783.

Skip evidence: `evidence-2026-08-20-local-safety/cloud-skip.txt` (`did-not-run: write ~/.mindroom/.env`, `did-not-run: launchctl kickstart`, `did-not-run: rm edge_fleet.db`).

RTO analogue: one env edit + restart. Not executed.

## RB-REVOKE - tombstone a node while still mounted (not executed)

When: a specific enrolled worker must be refused (FR1.1 / FR1.2) without full flag-off.

1. Signed-in operator: `DELETE /api/edge-fleet-admin/nodes/{node_id}`.
2. Confirm SQLite `edge_fleet_audit.event = node.revoked` (4.4 Q-REVOKE / A6).
3. Confirm subsequent enroll / heartbeat / lease / complete for that node fail.

**This run did not call DELETE.** `edge_fleet_audit` count remains **0**. No live worker to revoke.

## RB-WORKER-STOP - stop operator-started workers (N/A this run)

MindRoom does not supervise workers (FR2.5). If `pgrep -lf mindroom.edge_node` is non-empty, stop those PIDs, then re-quote health. This run: empty. Do **not** start a worker to exercise this book.

## RB-IDLE-CONFIRM - A7 is not an incident (executed)

If `healthy_nodes=0` **and** no `mindroom.edge_node` processes: record mounted-idle and stop. Do not page. Do not kickstart. Do not enqueue jobs. This-run: executed; A7 expected.

## SSM Automation skip

Canonical stage Step 4 would store SSM Automation documents and wire them to alarm actions.

- `did-not-run: aws ssm create-document`
- `did-not-run: aws ssm send-command`
- `did-not-run: aws ssm start-automation-execution`
- `which aws` = not-found (not invoked)

No document name, no Automation ARN.

## Explicitly not done

- Live host restart / `launchctl kickstart`
- Live `~/.mindroom/.env` write
- Starting OpenClaw/Hermes workers
- `DELETE` revoke
- `rm` of the live store (pid 14783 holds db+wal+shm)
- AWS / kubectl / remote Actions / SNS page
