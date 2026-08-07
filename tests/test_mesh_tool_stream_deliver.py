"""Tests for Phase B Unit 3 — binding delivery_gateway.deliver_stream into the mesh tool-state sink.

Covers:
- ``MatrixToolStateSink`` schedules a real ``StreamingDeliveryRequest`` per delta when a
  ``deliver_stream`` callable (mock/fake, no network) is injected.
- The request targets the ``MessageTarget`` resolved from the worker's mapped session/thread.
- The streamed chunk carries the normalized, redacted tool trace (content-free lifecycle kept:
  no tool bodies leak into lifecycle outcomes).
- Backwards compatibility: with no ``deliver_stream`` bound the sink stays fully local.
- The binding is gated by ``PHASE_B_TOOL_STREAM_POSTING_ENABLED``.
- The separate results-leak gate ``INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED`` stays False.
"""

# ruff: noqa: ANN001, ANN201, ANN202, D103

from __future__ import annotations

import asyncio

import pytest
from agno.models.response import ToolExecution

from mindroom.mesh import (
    INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED,
    PHASE_B_TOOL_STREAM_POSTING_ENABLED,
    GatewayExecutionGate,
    GatewayRuntimeMode,
    MatrixMeshTransport,
    MatrixToolStateSink,
    MeshCursorStore,
    MeshGateway,
    MeshSessionMap,
    MeshSessionMappingCoordinator,
    MeshToolStateCoordinator,
    MeshWorkerRegistration,
)
from mindroom.message_target import MessageTarget

ROOM = "!alpha:localhost"
BETA_ROOM = "!beta:localhost"
ALPHA_THREAD = "$thread-alpha"
BETA_THREAD = "$thread-beta"


def _tool(*, name="read_file", args=None, result=None):
    return ToolExecution(
        tool_name=name,
        tool_args=dict(args or {}),
        result=result,
    )


def _store():
    return MeshCursorStore()


def _transport(store):
    return MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")


def _recording_deliver_stream():
    """Return (deliver_stream fake, records list, collector list).

    The fake consumes each request's stream (like the real streaming machinery),
    records the consumed ``StructuredStreamChunk``s, and feeds the shared
    ``tool_trace_collector`` with the streamed tool traces.
    """
    records: list[dict] = []
    collector: list = []

    async def _deliver(request):
        chunks = [chunk async for chunk in request.response_stream]
        if request.tool_trace_collector is not None:
            for chunk in chunks:
                for trace in chunk.tool_trace or ():
                    request.tool_trace_collector.append(trace)
        records.append({"request": request, "chunks": chunks})

    return _deliver, records, collector


def _recording_gateway(sink, *, session_threads=(ALPHA_THREAD, BETA_THREAD)):
    """Build a gateway wired to a recording sink."""
    store = _store()
    transport = _transport(store)
    coordinator = MeshSessionMappingCoordinator(
        session_map=MeshSessionMap(),
        enabled=True,
    )
    tool_coordinator = MeshToolStateCoordinator(
        sink_factory=lambda: sink,
        enabled=True,
    )
    gw = MeshGateway(
        transport=transport,
        cursor_store=store,
        execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
        gateway_room_id="!gw:localhost",
        session_mapping=coordinator,
        tool_state=tool_coordinator,
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id=ROOM, thread_id=session_threads[0]),
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="beta", agent_name="b", room_id=BETA_ROOM, thread_id=session_threads[1]),
    )
    return gw


def _default_resolver():
    """Return the default MessageTarget resolver for a chunk."""

    def _resolve(chunk):
        return MessageTarget.resolve(
            room_id=chunk.room_id,
            thread_id=chunk.thread_id,
            reply_to_event_id=None,
            room_mode=chunk.thread_id is None,
        )

    return _resolve


# ── deliver_stream binding (mock, no network) ────────────────────────────


