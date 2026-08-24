# Dependencies — Activation Surfaces

Internal edges were confirmed by reading imports and call sites.
External versions are from `pyproject.toml`.
This is a **partial** dependency view.

## External Dependencies Used by Scanned Modules

| Package | Used by | What for |
|---------|---------|----------|
| `fastapi` | `api/edge_fleet.py`, `api/main.py`, `api/auth.py` | routers, `Depends`, HTTPException |
| `pydantic` | `api/edge_fleet.py`, `config/mesh.py` | request models, `MeshConfig` |
| `aiosqlite` | `edge_fleet.py`, `learning_loop.py`, `flight_recorder.py` | async SQLite |
| `cryptography` | `edge_fleet.py`, `edge_node.py`, `mesh/enrollment.py` | Ed25519 |
| `pyjwt` | `api/auth.py` | trusted-upstream JWT |
| `agno` | `agents.py` | Agno Learning objects when `learning:` is not false |

Stdlib-only on these surfaces: `hmac`, `hashlib`, `json`, `sqlite3`, `urllib`, `asyncio`, `os`, `stat`, `secrets`, `shutil`.

## Internal Dependency Graph

```text
api/main.py
  → api/edge_fleet.py
  → edge_fleet.py (EdgeFleet, EnrollmentAuthority)
  → api/auth.py (verify_user)

api/edge_fleet.py
  → edge_fleet.py

edge_node.py
  → edge_fleet.py (attestation payload helpers only)

edge_tailscale.py
  → (none of the above)

mesh/enrollment.py
  → edge_fleet.py (EnrollmentAuthority, EdgeFleetError, WorkerRuntime, _b64, _json, _unb64, _utc)

mesh/gateway.py
  → mesh/enrollment.py (optional, duck-typed `.admit`)
  → mesh/transport.py
  → mesh/cursor.py, loop_guard, models, session_map (skimmed)

mesh/transport.py
  → mesh/cursor.py, lifecycle, models (skimmed)

mesh/__init__.py
  → enrollment, gateway, transport, and other mesh submodules

config/main.py
  → config/mesh.py (MeshConfig field)

learning_capture.py
  → flight_recorder.py
  → learning_loop.py

learning_runtime.py
  → learning_capture.py
  → learning_loop.py
  → approval_manager (function-level import in request_runtime_learning_review)

learning_candidates.py
  → learning_capture.py (LearningCandidate)
  → learning_loop.py (LearningLoopError)

learning_publishers.py
  → learning_loop.py

learning_stable_publishers.py
  → learning_loop.py
  → skill_registry, provenance_memory (outside deep scan)

agents.py
  → Agno learning types
  → does not import learning_loop.py
```

There is no import from orchestrator/bot/API into `MeshGateway` or `GovernedPublisher`.

## Cross-package / cross-repo

None on the deep-scan path.
SaaS platform and cluster charts are not dependents of these modules in the files read.
**FACT:** `cluster/k8s/instance/default-config.yaml` contains no `MINDROOM_EDGE_FLEET_*` keys (shallow grep).

## Runtime environment dependencies

| Name | Required to | Default if missing |
|------|-------------|--------------------|
| `MINDROOM_EDGE_FLEET_ENABLED` | mount routers | fleet `None` |
| `MINDROOM_EDGE_FLEET_ENROLLMENT_KEY` | construct `EnrollmentAuthority` | fleet `None` (warn/error) |
| `MINDROOM_EDGE_FLEET_NODE_ALLOWLIST` | admit any node | `None` → all enroll denied |
| `MINDROOM_EDGE_FLEET_PATH` | override DB location | `{storage_root}/edge_fleet.db` |
| `tailscale` binary | `check_tailscale_connectivity` | status `"unknown"` |
| `MINDROOM_MESH_ENROLLMENT` | (documented gate) | **dead** — no composition-root reader |
| `MINDROOM_MESH_GATEWAY_MODE` | `GatewayRuntimeMode.from_env` / `resolve_mesh_runtime_mode` | `full` in gateway env helper when empty; config resolver defaults `gateway_only` unless `full` |

**FACT:** the two mode resolvers disagree on the empty-env default (`GatewayRuntimeMode.from_env` → `FULL`; `resolve_mesh_runtime_mode` → `GATEWAY_ONLY` when `mode` is not authored).
That is an internal inconsistency, not an HTTP dependency.

## Dead or one-way edges

| Edge | Evidence |
|------|----------|
| `create_edge_fleet_router.node_allowlist` | parameter never referenced |
| `enrollment_flag_enabled` → env | exported; no production caller (vulture whitelist) |
| `require_tailscale` | no callers |
| `check_tailscale_connectivity` | only `edge_fleet_cross_device_demo.py` |
| `record_flight_event` | definition only |
| `MeshEnrollmentCoordinator.handshake` | default `None` |
| Docs → `MINDROOM_MESH_ENROLLMENT` as the gateway gate | gateway checks `coordinator.enabled`, not the env helper |

## Test-only dependents

Scanned tests import the stores, routers, and publishers directly and build in-process `FastAPI()` apps.
They are dependents of the libraries, not of a running server.

Live scripts depend on a running API and extra env:

- `scripts/testing/mesh_phaseb_unit1_live_smoke.py` → `EnrollmentAuthority` + `POST /api/edge-fleet/enroll`
- `edge_fleet_cross_device_demo.py` → node client + Tailscale + committed Bearer admin key
- `scripts/learning_stable_demo.py` → full P10 stack including `DockerSkillSandboxRunner`

## Coupling hotspots

1. **`mesh/enrollment.py` → private helpers in `edge_fleet.py`.**
   Inverted enrollment is cheaper because of this; renaming `_json` / `_utc` becomes a cross-context break.
2. **Admin revoke → `verify_user` shape.**
   The HTTP permission check depends on a field the auth component does not produce.
3. **P10 capture → Flight Recorder.**
   Capture cannot run in production until something writes records; that writer is missing.

## Assumptions

- **HYPOTHESIS:** no hidden caller of `MeshGateway` exists outside `src/mindroom/` (scripts/testing mesh demos were only skimmed).
- **HYPOTHESIS:** `approval_manager.get_approval_store` is the only live review transport if SM4 is wired later.
