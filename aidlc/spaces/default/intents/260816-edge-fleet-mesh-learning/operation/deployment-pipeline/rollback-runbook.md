# Rollback runbook — edge-fleet-mesh-learning

**Date:** 2026-08-20  
**Intent:** 260816-edge-fleet-mesh-learning  
**Authority:** FR6.1 flag-off + FR6.2 written cleanup. Product copy: `docs/edge-fleet-rollback.md`.

## When to roll back

- Unexpected enroll, lease, or complete after an incident.
- Allowlist too wide, enrollment key leak, or tailnet check bypass suspicion.
- Operator wants the local install returned to the ship default (unmounted).

Rollback is **not** a second cluster or a previous container image. NFR6 has one local process and one SQLite store. `infrastructure-specification` is absent.

## RTO analogue

Flag-off takes one env edit + process restart. New enroll/lease cannot succeed once routers are unmounted. In-flight complete is unspecified (requirements minor 5); stop workers, then delete the store.

## Procedure (execute)

Follow `docs/edge-fleet-rollback.md` exactly:

1. Set `MINDROOM_EDGE_FLEET_ENABLED=false` (or unset) in the local env file.
2. Restart the MindRoom process.
3. Confirm routers are gone: `POST /api/edge-fleet/enroll` and `POST /api/edge-fleet/lease` are 404. `GET /api/health` `edge_fleet.status` must quote: `Edge fleet is unmounted; production activation is not live.`
4. Stop operator-started OpenClaw / Hermes `EdgeNodeClient` processes. MindRoom does not supervise them.
5. With the process stopped, inspect then remove `{storage_root}/edge_fleet.db` (or `MINDROOM_EDGE_FLEET_PATH`) and WAL/SHM siblings.

Optional while still mounted: `DELETE /api/edge-fleet-admin/nodes/{node_id}` tombstones a worker (U1 / C1) and writes `edge_fleet_audit` `node.revoked`.

## Procedure (4.1 dry-run — this stage)

Do **not** edit the live `.env` or restart the operator's running instance from this stage. Evidence that rollback remains available:

- Import path and flag-off constructor: missing/false flag → `_edge_fleet_from_runtime_paths` returns `None`.
- Docs exist: `docs/edge-fleet-rollback.md`, `docs/edge-fleet.md`.
- CI still enforces those docs (`ci-config` / `quality-gates` / `cicd-pipeline` `test -f` checks).

## Residual state after flag-off

| Surface | After FR6.1 |
|---------|-------------|
| New enroll / new lease | Refused (unmounted) |
| In-flight complete / heartbeats | Unspecified — stop workers, delete store |
| Learning promotion (U3) | Unaffected HTTP-wise (no fleet routes). OpenClaw `learning:` stays false |
| Handshake (U4) | Still `surface_absent`; nothing to unmount |

## Evidence to collect

- Process log: `Edge fleet is disabled — no routes mounted` (or missing-key warning).
- Health sentence quoted above.
- If revoke happened while mounted: `edge_fleet_audit` event `node.revoked`.

## What not to do

- Do not roll back by redeploying GHCR/k8s for this intent.
- Do not reintroduce `MINDROOM_MESH_ENROLLMENT`.
- Do not `rm` the store while the process still has the DB open.
