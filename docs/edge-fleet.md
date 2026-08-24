---
icon: lucide/network
---

# Edge Fleet (local install)

Operator path for issuing an enrollment token, enqueueing a compatible job, and starting an OpenClaw or Hermes worker against the local MindRoom API.

MindRoom does **not** start or supervise workers. Finish is **not** process exit. Finish is the fleet accepting that worker's attested `POST /api/edge-fleet/complete` (`204`).

## Activation status

`GET /api/health` is the source of truth (FR5.1 / C6). Quote the `edge_fleet.status` sentence. Do not treat older portfolio or security-architecture sentences as authority.

Unmounted (flag off, missing/short enrollment key):

> Edge fleet is unmounted; production activation is not live.

Mounted:

> Edge fleet is mounted with {n} healthy node(s).

Mounted but the store probe failed:

> Edge fleet is mounted but health is unavailable.

HTTP shape:

- unmounted: `{ "enabled": false, "status": "..." }`
- mounted: `{ "enabled": true, "healthy_nodes": <int>, "status": "..." }`
- mounted error: `{ "enabled": true, "error": "...", "status": "..." }`


## Local activation (CD)

NFR6 is a local single-user install. Passing `.github/workflows/edge-fleet-mesh-learning.yml` makes a commit a **release candidate**, not a live mount. Default remains unmounted.

To mount on this machine (manual; not auto-deploy):

1. Confirm the intent CI (or local `scripts/testing/run_edge_fleet_mesh_learning_ci.sh`) is green.
2. In the config-adjacent `.env`, set `MINDROOM_EDGE_FLEET_ENABLED=true`, a ≥32-byte URL-safe Base64 `MINDROOM_EDGE_FLEET_ENROLLMENT_KEY`, and `MINDROOM_EDGE_FLEET_NODE_ALLOWLIST` to the node ids that may enroll.
3. Restart MindRoom.
4. Quote `GET /api/health` `edge_fleet.status`. Unmounted sentence: `Edge fleet is unmounted; production activation is not live.`

Dry-run the CD path without touching live env: `scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh`.

Do not set `MINDROOM_MESH_ENROLLMENT` (C8 `surface_absent`). Keep the OpenClaw-named agent `learning: false`.

## Issue enrollment (C3)

Signed-in operator:

```http
POST /api/edge-fleet-admin/enrollments
```

JSON body: `node_id`, `runtime` (`openclaw` or `hermes`), `public_key` (Ed25519), `capabilities` (one or more strings), optional `expires_in_seconds` (1–3600, default 600).

Success: `200` with `token` and `expires_at`, `Cache-Control: no-store`. The token is HMAC `mindroom.edge-enrollment/1`. No new credential type.

## Enqueue a compatible job (C4)

Signed-in operator (preferred):

```http
POST /api/edge-fleet-admin/jobs
```

JSON body: `job_id`, `runtime` (`openclaw` or `hermes`), `required_capabilities`, `payload`.

Success: `201` queued. Inspect with `GET /api/edge-fleet-admin/jobs/{job_id}`. Compatible means the runtime matches the admitted worker and `required_capabilities` are a subset of that node's capabilities.

A documented in-process command may call `EdgeFleet.queue_job` with the same store contract.

## Start a worker (FR2.1, FR2.5)

Use the packaged `EdgeNodeClient` command. Same command, different `--runtime`. MindRoom does not spawn these processes.

OpenClaw:

```bash
python -m mindroom.edge_node \
  --base-url http://127.0.0.1:8765 \
  --identity ~/.mindroom/edge-nodes/openclaw.json \
  --node-id openclaw-node-1 \
  --runtime openclaw \
  --capability notify \
  --token "$MINDROOM_EDGE_ENROLLMENT_TOKEN" \
  --once
```

Hermes:

```bash
python -m mindroom.edge_node \
  --base-url http://127.0.0.1:8765 \
  --identity ~/.mindroom/edge-nodes/hermes.json \
  --node-id hermes-node-1 \
  --runtime hermes \
  --capability research \
  --token "$MINDROOM_EDGE_ENROLLMENT_TOKEN" \
  --once
```

`--once` enrolls (when `--token` is set), heartbeats, takes at most one job, and exits `0` only after attested complete is accepted (`204`). Exit `2` means no compatible queued job. Optional `-- <argv>` runs a stdin/stdout JSON worker instead of echoing the payload.

Workers must reach the fleet on loopback HTTP or HTTPS. Tailnet fail-closed still applies on enroll, heartbeat, lease, and complete.

## Rollback

See [Edge fleet rollback](edge-fleet-rollback.md).