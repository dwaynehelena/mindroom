# Reverse Engineering Timestamp

## Run Metadata

- **Date:** 2026-08-16
- **Commit:** `fe018be77b252ddc9faf594f388f95cceb44eeb2` (short `fe018be77`)
- **Repo:** `mindroom`
- **Intent:** `edge-fleet-mesh-learning`
- **Kind:** `partial`
- **Fingerprint:** `964056a6a1c5027a02b1a3ff3bdb3b2257a2f5d0`
- **Conversation language:** English

This run is not a whole-repo deep scan.
The YAML `kind` value for this store is only `partial`.

## Method

The developer-agent listed the files it read and skimmed.
The architect-agent re-read those sources, preserved the developer's established facts, and wrote the eight sibling artifacts in this directory.
Approved Ideation artifacts were not edited.

Established facts carried forward:

- Edge fleet mounts only if the enable flag is set and the enrollment key decodes to at least 32 bytes.
- An unset allowlist means every enroll is denied (fail-closed).
- Router `node_allowlist` is accepted and never used.
- Admin revoke requires `admin.nodes.revoke` in `user["permissions"]`.
- Real `verify_user` returns only identity fields; production DELETE is always 403.
- Store `revoke_node` works.
- `check_tailscale_connectivity` is only called from the cross-device demo.
- `require_tailscale` has zero callers and does not raise.
- Mesh handshake is `Callable[[], None] | None`, default `None`, plus `handshake_enabled=False`.
- No orchestrator, bot, or API constructs `MeshGateway`.
- Default mesh transport is in-memory when no nio client is injected.
- `MINDROOM_MESH_ENROLLMENT` is dead at the composition root.
- Learning stages include proposed → evaluated → approved → canary → stable.
- `record_flight_event` has no callers; live runs do not emit candidates.
- Canary writes JSON under `.mindroom-learning-canary/`; stable writes `SKILL.md`.
- Review needs `reviewer_id` and `reason`.
- Agno `learning: false` only on agent `openclaw`; that is not the P10 loop.
- The demo bearer `ADMIN_KEY = "u02UPriNVXGesySJtoGGy46C7-H9w6RG_1x6w5ADJUQ"` is committed in two scripts.
- Docs disagree: portfolio-register says P9 is gated; security-architecture says approval was obtained; mesh Phase B says live enroll returned 200.

## Scope of Analysis

```yaml
scope_version: 1
kind: partial
intent: edge-fleet-mesh-learning
fingerprint: 964056a6a1c5027a02b1a3ff3bdb3b2257a2f5d0
analyzed:
  paths:
    - src/mindroom/edge_fleet.py
    - src/mindroom/api/edge_fleet.py
    - src/mindroom/api/main.py
    - src/mindroom/api/auth.py
    - src/mindroom/edge_node.py
    - src/mindroom/edge_tailscale.py
    - src/mindroom/mesh/enrollment.py
    - src/mindroom/mesh/gateway.py
    - src/mindroom/mesh/transport.py
    - src/mindroom/mesh/__init__.py
    - src/mindroom/config/mesh.py
    - src/mindroom/config/main.py
    - src/mindroom/learning_loop.py
    - src/mindroom/learning_runtime.py
    - src/mindroom/learning_stable_publishers.py
    - src/mindroom/learning_capture.py
    - src/mindroom/learning_candidates.py
    - src/mindroom/learning_publishers.py
    - src/mindroom/flight_recorder.py
    - src/mindroom/agents.py
    - config.yaml
    - edge_fleet_cross_device_demo.py
    - scripts/learning_stable_demo.py
    - scripts/testing/mesh_phaseb_unit1_live_smoke.py
    - docs/dev/portfolio-register.md
    - docs/mesh_enrollment_phase_b_gate.md
    - docs/edge-fleet-production-activation-security-architecture.md
    - docs/dev/security/P9_EXPOSURE_REMEDIATION_VERIFICATION.md
    - tests/test_edge_fleet.py
    - tests/test_edge_node.py
    - tests/test_p9_remediation.py
    - tests/api/test_edge_fleet_api.py
    - tests/api/test_edge_fleet_lifecycle.py
    - tests/api/test_edge_fleet_security.py
    - tests/api/test_edge_fleet_revocation_api.py
    - tests/api/test_edge_fleet_revocation_core.py
    - tests/test_mesh_enrollment.py
    - tests/test_mesh_enrollment_inverted.py
    - tests/test_learning_loop.py
    - tests/test_learning_runtime.py
    - tests/test_learning_capture.py
    - tests/test_learning_candidates.py
    - tests/test_learning_publishers.py
    - tests/test_learning_stable_publishers.py
    - pyproject.toml
  components:
    - Edge Fleet
    - Edge Fleet Store
    - Enrollment Authority
    - Edge Fleet HTTP API
    - Edge Node Client
    - Tailscale Connectivity Check
    - Agent Mesh
    - Mesh Enrollment Coordinator
    - Mesh Enrollment Authority
    - Mesh Enrollment Registry
    - Mesh Gateway
    - Mesh Transport
    - Mesh Configuration
    - Governed Learning
    - Learning Loop Store
    - Governed Publisher
    - Learning Capture
    - Learning Candidates
    - Learning Runtime
    - Learning Filesystem Canary Publisher
    - Learning Stable Publisher
    - Flight Recorder
    - Shared Runtime
    - Dashboard Authentication
    - API Application Mount
    - Agent Agno Learning
    - Ownership Summary
shallow:
  paths:
    - src/mindroom/
    - src/mindroom/mesh/
    - scripts/testing/
    - cluster/k8s/instance/default-config.yaml
    - .github/workflows/pytest.yml
```
