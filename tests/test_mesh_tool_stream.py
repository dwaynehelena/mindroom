"""Tests for Item 3: streaming worker tool-state forwarding into MindRoom threads (Phase A, local only).

Covers:
- In-order delta forwarding from a worker's tool start/completed events.
- Thread-target correctness (uses Item 2 session/thread mapping to know which
  thread to forward into).
- Per-session sequencing + skip-on-reconnect (a reconnecting observer skips
  already-seen state).
- Clean teardown using the Item 4 cancellation primitive (tool-state streaming
  is inert on the default path and does not leak cancellation behavior).
- Content-free lifecycle preserved (no bodies leak into lifecycle outcomes).
- include_results=False redaction (tool results never leak by default).
- Default-OFF no-op path (gateway behavior unchanged when ``tool_state`` absent).
- No real Matrix client / no network (Phase B live-room posting is gated).
"""

# ruff: noqa: ANN001, ANN201, ANN202, D101, D102, RUF059

from __future__ import annotations

import pytest
from agno.models.response import ToolExecution

from mindroom.mesh import (
    INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED,
    MESH_TOOL_STREAM_ENV,
    PHASE_B_TOOL_STREAM_POSTING_ENABLED,
    GatewayExecutionGate,
    GatewayRuntimeMode,
    MatrixMeshTransport,
    MatrixToolStateSink,
    MeshCursorStore,
    MeshGateway,
    MeshMessage,
    MeshSessionMap,
    MeshSessionMappingCoordinator,
    MeshToolStateChunk,
    MeshToolStateCoordinator,
    MeshToolStateError,
    MeshToolStateForwarder,
    MeshToolStateObserver,
    MeshWorkerRegistration,
    NullToolStateSink,
    tool_state_flag_enabled,
)
from mindroom.mesh.tool_state import MeshToolStateSink
from mindroom.tool_system.events import StructuredStreamChunk, ToolTraceEntry

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


