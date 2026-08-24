# Component Inventory — Activation Surfaces

Heading names in this file are the component identifiers copied into `reverse-engineering-timestamp.md`.
Ownership is exclusive: each entity below is listed under one component.
This inventory is **partial**; it covers only the scanned activation surfaces.

## Edge Fleet

Coordinator-side worker inventory and job queue for OpenClaw and Hermes processes.

### Edge Fleet Store

- **Module:** `src/mindroom/edge_fleet.py` (`EdgeFleet`, `EdgeNode`, `EdgeJob`, `JobLease`, `RevokeOutcome`, `EdgeFleetError`)
- **Responsibility:** persist nodes, jobs, leases, nonces, and revocation tombstones; enforce allowlist, equivocation, attestation, and lease exclusivity
- **Owns:** SQLite tables `edge_node`, `edge_job`, `edge_enrollment_nonce`, `edge_request_nonce`, `edge_fleet_audit`
- **Depends on:** `EnrollmentAuthority`, `aiosqlite`, `cryptography` Ed25519
- **Does not own:** HTTP status codes, Tailscale, mesh rooms, P10 proposals
- **Notes (FACT):** `node_allowlist is None` denies every `enroll`

### Enrollment Authority

- **Module:** `src/mindroom/edge_fleet.py` (`EnrollmentAuthority`)
- **Responsibility:** issue and verify HMAC-SHA256 claims with schema `mindroom.edge-enrollment/1`
- **Owns:** enrollment key (≥32 bytes) and claim shape
- **Depends on:** `hmac`, `hashlib`, canonical JSON helpers in the same file
- **Used by:** `EdgeFleet.issue_enrollment` / `enroll`; subclassed by `MeshEnrollmentAuthority`

### Edge Fleet HTTP API

- **Module:** `src/mindroom/api/edge_fleet.py`
- **Responsibility:** expose node and admin HTTP; rate-limit; emit audit logs; map `EdgeFleetError` to 401/409/422
- **Owns:** Pydantic request/response models, in-memory `_RateLimiter`, `_require_revoke_permission`
- **Depends on:** `EdgeFleet`, FastAPI
- **Does not own:** `verify_user` implementation
- **Notes (FACT):** `create_edge_fleet_router(..., node_allowlist=)` is accepted and never read; revoke requires `admin.nodes.revoke`

### Edge Node Client

- **Module:** `src/mindroom/edge_node.py`
- **Responsibility:** persist a mode-0600 Ed25519 identity and call the node HTTP API
- **Owns:** `EdgeNodeIdentity` (`mindroom.edge-node/1`), `EdgeNodeClient`, `SubprocessJobExecutor`
- **Depends on:** `node_request_attestation_payload`, `result_attestation_payload`
- **Constraint:** HTTPS or loopback HTTP only

### Tailscale Connectivity Check

- **Module:** `src/mindroom/edge_tailscale.py`
- **Responsibility:** run `tailscale status --json` and return `TailscaleCheckResult`
- **Owns:** `check_tailscale_connectivity`, `require_tailscale`, `format_tailscale_result`
- **Depends on:** local `tailscale` binary on PATH
- **Notes (FACT):** only `edge_fleet_cross_device_demo.py` calls `check_tailscale_connectivity`; `require_tailscale` has zero callers and does not raise

## Agent Mesh

In-process worker routing and optional local enrollment.
Not constructed by the orchestrator, bot, or API.

### Mesh Enrollment Coordinator

- **Module:** `src/mindroom/mesh/enrollment.py` (`MeshEnrollmentCoordinator`)
- **Responsibility:** load-or-create identity, issue tokens, admit/re-admit, optionally invoke handshake
- **Owns:** `enabled`, `handshake`, `handshake_enabled`, identity path
- **Depends on:** `MeshEnrollmentAuthority`, `MeshEnrollmentRegistry`
- **Notes (FACT):** `handshake` type is `Callable[[], None] | None`, default `None`; `handshake_enabled` default `False`

### Mesh Enrollment Authority

