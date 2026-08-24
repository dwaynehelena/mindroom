# Contract Summary — Edge Fleet / Mesh / Learning Activation

Source of truth is the **Operators** field in `unit-of-work.md`, plus the DAG in `unit-of-work-dependency.md`.
`components.md` supplies entity names and brownfield module shapes only.
Its EdgeFleetHttpApi / LearningCapture responsibility lists lag the approved operators and must not be implemented as-is.

Contract-design human gate: APPROVED 2026-08-20 by standing human approval from `@dwayne:localhost` (Lobby `$dmYc1X2PeNmUkIzAX8vh8EanGyk2c1h6nrA-9YSpcGc`). Construction kickoff may proceed. This file does not itself start Construction.

## Contracts table

| # | Provider Unit | Consumer | Mechanism | Owner |
|---|---------------|----------|-----------|-------|
| C1 | U1 fleet-hygiene | External: signed-in local operator | sync REST `DELETE /api/edge-fleet-admin/nodes/{node_id}` | U1 |
| C2 | U1 fleet-hygiene | U2 live-fleet | shared process + SQLite (`edge_fleet.db`) | U1 |
| C3 | U2 live-fleet | External: signed-in local operator | sync REST `POST /api/edge-fleet-admin/enrollments` (`issue-enrollment`) | U2 |
| C4 | U2 live-fleet | External: signed-in local operator or documented command | sync REST `POST /api/edge-fleet-admin/jobs` or store `queue_job` (`enqueue-compatible-job`) | U2 |
| C5 | U2 live-fleet | External: operator-started OpenClaw / Hermes worker (`EdgeNodeClient`) | sync REST `/api/edge-fleet/{enroll,heartbeat,lease,complete}` | U2 |
| C6 | U2 live-fleet | External: operator / docs | sync REST `GET /api/health` `edge_fleet` fragment | U2 |
| C7 | U3 learning-promotion | SharedRuntime (writer) then Learning Runtime (caller) then LearningCapture | in-process call + shared SQLite (`flight_recorder.db`, `learning_loop.db`) | U3 |
| C8 | U4 handshake | SharedRuntime (optional start) / operator inspect | in-process inspect-or-remove; no fleet token or job-queue operators | U4 |

No fifth unit. No new credential type (NFR3). No public REST for learning or mesh (`api-documentation.md`).

## Per-contract specs

### C1 — U1 operator `revoke`

Signed-in operator revokes an enrolled worker over existing admin HTTP.
U1 does not issue enrollment tokens or enqueue jobs.

```yaml
openapi: 3.0.3
info:
  title: U1 fleet-hygiene revoke
  version: brownfield
paths:
  /api/edge-fleet-admin/nodes/{node_id}:
    delete:
      operationId: revoke
      security:
        - verify_user: []
      parameters:
        - in: path
          name: node_id
          required: true
          schema: { type: string, minLength: 1, maxLength: 128 }
      responses:
        "204":
          description: Node existed or was already revoked (idempotent). Store tombstones the node and requeues that worker's leased jobs.
        "401":
          description: verify_user failed
        "403":
          description: U1 must drop the unused admin.nodes.revoke check on the local install (ADR-002 / FR1.1). After U1, a signed-in operator gets 204 or 404, not a permissions 403.
        "404":
          description: Node never existed
        "429":
          description: more than 5 revokes / 60s / principal
```

Failure: missing tailnet is enforced on node ops (C5), not as a new revoke error.
Flag-off (FR6.1) unmounts or refuses new enroll/lease; written cleanup is FR6.2, not this route.

### C2 — U1 → U2 shared fleet store

`live-fleet` depends on `fleet-hygiene` (`unit-of-work-dependency.md`).
A live worker must not enroll until revoke and the tailnet check work.
Same process, same SQLite, same routers.

```yaml
shared-schema: mindroom.edge-fleet-store/1
owner: U1
consumer: U2
database: MINDROOM_EDGE_FLEET_PATH or {storage_root}/edge_fleet.db
tables:
  edge_node:
    identifier: node_id
    required: [node_id, runtime, public_key, capabilities_json, last_seen_at]
    optional: [revoked_at]
    invariant: revoked_at IS NOT NULL refuses enroll, heartbeat, lease, complete
  edge_job:
    identifier: job_id
    required: [job_id, runtime, required_capabilities_json, payload_json, status]
    status: [queued, leased, completed]
    optional: [node_id, lease_id, lease_expires_at, result_json, result_signature]
    invariant: revoke moves that node's leased rows back to queued and clears lease columns
  edge_enrollment_nonce:
    purpose: one-time consume of issue-enrollment token
  edge_request_nonce:
    purpose: per-request attestation replay protection
  edge_fleet_audit:
    purpose: enroll/revoke/deny evidence for NFR4
fail_closed:
  - node_allowlist is None denies every enroll
  - missing or short enrollment key keeps fleet unmounted
  - missing MINDROOM_EDGE_FLEET_ENABLED keeps fleet unmounted
not_owned_by_store:
  - Tailscale probe (EdgeFleetHttpApi / TailscaleConnectivityCheck)
  - dashboard permissions
```

