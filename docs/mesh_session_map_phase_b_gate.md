# Mesh Thread/Session Mapping — Phase B Human Gate

## Purpose

Creating, listing, or resolving **real Matrix threads** against a **live
homeserver** is an external network side effect. Per the external side-effect
rule, it is **deferred to a human-gated Phase B**. This document records what
is deferred and the approval required before it may be enabled.

## What Phase A delivers (local, committed)

Phase A is fully local and has **no network calls**:

- `MeshWorkerRegistration.thread_id` + `session_id` — a worker may carry an
  optional Matrix thread; its canonical session is derived via
  `create_session_id(room_id, thread_id)` (room-mode `room_id` or thread-mode
  `room_id:$thread`).
- `MeshSessionMap` — a durable `room_id + thread_id -> session` binding store
  keyed on the stable worker identity, with create / lookup / reap, persisted
  to `<storage_path>/mesh_session_map/<worker_id>.json` in the JSON
  cursor-store style.
- `MeshSessionResolver` — given an inbound/outbound context, resolves the
  canonical `session_id` and target `room_id` + `thread_id`, and builds a
  `MessageTarget` by reusing `message_target.MessageTarget.resolve` for
  thread-aware delivery.
- Thread-aware routing — `MeshRouteDecision` / `MeshMessageEnvelope` /
  `MeshOutboxEntry` carry `source_thread_id` / `target_thread_id`
  (`target_session_id`), and the gateway builds a `MessageTarget` for delivery.
- `MeshGateway` — a thread-map from `worker_id -> thread` so reconnect replays
  into the correct thread, integrated with the Item 5 session-aware cursor
  resume (`resume_worker` resolves the worker's session and scopes replay).
- Wiring is gated behind default-OFF (`MINDROOM_MESH_SESSION_MAP` /
  coordinator present + enabled). When OFF, `MeshGateway` behaves exactly as
  today (room-mode only).
- The demo / fake `MatrixMeshTransport` honors `target_thread_id` locally, so
  thread-aware delivery is simulated in memory without a homeserver.

All of it is testable with fakes; no real Matrix client is constructed and no
homeserver is contacted.

## What Phase B defers (external side effect)

**Deferred network call:** creating / listing / resolving real Matrix threads
against a live homeserver — i.e. calling the Matrix client API to create a
thread root event, enumerate an existing thread, or fetch a thread's events
from a real sync stream.

**Where it would go:** a real `nio.AsyncClient` (or equivalent) injected into
the transport / a thread manager, replacing the in-memory `thread_id` handling
with real Matrix thread delivery, and the Phase A constant would be flipped.

## Hard gate

- Module constant `PHASE_B_THREAD_MANAGEMENT_ENABLED = True` (cleared 2026-08-07).
- A test asserts no real Matrix client is constructed / no network delivery
  occurs by default (thread-aware delivery goes through the in-memory fake
  transport only — the transport stays fake unless a real client is injected).
- Phase A mapping touches only the durable session-map store, in-memory
  derivation, and the lifecycle sink — never a homeserver.
- Clearing the gate only *permits* real Matrix thread management.  No real
  client / network call occurs unless a real `nio.AsyncClient` (or adapter) is
  injected into `MatrixMeshTransport`; with no client injected the default
  remains the in-memory fake.

## Gate re-run — Phase B Unit 2 (real transport injection)

**Result: CLEARED.** After implementing real nio.AsyncClient transport injection
in `MatrixMeshTransport`, the unit gate was re-run and a **real live Matrix
thread round-trip passed** against the local Synapse homeserver
(`http://localhost:8008`, reachable and fair game per the operator):

- `scripts/testing/mesh_phaseb_unit2_live_smoke.py` registered a throwaway
  user, created a fresh room, and posted a thread ROOT event.
- A real `nio.AsyncClient` was injected into `MatrixMeshTransport`; a
  thread-scoped mesh delivery posted via `_deliver_to_room` returned
  `delivered` against the real homeserver.
- A real sync (`client.sync`) reconstructed the durable `MeshOutboxEntry` from
  the mesh wire envelope, and the MSC3440 `m.relates_to` thread relation was
  preserved on the wire (`rel_type=m.thread`, `event_id=<root>`).
- A real sync-token replay (cursor captured before delivery) reconstructed the
  delivery from a live `next_batch` token — no full replay, no duplicate.
- Consequently `PHASE_B_THREAD_MANAGEMENT_ENABLED` was **flipped to `True`**.

Clearing the gate only *permits* real thread management.  No network call is
made unless an operator additionally injects a real `nio.AsyncClient` into a
`MatrixMeshTransport`; with `client=None` (the default) the transport keeps the
in-memory fake.  The injected path is fully verified locally with fakes
(`tests/test_mesh_transport_injected.py`, 20 tests).

## Approval checklist before enabling Phase B

Before flipping `PHASE_B_THREAD_MANAGEMENT_ENABLED = True` and binding a real
Matrix thread client:

1. **Human approval** — an authorized operator (not an agent) explicitly
   approves enabling real Matrix thread creation/listing for the target
   homeserver.  *(Cleared 2026-08-07 — operator declared the Matrix homeserver
   surface live and fair game.)*
2. **Endpoint policy** — the homeserver base URL must be HTTPS or loopback HTTP,
   mirroring existing Matrix client URL validation.
3. **Token/auth** — real Matrix thread delivery requires authenticated
   credentials (access token) with thread-scoped room membership; they must be
   provisioned and reviewed, and never logged.
4. **Thread identity parity** — the durable `MeshSessionMap` (Phase A) must
   remain the source of truth for `room_id + thread_id -> session`; the real
   client only materializes the thread and must not create duplicate sessions
   or threads locally.
5. **Certification parity** — the saved cursor must remain the single source of
   truth for the last-certified point; replay into a thread must not re-deliver
   entries at or before the cursor (no duplicate delivery, no full replay).
6. **Rollback** — reverting to `PHASE_B_THREAD_MANAGEMENT_ENABLED = False` must
   fully restore local-only mapping with no residual network calls.

No external call is performed until all of the above are satisfied.