- **Module:** `src/mindroom/mesh/enrollment.py` (`MeshEnrollmentAuthority`)
- **Responsibility:** issue mesh claims (`mindroom.mesh-enrollment/1`) and edge claims (`mindroom.edge-enrollment/1`); verify both
- **Owns:** mesh-facing `issue` / `verify` dispatch
- **Depends on:** `EnrollmentAuthority` (subclass)

### Mesh Enrollment Registry

- **Module:** `src/mindroom/mesh/enrollment.py` (`MeshEnrollmentRegistry`, `MeshEnrolledWorker`)
- **Responsibility:** durable `worker_id` ↔ room inventory and enrollment-nonce replay protection
- **Owns:** SQLite tables `mesh_worker`, `mesh_enrollment_nonce`
- **Depends on:** synchronous `sqlite3`
- **Does not own:** edge_node rows (separate inventory)

### Mesh Gateway

- **Module:** `src/mindroom/mesh/gateway.py` (`MeshGateway`, `GatewayOnlyRuntime`, `GatewayExecutionGate`)
- **Responsibility:** register workers, route messages into an outbox, drain via transport, optional resume/cancel/tool-state
- **Owns:** in-memory worker map, outbox, lifecycle sink
- **Depends on:** `MeshTransport`, `MeshCursorStore`, optional `enrollment` coordinator
- **Notes (FACT):** static registration when enrollment is absent or `enabled` is false; no API/orchestrator constructor

### Mesh Transport

- **Module:** `src/mindroom/mesh/transport.py` (`MeshTransport`, `MatrixMeshTransport`)
- **Responsibility:** deliver outbox entries and replay from a cursor
- **Owns:** in-memory `_delivered_messages` when `client is None`
- **Depends on:** `MeshCursorStore`; optional injected object with `room_send` / `sync`
- **Notes (FACT):** default is in-memory; Matrix wire key is `io.mindroom.mesh`

### Mesh Configuration

- **Module:** `src/mindroom/config/mesh.py` plus `Config.mesh` in `src/mindroom/config/main.py`
- **Responsibility:** additive Pydantic section for mode, enrollment, session mapping, tool state, cancellation, cursor, loop
- **Owns:** defaults (enrollment `enabled=False`, mode `gateway_only` unless authored)
- **Depends on:** Pydantic
- **Notes (FACT):** `MINDROOM_MESH_ENROLLMENT` is not read here; `resolve_mesh_runtime_mode` reads `MINDROOM_MESH_GATEWAY_MODE`

## Governed Learning

P10 proposal governance.
Separate from Agno `learning:`.

### Learning Loop Store

- **Module:** `src/mindroom/learning_loop.py` (`LearningLoopStore`, `LearningProposal`, `EvaluationEvidence`)
- **Responsibility:** persist immutable proposals and stage transitions
- **Owns:** `learning_proposal`, `learning_publication`
- **Depends on:** `aiosqlite`
- **Stages:** `proposed`, `evaluated`, `approved`, `rejected`, `canary`, `stable`, `rolled_back`, `uncertain`

### Governed Publisher

- **Module:** `src/mindroom/learning_loop.py` (`GovernedPublisher`)
- **Responsibility:** canary both runtimes or compensate; stabilize both runtimes or return to canary
- **Owns:** dual-runtime publish/rollback orchestration
- **Depends on:** injected `RuntimePublisher` / `RuntimeRollback` callables for `openclaw` and `hermes`

### Learning Capture

- **Module:** `src/mindroom/learning_capture.py`
- **Responsibility:** create a proposal only when Flight Recorder shows a successful, non-failed source run
- **Owns:** `LearningCandidate`, `FlightRecorderLearningCapture`, `capture_learning_candidate`
- **Depends on:** `FlightRecorder`, `LearningLoopStore`

### Learning Candidates

- **Module:** `src/mindroom/learning_candidates.py`
- **Responsibility:** build candidates from signature-verified skills or consent-bound portable memory
- **Owns:** `candidate_from_signed_skill`, `candidate_from_portable_memory`
- **Depends on:** `SkillTrustRegistry` / `PortableMemory` types (imported for type checking and runtime validation)

### Learning Runtime

