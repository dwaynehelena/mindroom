# Mesh Cancellation Propagation — Phase B Human Gate

## Purpose

Issuing a **real worker `/cancel` RPC/HTTP call** to a **live OpenClaw worker
endpoint** is an external network side effect. Per the external side-effect
rule, it is **deferred to a human-gated Phase B**. This document records what
is deferred and the approval required before it may be enabled.

## What Phase A delivers (local, committed)

Phase A is fully local and has **no network calls**:

- `MeshCancellationPropagator` — translates a `cancel_outbox_entry` /
  `request_task_cancel` into a worker-facing cancel command
  (`MeshCancelCommand`) carrying the target worker, the originating
  `correlation_id`, the `outbox_id`, and the cancel provenance
  (`user_stop` / `sync_restart`, consistent with
  `delivery_gateway.deliver_cancelled_visible_note`). It awaits the
  acknowledgment and emits content-free lifecycle events
  `worker_cancel_requested` / `worker_cancel_acked` / `worker_cancel_failed`.
- `MeshCancelTransport` Protocol — the injectable cancel-transport surface.
  Two implementations:
  - `FakeMeshCancelTransport` (default, local, in-memory) — records issued
    commands and returns a configurable ack (success / worker-unreachable /
    delayed), so the propagation path is fully unit-testable with fakes.
  - `OpenClawMeshCancelTransport` (real HTTP, Phase B) — present for the
    wiring shape but **hard-gated**: `request_cancel` raises
    `MeshCancelPropagationError` unless the module-level
    `PHASE_B_CANCEL_RPC_ENABLED` is flipped after human approval.
- `MeshCancelRegistry` — in-flight cancel requests keyed by
  `(worker_id, correlation_id)` with a TTL, so a later ack can be correlated
  back to the originating outbox entry and stale cancels are reaped.
- `MeshGateway.cancel_outbox_entry` — when cancellation propagation is enabled
  (default-OFF via `MINDROOM_MESH_CANCEL_PROP` / a present + enabled
  `cancel_prop` coordinator), it issues the worker-facing cancel and awaits the
  acknowledgment as an **additive side effect**. When propagation is default-OFF
  (the gateway has no `cancel_prop` coordinator, or it is disabled), the
  existing `cancel_outbox_entry` behavior is byte-for-byte unchanged
  (pre-delivery outbox cancellation only).

All of it is testable with fakes; no OpenClaw worker is contacted and no real
HTTP/RPC call is made.

## What Phase B defers (external side effect)

**Deferred network call:** issuing a real worker-facing `/cancel` RPC/HTTP call
to a live OpenClaw worker endpoint — i.e. contacting `{endpoint}/cancel` (or
equivalent) on the target worker so the in-flight task on that worker is
actually cancelled.

**Where it would go:** `OpenClawMeshCancelTransport.request_cancel` performing
the real HTTP call against the worker endpoint resolved from
`WorkerHandle.endpoint` / `worker_api_endpoint(...)`, and the Phase A constant
being flipped.

## Hard gate

- Module constant `PHASE_B_CANCEL_RPC_ENABLED = False` in
  `mindroom/mesh/cancel_prop.py`.
- `MeshCancellationPropagator` defaults to `FakeMeshCancelTransport`, so the
  default local path never constructs or invokes the real HTTP transport.
- `OpenClawMeshCancelTransport.request_cancel` raises
  `MeshCancelPropagationError` ("not approved") unless
  `PHASE_B_CANCEL_RPC_ENABLED` is True — it cannot fire a network call by
  default.
- A test asserts no worker-facing cancel is issued when the flag is OFF and
  that the OpenClaw cancel transport is unreachable / Phase B gated.
- Phase A propagation touches only the fake/injected transport, the in-memory
  registry, the outbox state-machine and the lifecycle sink — never a live
  worker.

## Phase B Unit 4 — /cancel RPC body implemented (gated, DEFERRED for missing surface)

