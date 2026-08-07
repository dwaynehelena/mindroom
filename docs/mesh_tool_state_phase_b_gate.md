# Mesh Tool-State Streaming — Phase B Human Gate

## Purpose

Actually **posting/editing streaming edits into a live Matrix room** via
`delivery_gateway` (streaming worker tool-state into a real room/thread) is an
external network side effect. Per the external side-effect rule, it is
**deferred to a human-gated Phase B**. This document records what is deferred
and the approval required before it may be enabled.

## What Phase A delivers (local, committed)

Phase A is fully local and has **no network calls**:

- `MeshToolStateForwarder` — consumes a worker's tool start/completed events
  and produces `StructuredStreamChunk`-compatible tool traces (reusing
  `ToolTraceEntry` / `StructuredStreamChunk` shapes). It applies per-session
  sequence numbering so a reconnecting observer can skip already-seen state,
  and redacts tool results unless `include_results` is explicitly enabled.
- `MeshToolStateSink` Protocol — the injectable sink surface. Two
  implementations:
  - `MatrixToolStateSink` (default, local) — posts tool-state into the worker's
    thread via an injected `MeshTransport` / fake; depends only on
    `MeshTransport`, never `nio`. Fully unit-testable with fakes.
  - `NullToolStateSink` (default no-op) — used in gateway-only mode when no
    sink is configured.
- `MeshToolStateCoordinator` — owns per-session forwarders so tool-state flows
  into the correct mapped thread (integrating Item 2 thread/session mapping)
  and can resume across reconnects (integrating Item 5 session-aware resume via
  per-session sequencing).
- `MeshToolStateObserver` — a reconnecting observer that skips tool-state
  deltas already seen for a session.
- `MeshGateway.stream_tool_start` / `stream_tool_complete` — when tool-state
  streaming is enabled (default-OFF via `MINDROOM_MESH_TOOL_STREAM` / a present
  + enabled `tool_state` coordinator), they resolve the worker's canonical
  session/thread (Item 2 mapping) and forward the normalized, sequenced,
  redacted trace into the correct thread as an **additive side effect**. When
  default-OFF, the gateway's behavior is byte-for-byte unchanged.
- Content-free `tool_state_streamed` lifecycle events carry only a count and no
  message/tool payload (privacy invariant).

All of it is testable with fakes; no real Matrix room/thread is contacted and
no live streaming edit is made.

## What Phase B defers (external side effect)

**Deferred network call:** actually posting/editing *streaming edits* into a
live Matrix room — i.e. calling `delivery_gateway.deliver_stream` /
`StreamingDeliveryRequest` (or equivalent) to push worker tool-state into a
real room/thread on a live homeserver.

**Where it would go:** `MatrixToolStateSink.forward` performing a real
`deliver_stream` call against the target `MessageTarget` (from Item 2 mapping),
and the Phase A constant being flipped.

## Hard gate

- Module constant `PHASE_B_TOOL_STREAM_POSTING_ENABLED = False` in
  `mindroom/mesh/tool_state.py`.
- `MeshToolStateCoordinator` defaults to `NullToolStateSink`, so the default
  local path never constructs or invokes a real posting sink.
- `MatrixToolStateSink` records into a thread-scoped in-memory log through the
  injected transport/fake — it never constructs an `nio.AsyncClient` or makes a
  network call.
- A test asserts no tool-state is forwarded when the flag is OFF and that the
  matrix sink depends only on `MeshTransport`.
- Phase A forwarding touches only the fake/injected transport, the in-memory
  sink log, the per-session sequencer, the outbox lifecycle sink — never a live
  Matrix room.

## Approval checklist before enabling Phase B

Before flipping `PHASE_B_TOOL_STREAM_POSTING_ENABLED = True` and binding a real
streaming-posting sink:

1. **Human approval** — an authorized operator (not an agent) explicitly
   approves posting/editing streaming tool-state edits into live rooms.
2. **Delivery policy** — posting must go through `delivery_gateway.deliver_stream`
   with a `MessageTarget` resolved from Item 2 mapping; it must not bypass
   existing delivery/mention/redaction rules.
3. **Rate / edit budget** — streaming edits must respect the existing streaming
   throttle and oversized-edit fallbacks; tool-state must not spam rooms.
4. **Privacy** — tool-state metadata (`io.mindroom.tool_trace`) may flow, but
   lifecycle events must stay content-free; message-content bodies must never
   leak into the content-free lifecycle.
5. **Rollback** — reverting to `PHASE_B_TOOL_STREAM_POSTING_ENABLED = False`
   must fully restore local-only forwarding (null sink) with no residual
   network calls.

### Separate gate: `include_results=True`

Forwarding tool **results** (`include_results=True`) is a distinct,
privacy-relevant leak that requires its own human approval in addition to the
posting gate:

1. **Human approval** — an authorized operator explicitly approves
   `include_results=True` (flipping `INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED`).
2. **Default OFF** — `include_results=False` is the default and never leaks
   tool results; results are redacted (stripped) on every forwarded trace.
3. **Per-sink review** — enabling results must be reviewed for the target
   rooms/threads before forwarding begins.
4. **Rollback** — disabling must immediately stop forwarding results.

No external call is performed until all of the above are satisfied.