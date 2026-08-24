# Unit test instructions — U2 live-fleet

From the MindRoom repo root:

```sh
cd /Users/dwayne/mindroom
uv run pytest \
  tests/api/test_edge_fleet_live.py \
  tests/test_edge_node.py \
  tests/api/test_edge_fleet_api.py \
  tests/api/test_edge_fleet_lifecycle.py \
  tests/api/test_edge_fleet_revocation_api.py \
  tests/api/test_edge_fleet_revocation_core.py \
  tests/api/test_edge_fleet_security.py \
  tests/test_edge_fleet.py \
  tests/test_edge_tailscale.py \
  tests/test_p9_remediation.py \
  tests/api/test_api.py::test_health_check
```

## Acceptance mapped to tests

| Check | Test |
|-------|------|
| C3 issue-enrollment 200 + Cache-Control no-store | `test_c3_issue_enrollment_requires_auth_and_returns_no_store_token` |
| C3 401 unsigned | same |
| C4 enqueue 201 + inspect 200 | `test_c4_enqueue_compatible_job_and_inspect` |
| C4 409 equivocation / 422 invalid | `test_c4_equivocation_is_409_and_invalid_payload_is_422` |
| C5 OpenClaw and Hermes enroll → lease → complete 204 | `test_c5_openclaw_and_hermes_enroll_take_and_finish` |
| C6 health fragment + sentence | `test_c6_*` |
| FR2.1 start command both runtimes | `test_operator_start_command_documents_both_runtimes` |
| C2 consumer (tombstone/allowlist/tailnet) | U1 slice tests still green |

Do not require a live Tailscale or a real OpenClaw/Hermes binary for this slice. Finish is attested complete `204`, not process exit.