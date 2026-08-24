# Code Structure — Activation Surfaces

This is a **partial** map of the modules that implement Edge Fleet, mesh enrollment, and the P10 learning loop.
It does not describe the rest of `src/mindroom/` in depth.

## Package Layout

The activation code lives in the core `mindroom` package (`src/mindroom/`), not in `saas-platform/` or `frontend/`.

```text
src/mindroom/
  edge_fleet.py              fleet store, tokens, leases, revoke
  edge_node.py               worker HTTP client and identity file
  edge_tailscale.py          tailscale status probe
  api/main.py                flag-gated mount and health fragment
  api/edge_fleet.py          FastAPI routers
  api/auth.py                verify_user used by admin mount
  config/main.py             root Config includes mesh: MeshConfig
  config/mesh.py             default-OFF mesh Pydantic section
  mesh/__init__.py           public mesh exports
  mesh/enrollment.py         identity, authority, registry, coordinator
  mesh/gateway.py            MeshGateway, GatewayOnlyRuntime
  mesh/transport.py          in-memory default, optional nio client
  learning_loop.py           store + GovernedPublisher
  learning_runtime.py        Matrix review + ingest helpers
  learning_capture.py        Flight-Recorder-gated propose
  learning_candidates.py     signed-skill and consent-bound memory producers
  learning_publishers.py     canary JSON publisher
  learning_stable_publishers.py  stable SKILL.md / memory adapters
  flight_recorder.py         hash-chained run records
  agents.py                  Agno learning flag (not P10)
```

Supporting files in the scan:

- `config.yaml` — Agno `learning:` per agent; no `mesh:` or fleet env.
- `edge_fleet_cross_device_demo.py` — live demo client; committed bearer; Tailscale check.
- `scripts/learning_stable_demo.py` — reversible P10 demo.
- `scripts/testing/mesh_phaseb_unit1_live_smoke.py` — inverted enroll against a live API.

## File Classification

| Kind | Files | Role |
|------|-------|------|
| Domain store | `edge_fleet.py`, `mesh/enrollment.py` (`MeshEnrollmentRegistry`), `learning_loop.py`, `flight_recorder.py` | Own SQLite and invariants |
| HTTP boundary | `api/edge_fleet.py`, `api/main.py` | Rate limits, audit logs, status mapping |
| Worker client | `edge_node.py` | Identity file + signed urllib calls |
| Optional preflight | `edge_tailscale.py` | Not on the production call graph |
| Mesh runtime | `mesh/gateway.py`, `mesh/transport.py` | In-process router; default transport is memory |
| Config | `config/mesh.py`, `config/main.py` | Additive, extra=forbid |
| Learning adapters | `learning_publishers.py`, `learning_stable_publishers.py`, `learning_candidates.py`, `learning_capture.py`, `learning_runtime.py` | Publish and capture only |
| Agno wiring | `agents.py` | Separate learning product |
| Auth | `api/auth.py` | Identity only; no permissions |

## Code Patterns

### Fail-closed gates

**FACT:** fleet mount requires enable flag + valid ≥32-byte key.
**FACT:** allowlist `None` denies enroll.
**FACT:** missing `permissions` denies revoke.
**FACT:** mesh handshake stays inert unless three independent switches are on.
**FACT:** P10 capture refuses a run with no Flight Recorder rows.

### Default-OFF coordinators

`MeshGateway` optional collaborators (`enrollment`, `resume`, `session_mapping`, `cancel_prop`, `tool_state`) are `None` by default.
Each path checks `coordinator is not None and coordinator.enabled`.
`MINDROOM_MESH_ENROLLMENT` is **not** one of those checks.

### Frozen dataclasses and strict JSON

Domain records (`EdgeNode`, `JobLease`, `LearningProposal`, `MeshWorkerIdentity`) are `frozen=True, slots=True`.
Signing uses sorted-key compact JSON and non-canonical Base64URL is rejected.

### Transaction style