- **Module:** `src/mindroom/learning_runtime.py`
- **Responsibility:** ingest a runtime candidate event; request Matrix review via `mindroom.learning.promote`
- **Owns:** `GovernedLearningRuntime`, `RuntimeLearningCandidateEvent`, `LearningReviewContext`
- **Depends on:** `LearningLoopStore`, `capture_learning_candidate`, optional `get_approval_store()`
- **Notes (FACT):** not imported by orchestrator or bot modules

### Learning Filesystem Canary Publisher

- **Module:** `src/mindroom/learning_publishers.py` (`LearningFilesystemPublisher`)
- **Responsibility:** write receipt-bound JSON under `.mindroom-learning-canary/` and roll it back by digest
- **Owns:** canary directory layout and 0600 files
- **Depends on:** `LearningProposal` governance fields (`reviewed_by`, `review_reason`, passed evaluation)

### Learning Stable Publisher

- **Module:** `src/mindroom/learning_stable_publishers.py` (`LearningStablePublisher`)
- **Responsibility:** install a verified skill as `SKILL.md` or upsert/delete portable memory
- **Owns:** active skill payload format (front-matter + entrypoint)
- **Depends on:** `SkillTrustRegistry`, `PropagationHandler`

### Flight Recorder

- **Module:** `src/mindroom/flight_recorder.py`
- **Responsibility:** append hash-chained records for a `run_id`
- **Owns:** `FlightRecorder`, `record_flight_event`, `flight_recorder.db` under tracking
- **Depends on:** `aiosqlite`
- **Notes (FACT):** `record_flight_event` has no callers; live runs do not emit P10 candidates through it

## Shared Runtime

Composition and identity used by the surfaces above.

### Dashboard Authentication

- **Module:** `src/mindroom/api/auth.py` (`verify_user`)
- **Responsibility:** validate trusted-upstream, standalone API key, or Supabase token
- **Owns:** `auth_user` dict placed on `request.scope`
- **Returns (FACT):** `{user_id, email}` (plus trusted-upstream extras); never `permissions`
- **Used by:** admin fleet router mount

### API Application Mount

- **Module:** `src/mindroom/api/main.py`
- **Responsibility:** build the process fleet from env, mount routers, open/close the DB, add `/api/health` fleet fragment
- **Owns:** `MINDROOM_EDGE_FLEET_*` env contract and `_edge_fleet_instance`
- **Depends on:** `create_edge_fleet_router`, `create_edge_fleet_admin_router`, `verify_user`, `EnrollmentAuthority`
- **Does not own:** mesh gateway construction

### Agent Agno Learning

- **Module:** `src/mindroom/agents.py` (`_is_learning_enabled`, `_resolve_agent_learning`) plus `config.yaml`
- **Responsibility:** turn Agno `Agent.learning` on or off from authored config
- **Owns:** per-agent `learning` / `learning_mode` interpretation
- **Does not own:** P10 proposals or SKILL.md promotion
- **Notes (FACT):** `config.yaml` sets `learning: false` only on agent `openclaw`

## Ownership Summary

| Entity | Owner |
|--------|-------|
| `edge_node` / `edge_job` rows | Edge Fleet Store |
| Enrollment HMAC key and `mindroom.edge-enrollment/1` | Enrollment Authority |
| HTTP `/api/edge-fleet*` | Edge Fleet HTTP API |
| Node private key file | Edge Node Client |
| Tailscale probe | Tailscale Connectivity Check |
| `mesh_worker` rows | Mesh Enrollment Registry |
| Handshake thunk | Mesh Enrollment Coordinator |
| Outbox and worker map | Mesh Gateway |
| In-memory or nio delivery | Mesh Transport |
| `learning_proposal` rows | Learning Loop Store |
| Dual-runtime canary/stable transaction | Governed Publisher |
| `.mindroom-learning-canary/*.json` | Learning Filesystem Canary Publisher |
| `{skill_id}/SKILL.md` | Learning Stable Publisher |
| Flight Recorder chain | Flight Recorder |
| Dashboard `auth_user` | Dashboard Authentication |
| Process-global fleet mount | API Application Mount |
| Agno `learning:` flag | Agent Agno Learning |