### C3 — U2 operator `issue-enrollment`

Brownfield path only. Construction does not invent a fifth unit or a new credential.

```yaml
openapi: 3.0.3
info:
  title: U2 issue-enrollment
  version: mindroom.edge-enrollment/1
paths:
  /api/edge-fleet-admin/enrollments:
    post:
      operationId: issue-enrollment
      security:
        - verify_user: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              title: EnrollmentIssueRequest
              type: object
              additionalProperties: false
              required: [node_id, runtime, public_key, capabilities]
              properties:
                node_id: { type: string, minLength: 1 }
                runtime: { type: string, enum: [openclaw, hermes] }
                public_key: { type: string, description: Ed25519 public key already used by brownfield enroll }
                capabilities:
                  type: array
                  minItems: 1
                  items: { type: string }
                expires_in_seconds:
                  type: integer
                  minimum: 1
                  maximum: 3600
                  default: 600
      responses:
        "200":
          description: One-time HMAC token. Cache-Control no-store.
          headers:
            Cache-Control: { schema: { type: string, enum: [no-store] } }
          content:
            application/json:
              schema:
                title: EnrollmentIssueResponse
                type: object
                required: [token, expires_at]
                properties:
                  token: { type: string, minLength: 1 }
                  expires_at: { type: string, format: date-time }
        "401":
          description: not signed in
        "422":
          description: Edge fleet request is invalid
        "429":
          description: admin limiter 120 / 60s
store_call: EdgeFleet.issue_enrollment
token_schema: mindroom.edge-enrollment/1
notes:
  - U1 does not own this operator
  - components.md omission of issue-enrollment is catalogue lag
```

### C4 — U2 operator `enqueue-compatible-job`

Signed-in operator or documented command enqueues a job the named admitted runtime can lease.
Finish is **not** process exit. Finish is the fleet accepting that worker’s attested complete for the lease (C5 `204`).

```yaml
openapi: 3.0.3
info:
  title: U2 enqueue-compatible-job
  version: brownfield
paths:
  /api/edge-fleet-admin/jobs:
    post:
      operationId: enqueue-compatible-job
      security:
        - verify_user: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              title: QueueJobRequest
              type: object
              additionalProperties: false
              required: [job_id, runtime, required_capabilities, payload]
              properties:
                job_id: { type: string, minLength: 1 }
                runtime: { type: string, enum: [openclaw, hermes] }
                required_capabilities:
                  type: array
                  items: { type: string }
                payload: { type: object }
      responses:
        "201":
          description: EdgeJobResponse; job is queued for that runtime
        "409":
          description: equivocation / conflict
        "422":
          description: invalid request
        "429":
          description: admin limiter 120 / 60s
  /api/edge-fleet-admin/jobs/{job_id}:
    get:
      operationId: inspect-job
      responses:
        "200":
          description: EdgeJobResponse status queued | leased | completed
        "404":
          description: Edge job was not found
store_call: EdgeFleet.queue_job
compatible_means:
  - runtime matches the admitted worker (openclaw or hermes)
  - required_capabilities are a subset of the enrolled node's capabilities
finish_means:
  - POST /api/edge-fleet/complete returns 204 for that lease
  - edge_job.status becomes completed
not_finish:
  - local worker process exit
  - lease acquired without complete
```

HTTP is preferred for the signed-in operator.
A documented command may call `queue_job` in-process; the store contract is the same.

### C5 — U2 worker node path (enroll → take a job → finish)

Operator-started `EdgeNodeClient` (FR2.1, FR2.5). MindRoom does not start workers.
U1 tailnet fail-closed applies on every node op (FR1.3, FR1.4, ADR-003).