The OpenClaw `/cancel` RPC **request body and transport logic are now fully
implemented** in `OpenClawMeshCancelTransport` and are verified locally against
a documented loopback fake gateway — the primary path per the operator's
instruction.  The real network side effect remains hard-gated behind
`PHASE_B_CANCEL_RPC_ENABLED` (still `False`).

What Unit 4 adds:

- `build_cancel_body(command)` — constructs the documented request body
  `{worker_id, correlation_id, cancel_source, outbox_id}`.
- `parse_ack(command, status, payload)` — maps a 2xx ack / 2xx unacknowledged /
  non-2xx error into a `MeshCancelAck` or raises `MeshCancelPropagationError`.
- `request_cancel(command)` — POSTs the body to `{endpoint}/cancel` (injectable
  transport, default urllib via `asyncio.to_thread`), parses the response, and
  surfaces error/timeout conditions.  Endpoint policy is HTTPS or loopback HTTP
  (mirrors the edge-node rule).
- A documented loopback fake gateway
  (`scripts/testing/mesh_phaseb_cancel_fake_gateway.py`) exposes the exact
  `POST /cancel` route so the body + transport + response parsing + error/
  timeout + registry integration are fully testable locally with **no real
  OpenClaw gateway**.  New tests: `tests/test_mesh_cancel_rpc.py` (20).
- A live gate probe (`scripts/testing/mesh_phaseb_cancel_gate_probe.py`) probes
  the real gateway for a `/cancel` route.

### Gate re-run (Unit 4): **DEFERRED-for-missing-external-surface**

`PHASE_B_CANCEL_RPC_ENABLED` remains `False`.  The real OpenClaw gateway on
port `18789` was probed and **lacks the `/cancel` route**:

```
GET  / -> HTTP 200          (SPA catch-all, returns HTML)
POST /cancel              -> HTTP 404
POST /api/cancel          -> HTTP 404
POST /api/worker/cancel   -> HTTP 404
POST /workers/cancel      -> HTTP 404
POST /api/v1/cancel       -> HTTP 404
```

The gateway serves an SPA that swallows `GET` paths but returns `404 Not Found`
for `POST /cancel` (and every plausible variant), so no live `/cancel` route
and no live ack smoke are available.  Per the operator's instruction ("flag
stays off in that case"), the unit is **deferred for the missing external
surface** and the flag is **not flipped**.

## Approval checklist before enabling Phase B

Before flipping `PHASE_B_CANCEL_RPC_ENABLED = True` and binding a real
OpenClaw cancel transport:

1. **A real `/cancel` route exists** — the live OpenClaw gateway must expose
   `POST /cancel` (verified by `scripts/testing/mesh_phaseb_cancel_gate_probe.py`
   returning GO), and a live smoke must pass.
2. **Human approval** — an authorized operator (not an agent) explicitly
   approves issuing real `/cancel` RPC/HTTP calls to the target worker fleet.
3. **Endpoint policy** — each worker `endpoint` must be HTTPS or loopback HTTP,
   mirroring existing URL validation, and must be a registered mesh worker.
4. **Token/auth** — issuing a real cancel requires authenticated credentials
   (`auth_token`) with permission to cancel the target worker's task; they must
   be provisioned and reviewed, and never logged.
5. **Correlation parity** — the Phase A `MeshCancelRegistry` must remain the
   source of truth for `(worker_id, correlation_id) -> outbox_id`; the real
   client only issues the wire cancel and must not fabricate correlation IDs or
   cancel unrelated outbox entries.
6. **Idempotency** — a worker that has already finished the cancelled task must
   not error spuriously; the Phase A terminal `cancelled` outbox state + saved
   `cancel_source` remain authoritative.
7. **Rollback** — reverting to `PHASE_B_CANCEL_RPC_ENABLED = False` must fully
   restore local-only propagation (fake transport) with no residual network
   calls.

No external call is performed until the route exists and all of the above are
satisfied.