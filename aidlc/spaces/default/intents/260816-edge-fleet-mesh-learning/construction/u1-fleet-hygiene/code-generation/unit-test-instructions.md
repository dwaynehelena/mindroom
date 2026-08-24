# Unit test instructions — U1 fleet-hygiene

From the MindRoom repo root:

```sh
cd /Users/dwayne/mindroom
uv run pytest \
  tests/test_edge_fleet.py \
  tests/test_edge_node.py \
  tests/test_edge_tailscale.py \
  tests/test_p9_remediation.py \
  tests/api/test_edge_fleet_api.py \
  tests/api/test_edge_fleet_lifecycle.py \
  tests/api/test_edge_fleet_revocation_api.py \
  tests/api/test_edge_fleet_revocation_core.py \
  tests/api/test_edge_fleet_security.py
```

## Acceptance mapped to tests

| Check | Test |
|-------|------|
| Signed-in operator, no permissions field → 204 | `test_ac6_signed_in_operator_without_permissions_revokes` |
| 404 never existed | `test_ac2_404_for_never_existed_node` |
| Idempotent 204 | `test_ac3_repeat_delete_is_idempotent_204` |
| 401 unauthenticated | `test_ac6_unauthenticated_request_is_rejected` |
| Tombstone + heartbeat 401 | `test_ac4_lease_and_heartbeat_invalidated_after_revoke` |
| Requeue leased jobs | `tests/api/test_edge_fleet_revocation_core.py` |
| Allowlist None denies enroll (store) | `test_missing_allowlist_denies_enroll_at_the_store`, `test_denied_node_enrollment_is_rejected` |
| HTTP allowlist param gone | `test_enrollment_http_surface_has_no_allowlist_parameter` |
| Tailnet refuse on node ops | `test_enroll_refuses_when_tailnet_check_fails`, `tests/test_edge_tailscale.py` |
| Flag-off / short key unmount | `tests/api/test_edge_fleet_lifecycle.py` |

Do not require an OpenClaw/Hermes worker to finish a job for slice 1.