@pytest.mark.asyncio
async def test_sink_schedules_streaming_request_per_delta():
    deliver, records, _collector = _recording_deliver_stream()
    sink = MatrixToolStateSink(
        _transport(_store()),
        deliver_stream=deliver,
        target_resolver=_default_resolver(),
    )
    gw = _recording_gateway(sink)
    gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
    gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "a.txt"}, result="done"))
    gw.stream_tool_start("beta", _tool(name="grep", args={"pattern": "x"}))

    # Let the scheduled tasks drain.
    await asyncio.sleep(0)

    requests = [r["request"] for r in records]
    assert len(requests) == 3
    # One per delta, in forwarding order.
    assert [r.target.room_id for r in requests] == [ROOM, ROOM, BETA_ROOM]
    assert [r.target.resolved_thread_id for r in requests] == [ALPHA_THREAD, ALPHA_THREAD, BETA_THREAD]


@pytest.mark.asyncio
async def test_streaming_request_target_matches_mapped_session():
    deliver, records, _collector = _recording_deliver_stream()
    sink = MatrixToolStateSink(
        _transport(_store()),
        deliver_stream=deliver,
        target_resolver=_default_resolver(),
    )
    gw = _recording_gateway(sink)
    chunk = gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
    await asyncio.sleep(0)

    req = records[0]["request"]
    assert req.target.room_id == chunk.room_id
    assert req.target.resolved_thread_id == chunk.thread_id
    assert req.target.session_id == f"{ROOM}:{ALPHA_THREAD}"


@pytest.mark.asyncio
async def test_streamed_chunk_carries_redacted_tool_trace():
    deliver, records, _collector = _recording_deliver_stream()
    sink = MatrixToolStateSink(
        _transport(_store()),
        deliver_stream=deliver,
        target_resolver=_default_resolver(),
    )
    gw = _recording_gateway(sink)
    gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "secret.txt"}, result="SECRET body"))
    await asyncio.sleep(0)

    record = records[0]
    assert record["request"].target.room_id == ROOM
    # Single structured chunk carrying the trace (consumed by the fake).
    chunks = record["chunks"]
    assert len(chunks) == 1
    trace = chunks[0].tool_trace[0]
    assert trace.tool_name == "read_file"
    assert trace.result_preview is None  # redacted (results leak gate is False)
    assert trace.args_preview is not None  # args preview kept
    # Content-free lifecycle: no tool bodies in lifecycle outcomes.
    serialized = str(gw.lifecycle_events) + str(gw.lifecycle_outcomes)
    assert "SECRET body" not in serialized
    assert "secret.txt" not in serialized


@pytest.mark.asyncio
async def test_collector_records_streamed_tool_trace():
    deliver, _records, collector = _recording_deliver_stream()
    sink = MatrixToolStateSink(
        _transport(_store()),
        deliver_stream=deliver,
        target_resolver=_default_resolver(),
        tool_trace_collector=collector,
    )
    gw = _recording_gateway(sink)
    gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
    gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "a.txt"}, result="done"))
    await asyncio.sleep(0)

    # The shared collector was fed by the streaming responses.
    names = [t.tool_name for t in collector]
    assert "read_file" in names


# ── Backwards compatibility ──────────────────────────────────────────────


def test_no_deliver_stream_stays_local():
    """With no deliver_stream bound, the sink stays fully local (no requests)."""
    sink = MatrixToolStateSink(_transport(_store()))
    gw = _recording_gateway(sink)
    gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
    assert len(sink.streamed_requests()) == 0
    assert len(sink.all_posted()) == 1  # still recorded locally


@pytest.mark.asyncio
async def test_deliver_stream_binding_still_records_locally():
    """Even when bound, the sink still records the local thread-scoped log."""
    deliver, records, _collector = _recording_deliver_stream()
    sink = MatrixToolStateSink(
        _transport(_store()),
        deliver_stream=deliver,
        target_resolver=_default_resolver(),
    )
    gw = _recording_gateway(sink)
    gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
    await asyncio.sleep(0)
    assert len(sink.posted_chunks(ALPHA_THREAD)) == 1
    assert len(records) == 1


# ── Gating ───────────────────────────────────────────────────────────────


def test_posting_gate_constant_is_cleared():
    """The real live-room posting gate is CLEARED (live round-trip verified)."""
    assert PHASE_B_TOOL_STREAM_POSTING_ENABLED is True


def test_results_leak_gate_constant_remains_false():
    """The separate tool-results leak gate stays False."""
    assert INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED is False
