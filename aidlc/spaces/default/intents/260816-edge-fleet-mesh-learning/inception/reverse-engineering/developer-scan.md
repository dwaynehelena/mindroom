# Developer Code Scan Results

Repo: `mindroom`
Root: `/Users/dwayne/mindroom`
Breadth: focused scan of edge-fleet activation, mesh enrollment handshake, and governed learning-loop promotion
Intent: `260816-edge-fleet-mesh-learning` / `edge-fleet-mesh-learning-activation`

Items below marked **FACT** were read in source. Items marked **HYPOTHESIS** are inferences not proven by a live process run.

### Scan Coverage
- **Analyzed deeply**:
  - `src/mindroom/edge_fleet.py`
  - `src/mindroom/api/edge_fleet.py`
  - `src/mindroom/api/main.py`
  - `src/mindroom/api/auth.py`
  - `src/mindroom/edge_node.py`
  - `src/mindroom/edge_tailscale.py`
  - `src/mindroom/mesh/enrollment.py`
  - `src/mindroom/mesh/gateway.py`
  - `src/mindroom/mesh/transport.py`
  - `src/mindroom/mesh/__init__.py`
  - `src/mindroom/config/mesh.py`
  - `src/mindroom/config/main.py`
  - `src/mindroom/learning_loop.py`
  - `src/mindroom/learning_runtime.py`
  - `src/mindroom/learning_stable_publishers.py`
  - `src/mindroom/learning_capture.py`
  - `src/mindroom/learning_candidates.py`
  - `src/mindroom/learning_publishers.py`
  - `src/mindroom/flight_recorder.py`
  - `src/mindroom/agents.py`
  - `config.yaml`
  - `edge_fleet_cross_device_demo.py`
  - `scripts/learning_stable_demo.py`
  - `scripts/testing/mesh_phaseb_unit1_live_smoke.py`
  - `docs/dev/portfolio-register.md`
  - `docs/mesh_enrollment_phase_b_gate.md`
  - `docs/edge-fleet-production-activation-security-architecture.md`
  - `docs/dev/security/P9_EXPOSURE_REMEDIATION_VERIFICATION.md`
  - `tests/test_edge_fleet.py`
  - `tests/test_edge_node.py`
  - `tests/test_p9_remediation.py`
  - `tests/api/test_edge_fleet_api.py`
  - `tests/api/test_edge_fleet_lifecycle.py`
  - `tests/api/test_edge_fleet_security.py`
  - `tests/api/test_edge_fleet_revocation_api.py`
  - `tests/api/test_edge_fleet_revocation_core.py`
  - `tests/test_mesh_enrollment.py`
  - `tests/test_mesh_enrollment_inverted.py`
  - `tests/test_learning_loop.py`
  - `tests/test_learning_runtime.py`
  - `tests/test_learning_capture.py`
  - `tests/test_learning_candidates.py`
  - `tests/test_learning_publishers.py`
  - `tests/test_learning_stable_publishers.py`
  - `pyproject.toml`
- **Skimmed only**:
  - rest of `src/mindroom/` for callers
  - remaining `src/mindroom/mesh/` modules
  - other `scripts/testing/mesh_*` demos
  - `scripts/testing/p10_learning_loop_evidence.py`
  - `src/mindroom/config/agent.py` / `config/models.py`
  - `.github/workflows/pytest.yml`
  - `cluster/k8s/instance/default-config.yaml`
  - `docs/agent-mesh-gateway-architecture.md`

See the developer-agent return for the full findings (activation gates, revocation, tailscale, allowlist, mesh, learning loop, dead flags, docs vs code).
