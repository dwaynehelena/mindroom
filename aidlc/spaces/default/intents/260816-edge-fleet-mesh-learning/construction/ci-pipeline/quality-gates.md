# Quality gates — edge-fleet-mesh-learning

## Merge gate (blocking)

Job: `.github/workflows/edge-fleet-mesh-learning.yml` → `U1–U4 intent suite`.

| Gate | Criteria | Source |
|------|----------|--------|
| Import | `create_edge_fleet_router` and `inspect_openclaw` import | `construction/build-and-test/build-instructions.md`; U1/U2/U4 code-summary |
| Rollback/health docs | `docs/edge-fleet-rollback.md` and `docs/edge-fleet.md` exist | FR6.2 / FR5.2; build-instructions |
| U1∪U2 listed pytest | 88 passed / 3 skipped / 0 failed | build-test-results |
| U3 listed pytest | 15 passed | build-test-results |
| U3 FR3.3/FR3.4 extras | 25 passed (`test_learning_loop`, `test_learning_publishers`, `test_learning_stable_publishers`, `test_learning_candidates`) | build-test-results |
| U4 listed pytest | 8 passed (`surface_absent`) | build-test-results; U4 code-summary |
| Combined intent job | 136 passed, 3 skipped, 0 failed | sum of the rows above |
| Dirty tree | `git diff --exit-code` after tests | same pattern as `pytest.yml` |

Any failed test **blocks** merge. Skips allowed only for the three construction-documented P9 bind-default tests in `tests/test_p9_remediation.py`.

## Non-blocking / not gates

| Item | Why not blocking |
|------|------------------|
| Full `tests/` collect | NFR5 minor m2; 13 optional-extra collect errors |
| `pytest.yml` `uv sync --all-extras` | Same extras; not the intent command set |
| Live Tailscale helper | Quality ran it; not a worker path; no Tailscale on `ubuntu-latest` |
| Live worker-over-Tailscale | Not run in construction or quality |
| Coverage % | NFR5: no new floor |
| HTTP 422 deprecation (`edge_fleet.py:429`) | quality minor m3 |
| asyncio mark on sync allowlist-param test | quality minor m4 |
| Residual API bind `0.0.0.0` | NFR2 minor m1; U1 did not change CLI/orchestrator bind |
| `security-scan.yml` | Advisory (`\|\| true`) |
| Branch-protection required-check checkbox | Repo-admin / Operation; documented here, not flipped in this stage |

## Promotion

NFR6 is local single-user install. Passing this CI means the commit is a **release candidate for local activation**, not an auto-deploy. Production promotion, smoke, and rollback execution belong to Operation `deployment-pipeline` (4.1). Rollback procedure already exists: `docs/edge-fleet-rollback.md` (FR6.1 flag-off + FR6.2 written cleanup).

## Mapping to units

| Unit | code-summary | Commands in the gate |
|------|----------------|----------------------|
| U1 fleet-hygiene | `construction/u1-fleet-hygiene/code-generation/code-summary.md` | fleet/revoke/allowlist/tailnet/lifecycle/security |
| U2 live-fleet | `construction/u2-live-fleet/code-generation/code-summary.md` | live HTTP C3–C6 + health + edge_node |
| U3 learning-promotion | `construction/u3-learning-promotion/code-generation/code-summary.md` | promotion/capture/runtime + loop/publishers |
| U4 handshake | `construction/u4-handshake/code-generation/code-summary.md` | `tests/test_handshake_inspect.py` |

## Residual minors (do not fail CI)

Recorded in `construction/build-and-test/build-and-test-summary.md`:

- m1 NFR2 residual public API bind default
- m2 NFR5 full suite not green
- m3 HTTP 422 deprecation
- m4 asyncio mark on sync test