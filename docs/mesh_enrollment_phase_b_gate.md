# Mesh Worker Enrollment — Phase B Human Gate

## Purpose

The real OpenClaw gateway enrollment **handshake** is an external network side
effect. Per the external side-effect rule, it is **deferred to a human-gated
Phase B**. This document records what is deferred and the approval required
before it may be enabled.

## What Phase A delivers (local, committed)

Phase A is fully local and has **no network calls**:

- `MeshWorkerIdentity` — stable per-worker identity persisted in a mode-0600
  JSON file (schema `mindroom.mesh-worker/1`). Same file across restarts ⇒ same
  `worker_id` ⇒ worker is **re-admitted**, never duplicated.
- `MeshEnrollmentAuthority` — issues/verifies short-lived HMAC-SHA256 signed
  enrollment claims (mesh sibling of `edge_fleet.EnrollmentAuthority`).
- `MeshEnrollmentRegistry` — durable SQLite worker inventory (`worker_id <-> room`
  binding, capabilities, `last_seen`, replay-nonce table), mirroring the
  `edge_fleet.EdgeFleet` table pattern.
- `MeshEnrollmentCoordinator` — orchestrates identity + authority + registry to
  admit / re-admit workers, and emits `worker_enrolled` / `worker_registered` /
  `worker_reconnected` lifecycle events.
- Gateway integration — gated behind default-OFF (`MINDROOM_MESH_ENROLLMENT` /
  coordinator present). When OFF, `MeshGateway.register_worker` is unchanged.

All of it is testable with fakes; no real OpenClaw gateway is contacted.

## What Phase B defers (external side effect)

**Deferred network call:** the real OpenClaw gateway enrollment handshake —
the network round-trip in which the OpenClaw gateway authority confirms /
binds a mesh worker's `worker_id` and public key to its enrollment.

**Where it would go:** `MeshEnrollmentCoordinator.handshake` (currently
`None`) would be bound to a real client that performs the handshake, and
`handshake_enabled` would be set to `True`.

## Hard gate

- Module constant `PHASE_B_HANDSHAKE_ENABLED = False`.
- `MeshEnrollmentCoordinator._assert_phase_b_gate()` raises if
  `handshake_enabled` is set while `PHASE_B_HANDSHAKE_ENABLED` is still
  `False`.
- A test asserts the handshake is **not** called and **no network** occurs by
  default.

## Approval checklist before enabling Phase B

Before setting `PHASE_B_HANDSHAKE_ENABLED = True` and binding a real
`handshake`:

1. **Human approval** — an authorized operator (not an agent) explicitly
   approves enabling the external OpenClaw handshake for the target gateway
   endpoint.
2. **Endpoint policy** — the OpenClaw gateway base URL must be HTTPS or
   loopback HTTP, mirroring `EdgeNodeClient` URL validation.
3. **Auth token** — enrollment to the real gateway requires the configured
   auth token (`MeshEnrollmentConfig.require_auth_token`); a token must be
   provisioned and reviewed.
4. **Registry parity** — the local SQLite registry (Phase A) must be the
   source of truth for re-admission; the real handshake only confirms the
   external binding and must not create duplicate rows locally.
5. **Credential review** — the enrollment HMAC key and worker identity private
   key handling must be reviewed (mode-0600, no logging of secrets).
6. **Rollback** — reverting to `PHASE_B_HANDSHAKE_ENABLED = False` must fully
   restore local-only behavior with no residual network calls.

No external call is performed until all of the above are satisfied.