```yaml
openapi: 3.0.3
info:
  title: U2 node path
  version: mindroom.edge-request/1
paths:
  /api/edge-fleet/enroll:
    post:
      requestBody:
        content:
          application/json:
            schema:
              title: EnrollmentRequest
              additionalProperties: false
              required: [token]
              properties:
                token: { type: string, minLength: 1, maxLength: 16384 }
      responses:
        "200":
          description: NodeResponse node_id, runtime, capabilities, last_seen_at
        "401":
          description: any EdgeFleetError (bad token, allowlist, replay, revoked, equivocation)
  /api/edge-fleet/heartbeat:
    post:
      parameters: &node_auth
        - { in: header, name: X-Edge-Node-ID, required: true }
        - { in: header, name: X-Edge-Timestamp, required: true }
        - { in: header, name: X-Edge-Nonce, required: true }
        - { in: header, name: X-Edge-Signature, required: true }
      requestBody:
        content:
          application/json:
            schema:
              title: HeartbeatRequest
              properties:
                capabilities:
                  type: array
                  items: { type: string }
                  maxItems: 256
      responses:
        "200": { description: NodeResponse }
        "401": { description: attestation failed }
        "409": { description: Edge fleet operation could not be completed }
  /api/edge-fleet/lease:
    post:
      parameters: *node_auth
      requestBody:
        content:
          application/json:
            schema:
              title: LeaseRequest
              properties:
                lease_seconds: { type: integer, minimum: 1, maximum: 3600, default: 60 }
      responses:
        "200":
          description: LeaseResponse job_id, lease_id, payload, expires_at — or null body when no compatible queued job
        "401": { description: attestation failed }
        "409": { description: store conflict }
  /api/edge-fleet/complete:
    post:
      parameters: *node_auth
      requestBody:
        content:
          application/json:
            schema:
              title: CompleteRequest
              required: [job_id, lease_id, lease_expires_at, result, result_signature]
              properties:
                job_id: { type: string }
                lease_id: { type: string }
                lease_expires_at: { type: string, format: date-time }
                result: { type: object }
                result_signature: { type: string, description: mindroom.edge-result/1 }
      responses:
        "204":
          description: attested complete accepted — this is FR2 "finish"
        "401": { description: attestation failed }
        "409": { description: store conflict }
preconditions:
  - tailnet check succeeds or the handler refuses the op (U1)
  - fleet flag and enrollment key valid or routers are not mounted (FR6.1)
  - worker started by documented operator command, not by MindRoom
```

### C6 — U2 activation status on health

FR5.1 / ADR-007. One sentence on health; docs quote it. Exact wording remains an open question.

```yaml
openapi: 3.0.3
info:
  title: U2 activation-status
  version: brownfield-plus-one-sentence
paths:
  /api/health:
    get:
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                required: [edge_fleet]
                properties:
                  edge_fleet:
                    oneOf:
                      - { type: object, required: [enabled], properties: { enabled: { const: false } } }
                      - { type: object, required: [enabled, healthy_nodes], properties: { enabled: { const: true }, healthy_nodes: { type: integer } } }
                      - { type: object, required: [enabled, error], properties: { enabled: { const: true }, error: { type: string } } }
                    description: >
                      U2 adds one operator-quotable activation-status sentence
                      derived from this fragment. Docs must not keep the two
                      disagreeing portfolio/security sentences as authority.
```

### C7 — U3 `LearningCapture caller`

Call path from unit Operators, not from `components.md` (`LearningCapture.dependents: []` is catalogue lag).

1. SharedRuntime / turn pipeline writes FlightRecorder after a successful visible reply.
2. Existing Learning Runtime (`learning_runtime` / `GovernedLearningRuntime`) is the named caller.
3. LearningCapture stays propose policy; it is not the hook and not a new ingest module.
4. Demo-script-only origin must not yield a candidate (FR3.2).

```yaml
shared-schema: mindroom.learning-capture-call/1
owner: U3
sequence:
  - actor: SharedRuntime
    call: record_flight_event / FlightRecorder write
    after: successful visible agent reply
    not: LearningCapture.propose
  - actor: Learning Runtime
    module: src/mindroom/learning_runtime.py
    types: [GovernedLearningRuntime, RuntimeLearningCandidateEvent]
    call: ingest / capture_runtime_learning_event → capture_learning_candidate
    after: FlightRecorder write for that run_id
  - actor: LearningCapture
    module: src/mindroom/learning_capture.py
    types: [FlightRecorderLearningCapture, LearningCandidate]
    role: propose policy only
    writes: LearningLoopStore.propose → stage proposed
databases:
  flight_recorder: "{tracking_dir}/flight_recorder.db"
  learning_loop: "{tracking_dir}/learning_loop.db"
entities:
  FlightRecord:
    identifier: record_id
    attributes: [record_id, run_id, visible_delivery]
  LearningProposal:
    identifier: proposal_id
    attributes: [proposal_id, stage, digest, reviewer_id]
    stage_after_one_visible_reply: proposed
fail_closed:
  - no FlightRecord for a successful visible delivery → no candidate
  - demo-script-only origin → no candidate (FR3.2)
  - Agno learning for the OpenClaw-named agent stays off (FR3.5)
later_in_unit_not_this_call:
  - canary both roots
  - stable both roots with named reviewer and reason
no_http: true
```

