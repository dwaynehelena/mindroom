# API Documentation — Edge Fleet and Related Surfaces

Only contracts read in source are listed.
No endpoint, header, or field is invented.
This is a **partial** API map for the activation surfaces.

## External HTTP — Node router

Mounted only when `_edge_fleet_from_runtime_paths` returns an `EdgeFleet`.
Prefix: `/api/edge-fleet`.
Tag: `edge-fleet`.
There is no dashboard `verify_user` on this router.
Node calls after enroll authenticate with Ed25519 headers.

Rate limits are per router instance (requests per 60s window):

| Route | Identity | Limit |
|-------|----------|-------|
| `POST /enroll` | client IP | 5 |
| `POST /heartbeat` | node_id | 60 |
| `POST /lease` | node_id | 30 |
| `POST /complete` | node_id | 30 |

### `POST /api/edge-fleet/enroll`

Request body (`EnrollmentRequest`, extra=forbid):

- `token` string, min_length 1, max_length 16384

Success: `200` `NodeResponse` (`node_id`, `runtime`, `capabilities`, `last_seen_at`).
Failure: `401` `"Edge node authentication failed"` for any `EdgeFleetError` (malformed token, bad signature, expired, allowlist miss, replay, revoked re-enroll, identity equivocation).
Audit events: `enrollment.success` / `enrollment.failure`.

### Authenticated node headers

Used by heartbeat, lease, and complete:

| Header | Type |
|--------|------|
| `X-Edge-Node-ID` | string node id |
| `X-Edge-Timestamp` | datetime |
| `X-Edge-Nonce` | string |
| `X-Edge-Signature` | Base64URL Ed25519 signature |

Signed payload schema `mindroom.edge-request/1` covers `body_digest`, `method` (`POST` or `PUT` only), `node_id`, `nonce`, `path` (must start `/api/edge-fleet/`), `schema`, `timestamp`.
Clock skew default is 5 minutes.
Auth failure is `401`.

### `POST /api/edge-fleet/heartbeat`

Body (`HeartbeatRequest`): `capabilities` tuple[str], max_length 256.
Success: `200` `NodeResponse`.
Store errors: `409` `"Edge fleet operation could not be completed"`.

### `POST /api/edge-fleet/lease`

Body (`LeaseRequest`): `lease_seconds` int, default 60, ge 1, le 3600.
Success with work: `200` `LeaseResponse` (`job_id`, `lease_id`, `payload`, `expires_at`).
Success with no work: `200` with empty/`null` body (`LeaseResponse | None`).
Store errors: `409`.

### `POST /api/edge-fleet/complete`

Body (`CompleteRequest`): `job_id`, `lease_id`, `lease_expires_at`, `result` object, `result_signature`.
Success: `204`.
Store errors: `409`.
The router rebuilds a `JobLease` with an empty payload dict; completion matches on job, node, lease id, and expiry, not on payload bytes.

## External HTTP — Admin router

Prefix: `/api/edge-fleet-admin`.
Tag: `edge-fleet-admin`.
Mounted with `dependencies=[Depends(verify_user)]`.
Most handlers still declare `_user: Annotated[dict, Depends(lambda: None)]` as a no-op.

Admin limiter: 120 requests / 60s keyed as `"coordinator"` (not the authenticated user) for issue, list, and queue.
Revoke uses a separate 5 / 60s limiter keyed by admin principal.

### `POST /api/edge-fleet-admin/enrollments`

Body (`EnrollmentIssueRequest`): `node_id`, `runtime` (`openclaw` | `hermes`), `public_key`, `capabilities` (min 1), `expires_in_seconds` default 600 (1–3600).
Success: `200` `EnrollmentIssueResponse` (`token`, `expires_at`) plus `Cache-Control: no-store`.
Invalid: `422` `"Edge fleet request is invalid"`.

### `GET /api/edge-fleet-admin/nodes`

Query: `max_age_seconds` default 300, must be 1–3600 or `422`.
Success: tuple of `NodeResponse` for nodes with `revoked_at IS NULL` and fresh `last_seen_at`.

### `POST /api/edge-fleet-admin/jobs`

Body (`QueueJobRequest`): `job_id`, `runtime`, `required_capabilities`, `payload`.
Success: `201` `EdgeJobResponse`.
Conflict (equivocation): `409`.

### `GET /api/edge-fleet-admin/jobs/{job_id}`

Success: `200` `EdgeJobResponse` (`status` is `queued` | `leased` | `completed`).
Missing: `404` `"Edge job was not found"`.

### `DELETE /api/edge-fleet-admin/nodes/{node_id}`

Path: `node_id` 1–128 characters.
Requires `admin.nodes.revoke` in `user["permissions"]` (list/tuple/set or truthy dict entry).
**FACT:** production `verify_user` never sets `permissions`, so this handler returns `403` `"Insufficient permission to revoke nodes"` for every authenticated dashboard user.
Unauthenticated callers fail earlier in `verify_user` (`401`).
If a test double injects the permission:

- never-existed node → `404` `"Edge node was not found"`
- existed (including already revoked) → `204` (idempotent)
- more than 5 revokes / 60s / principal → `429` `"Node revocation rate limit exceeded"` and audit `admin.node_revoke_burst`

Store revoke still works when called directly.

## Health fragment

`GET /api/health` always includes `edge_fleet`.
If the process fleet is `None`: `{"enabled": false}`.
If mounted: `{"enabled": true, "healthy_nodes": <int>}` using a 600s freshness window, or `{"enabled": true, "error": "<str>"}` on exception.
This field does not flip process liveness.