def _tool_gateway(store=None, transport=None, *, session_threads=(ALPHA_THREAD, BETA_THREAD), sink_factory=None, enabled=True):
    store = store or _store()
    transport = transport or _transport(store)
    coordinator = MeshSessionMappingCoordinator(
        session_map=MeshSessionMap(),
        enabled=True,
    )
    tool_coordinator = MeshToolStateCoordinator(
        sink_factory=sink_factory,
        enabled=enabled,
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
    return gw, store, transport, tool_coordinator


# ── Flag / default-OFF ───────────────────────────────────────────────────


class TestToolStateFlag:
    def test_flag_default_off(self):
        assert tool_state_flag_enabled({}) is False
        assert tool_state_flag_enabled({MESH_TOOL_STREAM_ENV: "0"}) is False
        assert tool_state_flag_enabled({MESH_TOOL_STREAM_ENV: "off"}) is False

    def test_flag_truthy(self):
        for value in ("1", "true", "yes", "on"):
            assert tool_state_flag_enabled({MESH_TOOL_STREAM_ENV: value}) is True, value


# ── In-order delta forwarding ────────────────────────────────────────────


class TestForwarderInOrder:
    def test_forwarder_produces_structured_chunks_in_order(self):
        sink = NullToolStateSink()
        fwd = MeshToolStateForwarder(
            worker_id="alpha",
            session_id=f"{ROOM}:{ALPHA_THREAD}",
            room_id=ROOM,
            thread_id=ALPHA_THREAD,
            sink=sink,
        )
        c1 = fwd.on_tool_start(_tool(name="read_file", args={"path": "a.txt"}))
        c2 = fwd.on_tool_complete(_tool(name="read_file", args={"path": "a.txt"}, result="done"))
        c3 = fwd.on_tool_start(_tool(name="grep", args={"pattern": "x"}))

        assert [c.sequence for c in (c1, c2, c3)] == [1, 2, 3]
        assert c1.trace.type == "tool_call_started"
        assert c2.trace.type == "tool_call_completed"
        assert c3.trace.type == "tool_call_started"
        assert fwd.current_sequence == 3

    def test_forwarder_emits_structured_stream_chunk_shape(self):
        sink = NullToolStateSink()
        fwd = MeshToolStateForwarder(
            worker_id="alpha",
            session_id=f"{ROOM}:{ALPHA_THREAD}",
            room_id=ROOM,
            thread_id=ALPHA_THREAD,
            sink=sink,
        )
        chunk = fwd.on_tool_start(_tool(name="read_file", args={"path": "a.txt"}))
        structured = chunk.as_structured_chunk()
        assert isinstance(structured, StructuredStreamChunk)
        assert isinstance(structured.tool_trace, list)
        assert isinstance(structured.tool_trace[0], ToolTraceEntry)
        assert structured.tool_trace[0].tool_name == "read_file"

    def test_forwarder_none_tool_is_noop(self):
        sink = NullToolStateSink()
        fwd = MeshToolStateForwarder(
            worker_id="alpha",
            session_id=f"{ROOM}:{ALPHA_THREAD}",
            room_id=ROOM,
            thread_id=ALPHA_THREAD,
            sink=sink,
        )
        assert fwd.on_tool_start(None) is None
        assert fwd.on_tool_complete(None) is None
        assert fwd.current_sequence == 0


# ── Thread-target correctness (Item 2 mapping) ───────────────────────────


class TestThreadTarget:
    def test_forwarded_chunks_carry_mapped_thread(self):
        sink = MatrixToolStateSink(_transport(_store()))
        gw, _, _, _ = _tool_gateway(sink_factory=lambda: sink)
        gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
        gw.stream_tool_start("beta", _tool(name="grep", args={"pattern": "x"}))

        alpha_chunks = sink.posted_chunks(ALPHA_THREAD)
        beta_chunks = sink.posted_chunks(BETA_THREAD)
        assert len(alpha_chunks) == 1
        assert len(beta_chunks) == 1
        assert alpha_chunks[0].thread_id == ALPHA_THREAD
        assert alpha_chunks[0].room_id == ROOM
        assert beta_chunks[0].thread_id == BETA_THREAD
        assert beta_chunks[0].room_id == BETA_ROOM

    def test_room_mode_targets_room_key(self):
        sink = MatrixToolStateSink(_transport(_store()))
        gw, _, _, _ = _tool_gateway(sink_factory=lambda: sink, session_threads=(None, BETA_THREAD))
        gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
        # alpha is room-scoped -> posted under room key, thread_id None
        assert len(sink.posted_chunks(ROOM)) == 1
        assert sink.posted_chunks(ROOM)[0].thread_id is None


# ── Per-session sequencing + skip-on-reconnect ───────────────────────────


class TestSequencingAndSkipOnReconnect:
    def test_observer_skips_already_seen_deltas(self):
        sink = MatrixToolStateSink(_transport(_store()))
        gw, _, _, _ = _tool_gateway(sink_factory=lambda: sink)

        # First observer streams 3 deltas.
        gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
        gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "a.txt"}, result="done"))
        gw.stream_tool_start("alpha", _tool(name="grep", args={"pattern": "x"}))
        assert len(sink.all_posted()) == 3
        last_seq = gw.stream_tool_complete("alpha", _tool(name="grep", args={"pattern": "x"}, result="1 match"))
        assert last_seq.sequence == 4

        # A reconnecting observer resumes from the last seen sequence.
        reconnecting_sink = MatrixToolStateSink(_transport(_store()))
        observer = MeshToolStateObserver(reconnecting_sink, last_seen_sequence=4)
        # Replay the same 4 deltas through the observer: all skipped.
        for chunk in sink.all_posted():
            assert observer.forward(chunk) is False
        assert reconnecting_sink.all_posted() == []

        # A genuinely new delta is forwarded.
        new_chunk = MeshToolStateChunk(
            session_id=last_seq.session_id,
            worker_id="alpha",
            sequence=5,
            trace=ToolTraceEntry(type="tool_call_started", tool_name="ls"),
            room_id=last_seq.room_id,
            thread_id=last_seq.thread_id,
        )
        assert observer.forward(new_chunk) is True
        assert len(reconnecting_sink.all_posted()) == 1

    def test_per_session_sequence_independent(self):
        sink = MatrixToolStateSink(_transport(_store()))
        gw, _, _, _ = _tool_gateway(sink_factory=lambda: sink)
        c1 = gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
        c2 = gw.stream_tool_start("beta", _tool(name="grep", args={"pattern": "x"}))
        # Each session numbers independently from 1.
        assert c1.sequence == 1
        assert c2.sequence == 1
        assert c1.session_id == f"{ROOM}:{ALPHA_THREAD}"
        assert c2.session_id == f"{BETA_ROOM}:{BETA_THREAD}"


# ── Clean teardown via Item 4 cancel primitive ───────────────────────────


class TestCleanTeardown:
    @pytest.mark.asyncio
    async def test_tool_state_streaming_does_not_leak_into_cancel(self):
        """Tool-state streaming must not interfere with cancellation teardown."""
        gw, _, _, _ = _tool_gateway(sink_factory=lambda: MatrixToolStateSink(_transport(_store())))
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="c1"),
        )
        await gw.cancel_outbox_entry(envelope.outbox_id, cancel_source="user_stop")
        entry = gw.get_outbox_entry(envelope.outbox_id)
        assert entry.status == "cancelled"
        assert entry.cancel_source == "user_stop"
        # No worker-facing cancel event leaked from tool-state streaming.
        types = [e.event_type for e in gw.lifecycle_events]
        assert "worker_cancel_requested" not in types

    @pytest.mark.asyncio
    async def test_tool_state_streaming_does_not_break_delivery(self):
        gw, _, transport, _ = _tool_gateway(sink_factory=lambda: MatrixToolStateSink(_transport(_store())))
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="c1"),
        )
        await gw.deliver_pending()
        assert gw.get_outbox_entry(envelope.outbox_id).status == "delivered"


