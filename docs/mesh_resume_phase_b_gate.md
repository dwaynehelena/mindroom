# Mesh Cursor Resume — Phase B Human Gate

## Purpose

Replaying undelivered mesh outbox entries from a **real Matrix sync token**
against a **live homeserver** is an external network side effect. Per the
external side-effect rule, it is **deferred to a human-gated Phase B**. This
document records what is deferred and the approval required before it may be
enabled.

## What Phase A delivers (local, committed)

Phase A is fully local and has **no network calls**:

- `MeshReconnectCursor` v2 — durable reconnect checkpoint with an optional
  `session_id`. `from_json` accepts both the current v2 record and the legacy
  v1 record (back-compat migration).
- `MeshReconnectCoordinator` — orchestrates a worker resume: load cursor ->
  `transport._sync_from_cursor` -> replay undelivered outbox entries -> save an
  advanced cursor. Idempotent (entries already delivered are skipped, no
  duplicate delivery, no full replay) and session-aware.
- `MatrixMeshTransport._sync_from_cursor` — real local replay against the fake /
  in-memory delivery queue (previously a stub returning `()`).
- `MeshGateway.resume_worker(worker_id, *, session_id)` — gateway entry point
  that runs the coordinator and emits `worker_reconnected`.
- Wiring is gated behind default-OFF (`MINDROOM_MESH_RESUME` / coordinator
  present + enabled). When OFF, `MeshGateway` behaves exactly as today.

All of it is testable with fakes; no real homeserver is contacted.

## What Phase B defers (external side effect)

**Deferred network call:** replaying from a real Matrix sync token against a
live homeserver — i.e. `MatrixMeshTransport._sync_from_cursor` fetching events
from a real Matrix sync stream using `cursor.cursor` as the sync token. This is
an external round-trip to a homeserver.

**Where it would go:** `MatrixMeshTransport._sync_from_cursor` (currently a
local in-memory replay) would be replaced by a real Matrix sync client, and the
Phase A constant would be flipped.

## Hard gate

- Module constant `PHASE_B_RESUME_ENABLED = False` in
  `mindroom/mesh/reconnect.py`.
- A test asserts `_deliver_to_room` (the wire-level delivery path) is **not**
  invoked during a local resume, i.e. no network delivery occurs by default.
- Phase A replay touches only the cursor store, the in-memory transport log,
  and the lifecycle sink — never a homeserver.

## Approval checklist before enabling Phase B

Before flipping `PHASE_B_RESUME_ENABLED = True` and binding a real Matrix sync
replay:

1. **Human approval** — an authorized operator (not an agent) explicitly
   approves enabling external sync-token replay for the target homeserver.
2. **Endpoint policy** — the homeserver base URL must be HTTPS or loopback HTTP,
   mirroring existing Matrix client URL validation.
3. **Token/auth** — real Matrix sync requires authenticated credentials (access
   token); they must be provisioned and reviewed, and never logged.
4. **Certification parity** — the saved cursor must remain the single source of
   truth for the last-certified point; the real replay must not re-deliver
   entries at or before the cursor (no duplicate delivery, no full replay).
5. **Rollback** — reverting to `PHASE_B_RESUME_ENABLED = False` must fully
   restore local-only replay with no residual network calls.

No external call is performed until all of the above are satisfied.