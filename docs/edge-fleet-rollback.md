# Edge fleet rollback (FR6.2)

Local-install cleanup after the fleet capability is turned off.

Slice 1 ships **flag-off** (FR6.1): missing `MINDROOM_EDGE_FLEET_ENABLED`, a false flag, or a missing/short enrollment key keeps `/api/edge-fleet` and `/api/edge-fleet-admin` unmounted. New enroll and new lease therefore cannot succeed.

This procedure is the written cleanup for workers, leases, and persisted records that already exist.

## 1. Turn the capability off

1. Set `MINDROOM_EDGE_FLEET_ENABLED=false` (or unset it) in the local env file.
2. Restart the MindRoom process.
3. Confirm routers are gone: `POST /api/edge-fleet/enroll` and `POST /api/edge-fleet/lease` are not mounted (404). `GET /api/health` `edge_fleet.status` must quote: `Edge fleet is unmounted; production activation is not live.`

## 2. Stop operator-started workers

MindRoom does not supervise workers. Stop any operator-started OpenClaw or Hermes `EdgeNodeClient` processes yourself.

## 3. Clean persisted records

Default store path: `{storage_root}/edge_fleet.db` unless `MINDROOM_EDGE_FLEET_PATH` points elsewhere.

With the process stopped:

```sh
# Inspect (optional)
sqlite3 "$MINDROOM_EDGE_FLEET_PATH" "SELECT node_id, runtime, revoked_at FROM edge_node;"
sqlite3 "$MINDROOM_EDGE_FLEET_PATH" "SELECT job_id, status, node_id, lease_id FROM edge_job;"

# Tombstone remaining workers if you need an audit trail before delete
# (requires the fleet mounted — do this *before* flag-off if possible)
# DELETE /api/edge-fleet-admin/nodes/{node_id}

# Remove the store after flag-off
rm -f "$MINDROOM_EDGE_FLEET_PATH" "${MINDROOM_EDGE_FLEET_PATH}-wal" "${MINDROOM_EDGE_FLEET_PATH}-shm"
```

If `MINDROOM_EDGE_FLEET_PATH` is unset, delete `{storage_root}/edge_fleet.db` and its WAL/SHM siblings.

## 4. Residual state after flag-off

| Surface | After FR6.1 flag-off |
|---------|----------------------|
| New enroll | Refused (routers unmounted) |
| New lease | Refused (routers unmounted) |
| In-flight complete | Unspecified in requirements; do not rely on it. Stop workers, then delete the store. |
| Heartbeats | Unspecified; stop workers. |
| Allowlist / enrollment key | Leave unset or rotate; a short key also keeps the fleet unmounted. |

## 5. Evidence

- Flag-off: process logs `Edge fleet is disabled — no routes mounted` (or the missing-key warning).
- Revoke (while still mounted): `edge_fleet_audit` event `node.revoked`.
- Allowlist deny (while mounted): enroll raises `edge node is not on the enrollment allowlist`.