### C8 — U4 operator `inspect-openclaw`

No fleet token or job-queue operators.
Presence of FR4.2 is conditional on inspection.

```yaml
shared-schema: mindroom.handshake-inspect/1
owner: U4
operator: inspect-openclaw
input:
  - local OpenClaw install (not only the MindRoom extension point)
outcomes:
  surface_found:
    implement: bind existing handshake callable Callable[[], None]
    start: SharedRuntime starts MeshGateway when mesh enrollment switch is on (ADR-006)
    no_new_http: true
    no_general_purpose_routing: true
  surface_absent:
    implement_fr4_2: false
    remove: unread MINDROOM_MESH_ENROLLMENT switch and configuration (FR4.5)
in_process_types:
  - MeshGateway.register_worker
  - MeshEnrollmentCoordinator.admit
  - MeshEnrollmentResult status enrolled | reconnected | rejected
```

## Contract ownership rules

- The **provider unit** named in the contracts table owns the spec and any additive field.
- **Operators in `unit-of-work.md` beat `components.md`.** Construction must not drop `issue-enrollment`, `enqueue-compatible-job`, or `LearningCapture caller` because the catalogue omitted them.
- Breaking change (new credential, new route family, new unit, change to “finish”, change of LearningCapture caller) requires the human gate. Additive JSON fields are safe; consumers ignore unknown fields.
- U1 may change revoke permission behaviour and tailnet enforcement on the shared HTTP/store without waiting for U2, but must not break C2 invariants U2 relies on (tombstone, requeue, allowlist fail-closed).
- U3 does not hard-depend on U2. U4 does not hard-depend on U3. Parallel sets in `unit-of-work-dependency.md` stay valid.
- SharedRuntime appears in U1 (flag-off), U2 (health), U3 (visible-delivery write), and U4 (mesh start). That is one process, four slices. File-level serialization is a delivery-planning note (units-generation minor 3), not a missing contract.
- No intra-unit FR order is required for these contracts to be implementable (units-generation minor 2).
- Do not invent `USx.y` story IDs (units-generation minor 4). Coverage stays on FR IDs.

## Catalogue lag (do not reopen units-generation)

| Lag in `components.md` | Contract that supersedes it |
|------------------------|-----------------------------|
| EdgeFleetHttpApi responsibilities omit issue-enrollment and enqueue | C3, C4 |
| LearningCapture `dependents: []` | C7 names Learning Runtime as caller |
| SharedRuntime does not `depends_on` LearningCapture | Correct — SharedRuntime writes FlightRecorder only |
| FR2 rows in domain-design `traceability.json` pin EdgeNodeClient only | U2 operators cover token + enqueue + client path |

## Unit coverage

| Unit | Operators contracted | FR coverage | Public/external | Inter-unit |
|------|----------------------|-------------|-----------------|------------|
| U1 fleet-hygiene | `revoke` | FR1, FR6 | C1 | C2 provider |
| U2 live-fleet | `issue-enrollment`, `enqueue-compatible-job` | FR2, FR5 | C3, C4, C5, C6 | C2 consumer |
| U3 learning-promotion | `LearningCapture caller` | FR3 | none (in-process) | C7 |
| U4 handshake | `inspect-openclaw` | FR4 | none (in-process) | C8 |

Every unit in `unit-of-work.md` has at least one contract.
Every dependency edge and integration point in `unit-of-work-dependency.md` is named.
NFR1–NFR6 constrain fail-closed, no public bind, existing credentials, observability, existing tests, local install.

## Open questions

| Contract | Question | Blocks |
|----------|----------|--------|
| C6 | Exact wording of the single activation-status sentence (requirements FR5.1 still open) | U2 docs quote only — not HTTP shape |
| C8 | Whether local OpenClaw exposes a usable handshake surface | U4 implement vs remove; not U1–U3 |
| C8 | How large live mesh wiring is if FR4.2 proceeds | U4 construction sizing |
| — | SharedRuntime file ownership across U1/U3/U4 | delivery-planning serialization, not this spec |
| — | Intra-unit FR order | none for implementability |
| — | Traceability sensor `USx.y` | none — do not invent stories |

None of these make C1–C7 unimplementable.
C8 is already specified as a closed alternative (bind or remove).