# SLO config — edge-fleet-mesh-learning (4.4)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** observability-setup (4.4)
**Envelope:** local fail-closed SLIs. No CloudWatch SLO, no error-budget burn-rate alarms.

## Purpose

Record the **actual** service-level intent for this brownfield local install. Canonical AWS SLO tracking is skipped (NFR6; `performance-design` / `reliability-design` / `infrastructure-specification` absent).

## SLO analogue (from quality, not invented nines)

`construction/build-and-test/nfr-validation-matrix.md`:

> Fail-closed is the SLO analogue (NFR1), not a multi-nines availability target.

No k6/locust performance NFR exists. 4.6 performance-validation remains later and is **not** started here.

## SLIs (operator-measurable)

| ID | User-facing question (NFR4) | Indicator | Target |
|----|-----------------------------|-----------|--------|
| SLI-MOUNT | Is the fleet mounted? | `GET /api/health` `edge_fleet.enabled` + quoted `status` sentence (C6) | When flags on and process current: `enabled=true` and mounted sentence. When flags off: unmounted sentence. |
| SLI-HEALTH | Is the process serving health? | HTTP 200 and top-level `status=healthy` | 200 / healthy on observe |
| SLI-ALLOW | Was enroll denied by allowlist? | Error string `edge node is not on the enrollment allowlist` | Fail-closed: deny when not listed or allowlist `None` |
| SLI-TAILNET | Did tailnet check fail? | Audit/log `tailnet.refused` | Fail-closed: refuse enroll/heartbeat/lease/complete |
| SLI-REVOKE | Did revoke succeed? | SQLite `edge_fleet_audit` row `event=node.revoked` and/or log `admin.node_revoked` | Row present after a successful revoke |
| SLI-IDLE | Are zero nodes expected? | `healthy_nodes` vs `pgrep mindroom.edge_node` | 0 and no workers = idle success |

## What is not an SLO

- 99.9% / 99.5% availability over 30 days
- p99 latency 200 ms
- Error budget policy / burn-rate 14x
- SLA with customer consequences (single operator, local install)

## Error budget

Not defined. Shipping risk is controlled by **flag-off rollback** (`docs/edge-fleet-rollback.md`, `operation/deployment-pipeline/rollback-runbook.md`), not by consuming a percentage of failed requests.

A future operator-authorized live worker path would still not create a CloudWatch error budget under NFR6.

## This-run measurement (not a 30-day window)

UTC `2026-08-20T20:33:05Z`:

| SLI | Observed |
|-----|----------|
| SLI-MOUNT | `enabled=true`, sentence `Edge fleet is mounted with 0 healthy nodes.` |
| SLI-HEALTH | HTTP 200, `status=healthy`, pid 14783 |
| SLI-ALLOW | not exercised (no enroll) |
| SLI-TAILNET | not exercised (no worker ops) |
| SLI-REVOKE | audit count 0 (no revoke this run) |
| SLI-IDLE | `healthy_nodes=0`, `no-edge-node-workers` |

Evidence: `evidence-2026-08-20-local-safety/health.json`, `workers.txt`, `sqlite-inventory.txt`.

## CloudWatch SLO skip

- No `AWS/ApplicationELB` or Lambda SLI math
- No burn-rate composite alarm
- `did-not-run: aws cloudwatch put-metric-alarm`
