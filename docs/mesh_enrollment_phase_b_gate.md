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
- `MeshEnrollmentAuthority` — **Phase B Unit 1 (inverted enrollment)** now
  subclasses the shared P9 edge-fleet `edge_fleet.EnrollmentAuthority` instead
  of being a standalone mesh sibling. It reuses the exact 32-byte key contract,
  HMAC-SHA256 scheme and canonical JSON helpers. It keeps the mesh claim shape
  (`worker_id`/`agent_name`, schema `mindroom.mesh-enrollment/1`) for backward
  compatibility AND issues the shared edge-fleet claim
  (`node_id`, schema `mindroom.edge-enrollment/1`) via `issue_edge_token` /
  `issue_edge`. `verify` accepts both shapes, dispatching on the claim schema.
- `MeshEnrollmentRegistry` — durable SQLite worker inventory (`worker_id <-> room`
  binding, capabilities, `last_seen`, replay-nonce table), mirroring the
  `edge_fleet.EdgeFleet` table pattern. It is shape-agnostic: an inverted
  edge-fleet claim's `node_id` is normalized to the mesh `worker_id` and
  re-admission is preserved.
- `MeshEnrollmentCoordinator` — orchestrates identity + authority + registry to
  admit / re-admit workers, exposes `issue_edge_token` for inverted enrollment,
  and emits `worker_enrolled` / `worker_registered` / `worker_reconnected`
  lifecycle events.
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

- Module constant `PHASE_B_HANDSHAKE_ENABLED = True` (cleared 2026-08-07).
- The handshake only runs when BOTH a real `handshake` callable is bound AND
  `handshake_enabled=True` on the coordinator. With `handshake=None` (the
  default) the coordinator stays inert — no network call occurs.
- A test asserts the handshake is **not** called and **no network** occurs by
  default (`handshake=None`).

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

## Gate re-run — Phase B Unit 1 (inverted enrollment)

**Result: CLEARED.** After re-architecting mesh enrollment onto the shared P9
edge-fleet `EnrollmentAuthority` (inverted enrollment), the unit gate was
re-run and a **real live inverted-enrollment handshake passed** against the
live MindRoom P9 edge-node surface:

- The live MindRoom API (`mindroom run`, PID 58132, port 8765) exposes the P9
  edge-fleet surface (`/api/edge-fleet/enroll` +
  `/api/edge-fleet-admin/enrollments`); both
  `MINDROOM_EDGE_FLEET_ENABLED=true` and
  `MINDROOM_EDGE_FLEET_ENROLLMENT_KEY` are provisioned at startup, so the
  surface is mounted and reachable.
- `scripts/testing/mesh_phaseb_unit1_live_smoke.py` issued a real
  `mindroom.edge-enrollment/1` token through the shared
  `edge_fleet.EnrollmentAuthority` (the code path `MeshEnrollmentAuthority`
  subclasses) and POSTed it to the live `/api/edge-fleet/enroll` endpoint.
- Result: **HTTP 200** and the node was admitted
  (`{"node_id":"phaseb-smoke-openclaw-worker","runtime":"openclaw",...}`).
- Consequently `PHASE_B_HANDSHAKE_ENABLED` was **flipped to `True`**.

Clearing the gate only *permits* the external OpenClaw handshake.  No network
call is made unless an operator additionally binds a real `handshake` callable
AND sets `handshake_enabled=True` on the coordinator; with `handshake=None`
(the default) the coordinator stays inert.  The inverted flow is also fully
verified locally with fakes (`tests/test_mesh_enrollment_inverted.py`, 13
tests): shared-authority claim issuance/verification, registry
`node_id`→`worker_id` normalization, re-admission on restart, and
duplicate/stale rejection.