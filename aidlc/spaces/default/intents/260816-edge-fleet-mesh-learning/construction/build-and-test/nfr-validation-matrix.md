# NFR validation matrix — edge-fleet-mesh-learning

Date: 2026-08-20. Targets from `inception/requirements-analysis/requirements.md`. Methods from quality-agent `nfr-validation-methods.md` / `nfr-reliability-guide.md`, scaled to local install (NFR6) and Minimal test strategy.

| NFR | Target | Actual | Status | Evidence | Notes |
|-----|--------|--------|--------|----------|-------|
| NFR1 | Fail-closed: missing flag, missing/short enrollment key, missing allowlist, failed tailnet, revoked worker → no successful enroll/heartbeat/lease/complete | Flag-off unmounts routers; short/missing key unmounts; `node_allowlist is None` denies enroll; `require_tailscale` raises; revoked node tombstoned | **PASS** | `tests/api/test_edge_fleet_lifecycle.py`; `tests/api/test_edge_fleet_api.py::test_missing_allowlist_denies_enroll_at_the_store`; `tests/test_edge_tailscale.py`; `tests/api/test_edge_fleet_revocation_api.py`; `src/mindroom/api/main.py` `_edge_fleet_from_runtime_paths`; `src/mindroom/api/edge_fleet.py` `_require_tailnet` | Live helper also connected (see NFR1 live row) |
| NFR1 live helper | Tailnet check fails the op, not log-and-continue (FR1.4) | `check_tailscale_connectivity()` → `connected`; `require_tailscale()` succeeded | **PASS (helper)** | Ran `mindroom.edge_tailscale` against local daemon: node `macbook-pro-3.tail9e4b21.ts.net`, IP `100.80.168.13`, 2 online peers | **Not** a live worker enroll/lease/complete over Tailscale. Construction C5 tests remain in-process HTTP. |
| NFR2 | No fleet or handshake listener exposed on a public network | No new fleet/handshake bind. Unread mesh switch removed. Node ops refuse without tailnet. Existing MindRoom API still defaults `--api-host 0.0.0.0` | **PASS-WITH-MINOR** | Kickoff checkable rule: no *new* public bind + FR1.3 refuse. `src/mindroom/handshake_inspect.py`; skipped P9 bind tests in `tests/test_p9_remediation.py`. Residual: `src/mindroom/cli/main.py:130`, `src/mindroom/orchestrator.py:2552`, `src/mindroom/api/main.py:1224` still default `0.0.0.0` | Matches requirements review finding 7 (minor). U1 explicitly did not change CLI/orchestrator bind. |
| NFR3 | Existing enrollment-token + per-request attestation; no new credential type | HMAC `EnrollmentAuthority` + Ed25519 node/result attestation unchanged | **PASS** | `src/mindroom/edge_fleet.py` `EnrollmentAuthority` / `_public_key`; `tests/api/test_edge_fleet_security.py` (tamper, wrong key, nonce, clock skew) | No new auth scheme in U1–U4 |
| NFR4 | Operator can tell from logs or health: fleet mounted, allowlist deny, tailnet fail, revoke success | Health `edge_fleet` fragment + activation sentence; `edge_fleet_audit` `node.revoked`; allowlist error string; `tailnet.refused` audit | **PASS** | `tests/api/test_edge_fleet_live.py` `test_c6_*`; `tests/api/test_edge_fleet_revocation_api.py` `test_ac5_audit_trail_present`; `src/mindroom/api/edge_fleet.py` `_require_tailnet`; `docs/edge-fleet-rollback.md` §5 | |
| NFR5 | Existing test suite remains green; no new coverage floor | Intent-scoped U1–U4 commands green after leftover fix. Full suite **not** executed to completion | **PASS-WITH-MINOR** | See `test-results.md`. Full collect-only: 13 remaining errors in optional extras (Gmail/OAuth/livekit/crawl4ai/defusedxml/claude-agent), not U1–U4 | Quality decided not to claim full-suite verification |
| NFR6 | Local single-user install only | No new cloud integration, no public REST for learning/mesh, operators are local HTTP / in-process | **PASS** | Contract C7/C8 `no_http`; construction summaries; live Tailscale is the local daemon | |

## Performance / load

No quantitative latency/throughput NFRs exist. Load tests (k6/locust) are **not required** at Minimal strategy for this local-install intent. Performance-validation remains Operation stage 4.6.

## Reliability snapshot

- Fail-closed is the SLO analogue (NFR1), not a multi-nines availability target.
- Rollback: FR6.1 flag-off + FR6.2 written procedure (`docs/edge-fleet-rollback.md`).
- Handshake FR4.2 not implemented (`surface_absent`); residual mesh surface is absent, unread switch removed.