## Dashboard authentication used by admin routes

`verify_user` (`src/mindroom/api/auth.py`) returns one of:

- trusted upstream: `{user_id, email, auth_source: "trusted_upstream"}` and optional `matrix_user_id`
- standalone with or without `MINDROOM_API_KEY`: `{user_id: "standalone", email: None}`
- Supabase: `{user_id: user.id, email: user.email}`

It does not return `permissions`.
Standalone public paths (`/api/homeassistant/callback`, `/api/integrations/spotify/callback`) are unrelated to the fleet.

## Node client contract

`EdgeNodeClient` (`src/mindroom/edge_node.py`) is the packaged caller of the node router.

- Base URL must be HTTPS or loopback HTTP (`127.0.0.1`, `localhost`, `::1`).
- `enroll(token)` expects HTTP 200 and `node_id` matching the identity.
- `heartbeat()` POSTs `{capabilities: [...]}`.
- `run_once(executor)` POSTs `/lease`, runs the executor, signs `mindroom.edge-result/1`, POSTs `/complete`, expects 204.
- Identity file schema `mindroom.edge-node/1`, mode 0600, exclusive create.

## Internal store API (not HTTP)

`EdgeFleet` public methods used by the routers and tests:

- `issue_enrollment(...)` → token string
- `enroll(token, observed_at=)` → `EdgeNode`
- `heartbeat(node_id, capabilities=, observed_at=)` → `EdgeNode`
- `revoke_node(node_id, observed_at=, actor=)` → `RevokeOutcome(existed, already_revoked)`
- `authenticate_request(...)` → `None` or `EdgeFleetError`
- `healthy_nodes(observed_at=, max_age=)` → tuple[`EdgeNode`]
- `queue_job(...)` / `job(job_id)` / `acquire(...)` / `complete(...)`

`EnrollmentAuthority.issue` / `verify` implement `mindroom.edge-enrollment/1`.
Key length must be ≥ 32 bytes.

## Mesh enrollment API (in-process)

There is no FastAPI router for mesh enrollment.
Public types from `mindroom.mesh.enrollment`:

- `MeshWorkerIdentity.generate` / `load`
- `MeshEnrollmentAuthority.issue` / `issue_edge` / `verify`
- `MeshEnrollmentRegistry.open` / `enroll` / `worker` / `known_worker_ids`
- `MeshEnrollmentCoordinator.admit` / `issue_token` / `issue_edge_token` / `load_or_create_identity`

`admit` returns `MeshEnrollmentResult(status="enrolled"|"reconnected"|"rejected", worker_id, reason=)`.
Handshake: `handshake: Callable[[], None] | None`, `handshake_enabled: bool = False`.
`enrollment_flag_enabled()` reads `MINDROOM_MESH_ENROLLMENT` but has no production caller.

`MeshGateway` methods relevant to this intent: `register_worker`, `deregister_worker`, `route_message`, `deliver_pending`, `resume_worker`.
They are not exposed over HTTP in the scanned files.

## Learning-loop API (in-process)

There is no FastAPI router for P10.

`LearningLoopStore`:

- `propose(...)` → stage `proposed`
- `record_evaluation(proposal_id, EvaluationEvidence)` → `evaluated` if the suite passed and the digest matches
- `review(proposal_id, reviewer_id=, approved=, reason=)` → `approved` or `rejected`; blank reviewer or reason raises
- `publication(...)` / `receipts(...)` / `get(...)`

`GovernedPublisher`:

- `canary(proposal_id)` — both `openclaw` and `hermes` or compensate
- `stabilize(proposal_id)` — requires both canary receipts and stable adapters

`GovernedLearningRuntime.ingest` / `review` and module functions `capture_runtime_learning_event` / `request_runtime_learning_review` exist.
`request_runtime_learning_review` uses `get_approval_store()` and tool name `mindroom.learning.promote`.
**FACT:** `record_flight_event` is unused, so live turns do not feed capture.

## Environment contracts that change the HTTP surface

| Variable | Effect |
|----------|--------|
| `MINDROOM_EDGE_FLEET_ENABLED` | default false; must be true to mount |
| `MINDROOM_EDGE_FLEET_ENROLLMENT_KEY` | URL-safe Base64, decoded length ≥ 32 or fleet stays `None` |
| `MINDROOM_EDGE_FLEET_PATH` | optional DB path; else `{storage_root}/edge_fleet.db` |
| `MINDROOM_EDGE_FLEET_NODE_ALLOWLIST` | comma-separated node ids; unset → `None` → every enroll denied |
| `MINDROOM_MESH_GATEWAY_MODE` | `full` or `gateway_only`; not an HTTP mount |
| `MINDROOM_MESH_ENROLLMENT` | documented as a gate; unread by the composition root |

## Demo credentials (not a supported API)

**FACT:** `edge_fleet_cross_device_demo.py` hard-codes `ADMIN_KEY = "u02UPriNVXGesySJtoGGy46C7-H9w6RG_1x6w5ADJUQ"` as a Bearer token.
**FACT:** `scripts/testing/mesh_live_external_placement_demo.py` defaults to the same string via `os.environ.get`.
Those scripts are not the production contract.
Removing the leftover demo key is in the approved scope.

## What is not an API here

- No public REST for mesh admit, handshake, or outbox drain.
- No public REST for P10 propose / review / canary / stabilize.
- No Tailscale HTTP endpoint.
- Router `node_allowlist` is not a request field and is not consulted.