# ── Content-free lifecycle preserved ─────────────────────────────────────


class TestContentFreeLifecycle:
    def test_tool_state_streamed_event_is_content_free(self):
        gw, _, _, _ = _tool_gateway(sink_factory=lambda: MatrixToolStateSink(_transport(_store())))
        gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
        gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "a.txt"}, result="SECRET body"))

        streamed = [e for e in gw.lifecycle_events if e.event_type == "tool_state_streamed"]
        assert len(streamed) == 2
        # Count carried, never a tool/tool-result payload.
        assert streamed[0].count == 1
        assert streamed[1].count == 2
        # Lifecycle events expose no tool-metadata fields at all.
        assert not hasattr(streamed[0], "tool_trace")
        assert not hasattr(streamed[0], "result_preview")

    def test_no_message_body_leaks_into_lifecycle_outcomes(self):
        gw, _, _, _ = _tool_gateway(sink_factory=lambda: MatrixToolStateSink(_transport(_store())))
        gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "secret.txt"}, result="SUPER_SECRET_DATA"))
        # lifecycle_outcomes is content-free: no tool result text anywhere.
        serialized = str(gw.lifecycle_outcomes) + str(gw.lifecycle_events)
        assert "SUPER_SECRET_DATA" not in serialized
        assert "secret.txt" not in serialized


# ── include_results=False redaction ──────────────────────────────────────


class TestIncludeResultsRedaction:
    def test_results_redacted_by_default(self):
        gw, _, _, _ = _tool_gateway(sink_factory=lambda: MatrixToolStateSink(_transport(_store())))
        chunk = gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "a.txt"}, result="SECRET body"))
        assert chunk.trace.result_preview is None
        assert chunk.trace.args_preview is not None  # args preview still kept
        # Lifecycle carries no tool body either.
        serialized = str(gw.lifecycle_events)
        assert "SECRET body" not in serialized

    def test_result_preview_none_kept_clean(self):
        # A completed tool with no result stays cleanly redacted (nothing to strip).
        gw, _, _, _ = _tool_gateway(sink_factory=lambda: MatrixToolStateSink(_transport(_store())))
        chunk = gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "a.txt"}))
        assert chunk.trace.result_preview is None

    def test_include_results_requires_human_gate(self):
        with pytest.raises(MeshToolStateError, match="human approval"):
            MeshToolStateCoordinator(include_results=True)

    def test_include_results_gate_constant_is_false(self):
        assert INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED is False


# ── Default-OFF no-op path ───────────────────────────────────────────────


class TestDefaultOffNoop:
    def test_no_tool_state_coordinator_is_unchanged(self):
        store = _store()
        transport = _transport(store)
        gw = MeshGateway(
            transport=transport,
            cursor_store=store,
            execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
            gateway_room_id="!gw:localhost",
        )
        gw.register_worker(MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id=ROOM))
        # Default-OFF: no tool-state forwarding, no tool_state_streamed event.
        assert gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"})) is None
        assert gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "a.txt"}, result="x")) is None
        assert [e.event_type for e in gw.lifecycle_events] == ["worker_registered"]

    def test_disabled_coordinator_is_unchanged(self):
        gw, _, _, _ = _tool_gateway(enabled=False)
        assert gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"})) is None
        assert gw.stream_tool_complete("alpha", _tool(name="read_file", args={"path": "a.txt"}, result="x")) is None
        assert "tool_state_streamed" not in [e.event_type for e in gw.lifecycle_events]


# ── No real network call (Phase B gated) ─────────────────────────────────


class TestPhaseBToolStreamPostingGate:
    def test_phase_b_posting_constant_is_false(self):
        assert PHASE_B_TOOL_STREAM_POSTING_ENABLED is False

    def test_coordinator_defaults_to_null_sink(self):
        """The default coordinator uses the null no-op sink — never real posting."""
        coord = MeshToolStateCoordinator(enabled=True)
        assert coord.sink_factory is None

    def test_matrix_sink_depends_only_on_transport(self):
        """MatrixToolStateSink is a transport-backed sink, not an nio client."""
        sink = MatrixToolStateSink(_transport(_store()))
        assert isinstance(sink.transport, MatrixMeshTransport)
        # It satisfies the sink protocol.
        assert isinstance(sink, MeshToolStateSink)

    def test_gateway_default_path_makes_no_network_call(self):
        """With tool-state OFF, no tool-state is forwarded at all."""
        store = _store()
        gw = MeshGateway(
            transport=_transport(store),
            cursor_store=store,
            execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
            gateway_room_id="!gw:localhost",
        )
        gw.register_worker(MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id=ROOM))
        gw.stream_tool_start("alpha", _tool(name="read_file", args={"path": "a.txt"}))
        # Nothing streamed, no lifecycle event.
        assert [e.event_type for e in gw.lifecycle_events] == ["worker_registered"]
