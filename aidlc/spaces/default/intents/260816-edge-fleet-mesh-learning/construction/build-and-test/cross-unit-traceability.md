# Cross-unit traceability — build-and-test

**Verdict:** PASS (FR/NFR IDs covered or N/A/Deferred with justification).  
No `construction/*/code-generation/traceability.json` files exist; coverage is joined from `inception/units-generation/traceability.json`, per-unit `code-summary.md` / `unit-test-instructions.md`, and tests executed this run. No `stories.md` (user-stories skipped). Do not invent `USx.y`.

## FR coverage

| ID | Owner | Status | Target / evidence |
|----|-------|--------|-------------------|
| FR1 | U1 | OK | C1+C2; revocation + store tests |
| FR1.1 | U1 | OK | `tests/api/test_edge_fleet_revocation_api.py` signed-in 204/404, no 403 |
| FR1.2 | U1 | OK | tombstone + heartbeat/lease refuse; `test_edge_fleet_revocation_core.py` requeue |
| FR1.3 | U1 | OK | `_require_tailnet` on enroll/heartbeat/lease/complete; `tests/test_edge_tailscale.py` |
| FR1.4 | U1 | OK | `require_tailscale` raises `TailscaleUnavailableError`; live helper also succeeded |
| FR1.5 | U1 | OK | `test_missing_allowlist_denies_enroll_at_the_store` |
| FR1.6 | U1 | OK | `test_enrollment_http_surface_has_no_allowlist_parameter` |
| FR1.7 | U1 | OK | `edge_fleet_cross_device_demo.py` reads `MINDROOM_API_KEY`; leftover bearer rotated out |
| FR2 | U2 | OK | C3+C4+C5; `tests/api/test_edge_fleet_live.py` |
| FR2.1 | U2 | OK | `test_operator_start_command_documents_both_runtimes` / `python -m mindroom.edge_node` |
| FR2.2 | U2 | OK | in-process OpenClaw path `test_c5_openclaw_and_hermes_enroll_take_and_finish` |
| FR2.3 | U2 | OK | same test, Hermes branch |
| FR2.4 | U2 | OK | both runtimes in one test |
| FR2.5 | U2 | OK | MindRoom does not start workers; documented operator command |
| FR3 | U3 | OK | C7 |
| FR3.1 | U3 | OK | `tests/test_learning_promotion.py` visible reply → FlightRecorder → `proposed` |
| FR3.2 | U3 | OK | `test_c7_demo_script_only_origin_yields_no_candidate` |
| FR3.3 | U3 | OK | restored `learning_loop.canary` both roots; `tests/test_learning_loop.py::test_two_runtime_canary_then_stable` |
| FR3.4 | U3 | OK | `stabilize` requires dual-root canary + non-blank reviewer/reason; loop + publishers tests |
| FR3.5 | U3 | OK | `test_fr35_openclaw_agno_learning_stays_off`; `config.yaml` `learning: false` |
| FR3.6 | U3 | N/A | process note |
| FR4 | U4 | OK | C8 inspect-or-remove |
| FR4.1 | U4 | OK | `src/mindroom/handshake_inspect.py`; `tests/test_handshake_inspect.py` |
| FR4.2 | U4 | N/A | `surface_absent`; FR4.2 not implemented (closed alternative) |
| FR4.3 | U4 | OK | deferred; tests assert FR4.2 false |
| FR4.4 | U4 | N/A | bind branch not taken |
| FR4.5 | U4 | OK | `test_c8_fr45_unread_switch_is_removed_in_this_tree`; env/config/source unread switch gone |
| FR5 | U2 | OK | C6 |
| FR5.1 | U2 | OK | `GET /api/health` `edge_fleet` fragment + one sentence |
| FR5.2 | U2 | OK | `docs/edge-fleet.md` / rollback doc quote health |
| FR6 | U1 | OK | flag-off + procedure |
| FR6.1 | U1 | OK | `tests/api/test_edge_fleet_lifecycle.py` |
| FR6.2 | U1 | OK | `docs/edge-fleet-rollback.md` (procedure, not a payload) |

## NFR coverage

| ID | Status | Owning evidence |
|----|--------|-----------------|
| NFR1 | OK | lifecycle + allowlist + tailnet + revoke |
| NFR2 | OK with residual minor | no new fleet/handshake bind; existing API `0.0.0.0` default unchanged |
| NFR3 | OK | HMAC + Ed25519; security tests |
| NFR4 | OK | health + `edge_fleet_audit` + `tailnet.refused` |
| NFR5 | OK with residual minor | intent-scoped green; full suite not verified |
| NFR6 | OK | local install only |

## Uncovered

None of the enumerated FR/NFR IDs lack an owner or justification.