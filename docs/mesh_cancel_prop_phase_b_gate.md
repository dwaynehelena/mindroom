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

## Approval checklist before enabling Phase B

Before flipping `PHASE_B_CANCEL_RPC_ENABLED = True` and binding a real
OpenClaw cancel transport:

1. **Human approval** — an authorized operator (not an agent) explicitly
   approves issuing real `/cancel` RPC/HTTP calls to the target worker fleet.
2. **Endpoint policy** — each worker `endpoint` must be HTTPS or loopback HTTP,
   mirroring existing URL validation, and must be a registered mesh worker.
3. **Token/auth** — issuing a real cancel requires authenticated credentials
   (`auth_token`) with permission to cancel the target worker's task; they must
   be provisioned and reviewed, and never logged.
4. **Correlation parity** — the Phase A `MeshCancelRegistry` must remain the
   source of truth for `(worker_id, correlation_id) -> outbox_id`; the real
   client only issues the wire cancel and must not fabricate correlation IDs or
   cancel unrelated outbox entries.
5. **Idempotency** — a worker that has already finished the cancelled task must
   not error spuriously; the Phase A terminal `cancelled` outbox state + saved
   `cancel_source` remain authoritative.
6. **Rollback** — reverting to `PHASE_B_CANCEL_RPC_ENABLED = False` must fully
   restore local-only propagation (fake transport) with no residual network
   calls.

No external call is performed until all of the above are satisfied.