Fleet mutate paths take `asyncio.Lock`, `BEGIN IMMEDIATE`, and roll back on `BaseException`.
Revoke writes the tombstone, requeues leases, and inserts `edge_fleet_audit` in one transaction.
Mesh registry uses a thread lock and synchronous `sqlite3`.
Learning transitions go through `_transition` with expected-stage guards.

### Router factory, not module-global app routes

`create_edge_fleet_router` and `create_edge_fleet_admin_router` take an `EdgeFleet` instance.
`api/main.py` builds at most one process-global fleet and mounts it.
Admin routes declare `Depends(lambda: None)` placeholders; the real gate is `dependencies=[Depends(verify_user)]` on `include_router`.

### Two claim faces on one HMAC key

`MeshEnrollmentAuthority` subclasses `EnrollmentAuthority`.
`issue` writes `mindroom.mesh-enrollment/1` (`worker_id`, `agent_name`).
`issue_edge` writes `mindroom.edge-enrollment/1` (`node_id`).
`verify` dispatches on the claim `schema` field.

### Placeholder and dead surface

| Symbol | Status |
|--------|--------|
| `create_edge_fleet_router.node_allowlist` | Accepted, never read |
| `MINDROOM_MESH_ENROLLMENT` / `enrollment_flag_enabled` | Defined, no production caller |
| `require_tailscale` | Defined, no caller, does not raise |
| `record_flight_event` | Defined, no caller |
| `MeshEnrollmentCoordinator.handshake` | Default `None` |
| Admin `_user: Depends(lambda: None)` | Placeholder; identity from `request.scope["auth_user"]` |

## Persistence Shape (FACT)

`EdgeFleet.open` creates:

- `edge_enrollment_nonce(nonce, used_at)`
- `edge_node(node_id, runtime, public_key, capabilities_json, last_seen_at, revoked_at)`
- `edge_request_nonce(node_id, nonce, used_at)`
- `edge_job(job_id, runtime, required_capabilities_json, payload_json, status, node_id, lease_id, lease_expires_at, result_json, result_signature)`
- `edge_fleet_audit(id, event, node_id, actor, detail, occurred_at)`

Job `status` is `queued | leased | completed`.
Revoke moves `leased` rows for that node back to `queued` and clears the lease columns.

`LearningLoopStore` creates `learning_proposal` and `learning_publication`.
Proposal stages are constrained by a CHECK list that includes `uncertain`.

## Import Direction

Dependencies flow inward toward the stores.

- `api/edge_fleet.py` → `edge_fleet.py`
- `api/main.py` → `api/edge_fleet.py` + `edge_fleet.py` + `api/auth.py`
- `edge_node.py` → attestation helpers in `edge_fleet.py` (no HTTP server import)
- `mesh/enrollment.py` → `edge_fleet.EnrollmentAuthority` and private `_b64` / `_json` / `_unb64` / `_utc`
- `mesh/gateway.py` → enrollment, transport, cursor, loop guard
- `learning_capture.py` → `flight_recorder` + `learning_loop`
- `learning_runtime.py` → `learning_capture` + `learning_loop`; `approval_manager` is imported inside `request_runtime_learning_review`
- `agents.py` does not import the P10 modules

**HYPOTHESIS:** mesh enrollment importing private helpers from `edge_fleet.py` will break if those helpers are later made module-private in earnest or relocated.

## Tests Beside the Code

The scanned tests sit under `tests/` and `tests/api/`, not next to the modules.
They construct `FastAPI()` apps and inject fleets; they do not require a running `mindroom run` process.
Live scripts (`mesh_phaseb_unit1_live_smoke.py`, `edge_fleet_cross_device_demo.py`, `scripts/learning_stable_demo.py`) are not part of the pytest default path.

## What This Scan Did Not Structure

The rest of `src/mindroom/` (turn pipeline, orchestrator, tools, memory, knowledge) was skimmed only for callers.
`src/mindroom/mesh/` modules other than enrollment, gateway, transport, and `__init__` were not read in full.
SaaS, frontend, and cluster charts were out of depth except a shallow look at `cluster/k8s/instance/default-config.yaml` (no Edge Fleet keys found).
