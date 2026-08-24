# Code Quality Assessment — Activation Surfaces

This assessment is limited to the files in the partial scan.
No coverage percentage is claimed; coverage was not measured in this run.
Tests listed below exist on disk; they were not re-executed here.

## Test Coverage (scanned files)

Test directories: `tests/`, `tests/api/`.
Framework: pytest, `asyncio_mode` auto/strict, xdist, 60s timeout.
CI: `.github/workflows/pytest.yml` runs `uv run pytest -m "not requires_matrix"` on Python 3.13 after `uv sync --all-extras`.

Scanned test modules and what they actually exercise:

| File | What it covers |
|------|----------------|
| `tests/test_edge_fleet.py` | enroll, lease, attestation, expiry, nonce replay |
| `tests/test_edge_node.py` | identity file and client (file present in scan) |
| `tests/test_p9_remediation.py` | P9 exposure remediation checks |
| `tests/api/test_edge_fleet_api.py` | HTTP enroll/heartbeat/lease/admin issue |
| `tests/api/test_edge_fleet_lifecycle.py` | flag+key mount gates |
| `tests/api/test_edge_fleet_security.py` | HTTP security cases (large file) |
| `tests/api/test_edge_fleet_revocation_core.py` | store tombstone, lease cancel, re-enroll deny |
| `tests/api/test_edge_fleet_revocation_api.py` | DELETE path including fail-closed missing `permissions` |
| `tests/test_mesh_enrollment.py` | Phase A local admit (424 lines) |
| `tests/test_mesh_enrollment_inverted.py` | shared-authority inverted claims (293 lines) |
| `tests/test_learning_loop.py` | propose → review → canary → stable and rollback |
| `tests/test_learning_runtime.py` | Matrix review helper |
| `tests/test_learning_capture.py` | Flight Recorder gate |
| `tests/test_learning_candidates.py` | skill/memory producers |
| `tests/test_learning_publishers.py` | canary JSON |
| `tests/test_learning_stable_publishers.py` | SKILL.md install |

**FACT:** revocation API tests inject a user dict that sometimes includes `permissions`.
`test_ac6_fail_closed_when_permissions_key_absent` documents that real `verify_user` output is always 403.

**FACT:** live scripts (`mesh_phaseb_unit1_live_smoke.py`, `edge_fleet_cross_device_demo.py`, `scripts/learning_stable_demo.py`) are not in the default pytest selection.

Gaps relative to the approved scope (not missing unit tests for the store):

- No test that a mounted production app with real `verify_user` can revoke (it cannot).
- No test that fleet HTTP handlers call Tailscale (they do not).
- No test that orchestrator constructs `MeshGateway` (it does not).
- No test that a live agent turn calls `record_flight_event` (nothing does).

## Linting and Types

- Ruff: `select = ["ALL"]` with a documented ignore list in `pyproject.toml`.
- ty: `error-on-warning = true` on `src` and `tests`.
- vulture: `src/mindroom` plus `vulture_whitelist.py`; `enrollment_flag_enabled` is explicitly whitelisted as test-facing.

Scanned production modules follow the repo style: module docstrings, frozen dataclasses, explicit errors, few imports inside functions.
`learning_runtime.request_runtime_learning_review` defers `approval_manager` with `# noqa: PLC0415`.

## Documentation Quality

Docs in the scan are detailed and often ahead of or beside the code.

| Document | Claim | Code |
|----------|-------|------|
| `docs/dev/portfolio-register.md` | P9 **GATED**, do not activate | matches default-off mount |
| `docs/edge-fleet-production-activation-security-architecture.md` | **"Security approval obtained"** | contradicts the register |
| `docs/mesh_enrollment_phase_b_gate.md` | live enroll **HTTP 200**; handshake still default-inert | `PHASE_B_HANDSHAKE_ENABLED = True` but `handshake=None` |
| `docs/dev/security/P9_EXPOSURE_REMEDIATION_VERIFICATION.md` | bind `127.0.0.1:8765`; Tailscale serve | CLI default bind is documented there as `0.0.0.0` |
| `edge_tailscale.py` docstring | `require_tailscale` raises if not connected | **FACT:** it logs and returns |
| `EdgeFleet.node_allowlist` docstring | `None` when not restricted | **FACT:** `None` denies enroll |
| Mesh Phase B doc | gateway gated by `MINDROOM_MESH_ENROLLMENT` | **FACT:** composition root never reads it |

Docstrings on the stores themselves are precise about transactions and schemas.

## Technical Debt Signals

1. **Production revoke is permanently 403.**
   Store quality is high; the HTTP permission check cannot succeed under `verify_user`.
   This is the Critical hygiene defect named in the scope document.

2. **Tailscale preflight is unused.**
   `require_tailscale` does not implement its own docstring.
   Scope treats a real check as a sign-off prerequisite.

3. **Dead allowlist parameter on the node router.**
   Operators may believe the HTTP layer enforces the list; only the store does.

4. **Dead `MINDROOM_MESH_ENROLLMENT`.**
   Docs and vulture treat it as a gate; nothing at startup calls `enrollment_flag_enabled`.

5. **Handshake has no protocol.**
   `Callable[[], None]` cannot carry URL, token, or error.
   SM3 needs a contract that does not exist yet.

6. **No MeshGateway composition root.**
   Quality of the gateway module is independent of activation; the runtime never builds it.

7. **`record_flight_event` unused.**
   P10 unit tests inject a recorder; live turns do not.
   SM4 cannot be met by tests of the store alone.

8. **Committed demo Bearer key.**
   `ADMIN_KEY = "u02UPriNVXGesySJtoGGy46C7-H9w6RG_1x6w5ADJUQ"` in `edge_fleet_cross_device_demo.py` and as the default in `scripts/testing/mesh_live_external_placement_demo.py`.
   Scope requires removal.

9. **Two learning products share a word.**
   Agno `learning: false` on `openclaw` is easy to confuse with P10.
   SM5 and SM4 can be implemented as if they were one switch.

10. **Mesh imports private fleet helpers.**
    `_b64`, `_json`, `_unb64`, `_utc` are cross-module coupling.

11. **Admin rate limiter identity is `"coordinator"`** for issue/list/queue.
    Per-user admin budgets do not apply except on revoke.

12. **In-memory mesh transport vs docs that say Matrix is the wire.**
    Default path never talks to a homeserver.

## Security Notes (quality, not a full review)

Positive: fail-closed allowlist, single-use nonces, clock skew, canonical Base64URL, identity equivocation checks, no-store on issued tokens, mode-0600 identity files, loopback-or-HTTPS node client.
Negative: leftover demo key; revoke permission field that production auth cannot populate (fail-closed, but the feature is inert); Tailscale not enforced; P9 docs disagree about whether activation is approved.

## CI/CD

`.github/workflows/pytest.yml` (shallow read): checkout, uv, Python 3.13, `uv sync --all-extras`, pytest excluding `requires_matrix`, then `git diff --exit-code`.
No fleet-specific job.
No live smoke of `/api/edge-fleet/enroll` in that workflow.

## Maintainability Verdict

The stores and publishers are small, invariant-heavy, and well tested at the library seam.
The activation problem is composition and honesty of docs, not missing domain logic.
Highest-leverage quality work for this intent is to make revoke and Tailscale true on the production path, delete or wire dead flags, remove the demo key, and stop describing Agno `learning:` as P10.
