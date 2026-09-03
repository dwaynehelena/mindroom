"""Tests for P1 Agent Mesh Gateway: gateway-only runtime, routing, cursors, cancellation."""

from __future__ import annotations

import asyncio
import time

import pytest

from mindroom.mesh import (
    GatewayExecutionGate,
    GatewayOnlyRuntime,
    GatewayRuntimeMode,
    MeshGateway,
    MeshGatewayError,
    MatrixMeshTransport,
    MeshCursorStore,
    MeshLifecycleEvent,
    MeshMessage,
    MeshWorkerRegistration,
    content_free_lifecycle_outcomes,
)
from mindroom.mesh.models import MeshOutboxEntry


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def cursor_store(tmp_path):
    """Return a cursor store with a temp directory."""
    return MeshCursorStore(storage_path=tmp_path)


@pytest.fixture
def transport(cursor_store):
    """Return a Matrix mesh transport."""
    return MatrixMeshTransport(cursor_store=cursor_store, gateway_room_id="!gw:localhost")


@pytest.fixture
def gate():
    """Return a closed execution gate (gateway-only mode)."""
    return GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY)


@pytest.fixture
def gateway(transport, cursor_store, gate):
    """Return a mesh gateway with two registered workers."""
    gw = MeshGateway(
        transport=transport,
        cursor_store=cursor_store,
        execution_gate=gate,
        gateway_room_id="!gw:localhost",
    )
    gw.register_worker(
        MeshWorkerRegistration(
            worker_id="alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
        ),
    )
    gw.register_worker(
        MeshWorkerRegistration(
            worker_id="beta",
            agent_name="beta-agent",
            room_id="!beta:localhost",
        ),
    )
    return gw


def make_message(source="alpha", target="beta", content="hello", corr_id="corr-1"):
    """Create a mesh message."""
    return MeshMessage(
        source_worker_id=source,
        target_worker_id=target,
        content=content,
        correlation_id=corr_id,
    )


# ── GatewayRuntimeMode tests ─────────────────────────────────────────────


class TestGatewayRuntimeMode:
    """Tests for runtime mode resolution."""

    def test_full_mode_from_empty_env(self):
        assert GatewayRuntimeMode.from_env({}) is GatewayRuntimeMode.FULL

    def test_gateway_only_from_env(self):
        assert GatewayRuntimeMode.from_env({"MINDROOM_MESH_GATEWAY_MODE": "gateway_only"}) is GatewayRuntimeMode.GATEWAY_ONLY

    def test_gateway_only_from_env_hyphen(self):
        assert GatewayRuntimeMode.from_env({"MINDROOM_MESH_GATEWAY_MODE": "gateway-only"}) is GatewayRuntimeMode.GATEWAY_ONLY

    def test_full_mode_from_env(self):
        assert GatewayRuntimeMode.from_env({"MINDROOM_MESH_GATEWAY_MODE": "full"}) is GatewayRuntimeMode.FULL

    def test_unknown_defaults_to_full(self):
        assert GatewayRuntimeMode.from_env({"MINDROOM_MESH_GATEWAY_MODE": "bogus"}) is GatewayRuntimeMode.FULL

    def test_is_gateway_only_property(self):
        assert GatewayRuntimeMode.GATEWAY_ONLY.is_gateway_only is True
        assert GatewayRuntimeMode.FULL.is_gateway_only is False


# ── Execution gate tests ─────────────────────────────────────────────────


class TestExecutionGate:
    """Tests for the gateway execution gate."""

    def test_closed_in_gateway_only_mode(self):
        gate = GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY)
        assert gate.is_closed is True
        assert gate.is_open is False

    def test_open_in_full_mode(self):
        gate = GatewayExecutionGate(mode=GatewayRuntimeMode.FULL)
        assert gate.is_open is True
        assert gate.is_closed is False

    def test_check_raises_when_closed(self):
        gate = GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY)
        with pytest.raises(MeshGatewayError, match="gated in gateway-only mode"):
            gate.check()

    def test_check_passes_when_open(self):
        gate = GatewayExecutionGate(mode=GatewayRuntimeMode.FULL)
        gate.check()  # Should not raise

    def test_close_blocks_execution(self):
        gate = GatewayExecutionGate(mode=GatewayRuntimeMode.FULL)
        assert gate.is_open
        gate.close()
        assert gate.is_closed
        with pytest.raises(MeshGatewayError):
            gate.check()

    def test_open_allows_execution(self):
        gate = GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY)
        assert gate.is_closed
        gate.open()
        assert gate.is_open
        gate.check()  # Should not raise


# ── Cursor store tests ───────────────────────────────────────────────────


class TestCursorStore:
    """Tests for reconnect cursor persistence."""

    def test_save_and_load_cursor(self, tmp_path):
        from mindroom.mesh.cursor import MeshReconnectCursor

        store = MeshCursorStore(storage_path=tmp_path)
        cursor = MeshReconnectCursor(
            worker_id="alpha",
            cursor="cursor-1",
            cache_generation="gen-1",
        )
        store.save(cursor)
        loaded = store.load("alpha")
        assert loaded is not None
        assert loaded.worker_id == "alpha"
        assert loaded.cursor == "cursor-1"
        assert loaded.cache_generation == "gen-1"

    def test_load_returns_none_for_unknown_worker(self, tmp_path):
        store = MeshCursorStore(storage_path=tmp_path)
        assert store.load("unknown") is None

    def test_clear_removes_cursor(self, tmp_path):
        from mindroom.mesh.cursor import MeshReconnectCursor

        store = MeshCursorStore(storage_path=tmp_path)
        store.save(MeshReconnectCursor(worker_id="alpha", cursor="c1", cache_generation="g1"))
        store.clear("alpha")
        assert store.load("alpha") is None

    def test_in_memory_only_store(self):
        from mindroom.mesh.cursor import MeshReconnectCursor

        store = MeshCursorStore()
        store.save(MeshReconnectCursor(worker_id="beta", cursor="c2", cache_generation="g2"))
        loaded = store.load("beta")
        assert loaded is not None
        assert loaded.cursor == "c2"

    def test_cursor_to_json_roundtrip(self):
        from mindroom.mesh.cursor import MeshReconnectCursor

        cursor = MeshReconnectCursor(
            worker_id="alpha",
            cursor="cursor-abc",
            cache_generation="gen-xyz",
            saved_at=1234567890.0,
        )
        json_str = cursor.to_json()
        parsed = MeshReconnectCursor.from_json(json_str)
        assert parsed is not None
        assert parsed.worker_id == "alpha"
        assert parsed.cursor == "cursor-abc"
        assert parsed.cache_generation == "gen-xyz"
        assert parsed.saved_at == 1234567890.0

    def test_cursor_from_invalid_json_returns_none(self):
        from mindroom.mesh.cursor import MeshReconnectCursor

        assert MeshReconnectCursor.from_json("not json") is None
        assert MeshReconnectCursor.from_json('{"version": "wrong"}') is None

    def test_persistence_across_store_instances(self, tmp_path):
        from mindroom.mesh.cursor import MeshReconnectCursor

        store1 = MeshCursorStore(storage_path=tmp_path)
        store1.save(MeshReconnectCursor(worker_id="alpha", cursor="c1", cache_generation="g1"))

        store2 = MeshCursorStore(storage_path=tmp_path)
        loaded = store2.load("alpha")
        assert loaded is not None
        assert loaded.cursor == "c1"


# ── Worker registration tests ────────────────────────────────────────────


class TestWorkerRegistration:
    """Tests for worker registration and deregistration."""

    def test_register_worker(self, gateway):
        assert len(gateway.registered_workers) == 2
        ids = {w.worker_id for w in gateway.registered_workers}
        assert ids == {"alpha", "beta"}

    def test_duplicate_registration_raises(self, gateway):
        with pytest.raises(MeshGatewayError, match="already registered"):
            gateway.register_worker(
                MeshWorkerRegistration(
                    worker_id="alpha",
                    agent_name="alpha-agent",
                    room_id="!alpha:localhost",
                ),
            )

    def test_deregister_worker(self, gateway):
        gateway.deregister_worker("alpha")
        ids = {w.worker_id for w in gateway.registered_workers}
        assert "alpha" not in ids
        assert "beta" in ids

    def test_deregister_unknown_raises(self, gateway):
        with pytest.raises(MeshGatewayError, match="not registered"):
            gateway.deregister_worker("unknown")

    def test_worker_status_registered(self, gateway):
        assert gateway.worker_status("alpha") == "registered"

    def test_worker_status_deregistered(self, gateway):
        gateway.deregister_worker("alpha")
        assert gateway.worker_status("alpha") == "deregistered"

    def test_registration_emits_lifecycle_event(self, gateway):
        events = [e for e in gateway.lifecycle_events if e.event_type == "worker_registered"]
        assert len(events) == 2

    def test_deregistration_emits_lifecycle_event(self, gateway):
        gateway.deregister_worker("alpha")
        events = [e for e in gateway.lifecycle_events if e.event_type == "worker_deregistered"]
        assert len(events) == 1
        assert events[0].worker_id == "alpha"


# ── Message routing tests ────────────────────────────────────────────────


class TestMessageRouting:
    """Tests for cross-worker message routing."""

    def test_route_message_creates_outbox_entry(self, gateway):
        msg = make_message()
        envelope = gateway.route_message(msg)
        assert envelope.outbox_id is not None
        assert envelope.route.source_worker_id == "alpha"
        assert envelope.route.target_worker_id == "beta"
        assert envelope.route.target_room_id == "!beta:localhost"

        entry = gateway.get_outbox_entry(envelope.outbox_id)
        assert entry is not None
        assert entry.status == "pending"

    def test_route_from_unknown_source_raises(self, gateway):
        msg = make_message(source="unknown")
        with pytest.raises(MeshGatewayError, match="Source worker"):
            gateway.route_message(msg)

    def test_route_to_unknown_target_raises(self, gateway):
        msg = make_message(target="unknown")
        with pytest.raises(MeshGatewayError, match="Target worker"):
            gateway.route_message(msg)

    def test_route_emits_lifecycle_event(self, gateway):
        msg = make_message()
        gateway.route_message(msg)
        events = [e for e in gateway.lifecycle_events if e.event_type == "message_routed"]
        assert len(events) == 1
        assert events[0].source_worker_id == "alpha"
        assert events[0].target_worker_id == "beta"
        assert events[0].correlation_id == "corr-1"

    def test_pending_outbox_count(self, gateway):
        gateway.route_message(make_message(corr_id="c1"))
        gateway.route_message(make_message(corr_id="c2"))
        assert gateway.pending_outbox_count() == 2


# ── Delivery tests ───────────────────────────────────────────────────────


class TestDelivery:
    """Tests for message delivery through the transport."""

    @pytest.mark.asyncio
    async def test_deliver_pending_delivers_messages(self, gateway):
        msg = make_message()
        gateway.route_message(msg)
        outcomes = await gateway.deliver_pending()
        assert len(outcomes) == 1
        assert all(status == "delivered" for status in outcomes.values())

    @pytest.mark.asyncio
    async def test_delivered_messages_reach_target_room(self, gateway):
        msg = make_message()
        envelope = gateway.route_message(msg)
        await gateway.deliver_pending()

        delivered = gateway.transport.get_delivered_messages("!beta:localhost")
        assert len(delivered) == 1
        entry, delivered_msg = delivered[0]
        assert entry.outbox_id == envelope.outbox_id
        assert delivered_msg.content == "hello"

    @pytest.mark.asyncio
    async def test_delivery_saves_reconnect_cursor(self, gateway):
        msg = make_message()
        gateway.route_message(msg)
        await gateway.deliver_pending()

        cursor = gateway.worker_reconnect("beta")
        assert cursor is not None
        assert cursor.worker_id == "beta"
        assert cursor.cursor is not None

    @pytest.mark.asyncio
    async def test_delivery_emits_lifecycle_event(self, gateway):
        msg = make_message()
        gateway.route_message(msg)
        await gateway.deliver_pending()

        events = [e for e in gateway.lifecycle_events if e.event_type == "message_delivered"]
        assert len(events) == 1
        assert events[0].target_worker_id == "beta"

    @pytest.mark.asyncio
    async def test_deliver_pending_skips_already_delivered(self, gateway):
        msg = make_message()
        gateway.route_message(msg)
        await gateway.deliver_pending()
        # Second call should find nothing pending
        outcomes = await gateway.deliver_pending()
        assert len(outcomes) == 0

    @pytest.mark.asyncio
    async def test_bidirectional_delivery(self, gateway):
        # A → B
        gateway.route_message(make_message(source="alpha", target="beta", corr_id="c1"))
        # B → A
        gateway.route_message(make_message(source="beta", target="alpha", corr_id="c2"))
        outcomes = await gateway.deliver_pending()
        assert len(outcomes) == 2
        assert all(s == "delivered" for s in outcomes.values())

        to_beta = gateway.transport.get_delivered_messages("!beta:localhost")
        to_alpha = gateway.transport.get_delivered_messages("!alpha:localhost")
        assert len(to_beta) == 1
        assert len(to_alpha) == 1


# ── Cancellation tests ───────────────────────────────────────────────────


class TestCancellation:
    """Tests for outbox entry cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_pending_entry(self, gateway):
        msg = make_message()
        envelope = gateway.route_message(msg)
        await gateway.cancel_outbox_entry(envelope.outbox_id, cancel_source="user_stop")

        entry = gateway.get_outbox_entry(envelope.outbox_id)
        assert entry is not None
        assert entry.status == "cancelled"
        assert entry.cancel_source == "user_stop"

    @pytest.mark.asyncio
    async def test_cancel_emits_lifecycle_event(self, gateway):
        msg = make_message()
        envelope = gateway.route_message(msg)
        await gateway.cancel_outbox_entry(envelope.outbox_id)

        events = [e for e in gateway.lifecycle_events if e.event_type == "message_cancelled"]
        assert len(events) == 1
        assert events[0].cancel_source == "user_stop"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_raises(self, gateway):
        with pytest.raises(MeshGatewayError, match="not found"):
            await gateway.cancel_outbox_entry("bogus-id")

    @pytest.mark.asyncio
    async def test_cancel_already_delivered_raises(self, gateway):
        msg = make_message()
        envelope = gateway.route_message(msg)
        await gateway.deliver_pending()
        with pytest.raises(MeshGatewayError, match="already delivered"):
            await gateway.cancel_outbox_entry(envelope.outbox_id)

    @pytest.mark.asyncio
    async def test_cancelled_entry_not_delivered(self, gateway):
        msg = make_message()
        envelope = gateway.route_message(msg)
        await gateway.cancel_outbox_entry(envelope.outbox_id)
        outcomes = await gateway.deliver_pending()
        assert envelope.outbox_id not in outcomes


# ── Content-free lifecycle tests ─────────────────────────────────────────


class TestContentFreeLifecycle:
    """Tests for content-free lifecycle outcomes."""

    @pytest.mark.asyncio
    async def test_outcomes_contain_no_message_content(self, gateway):
        msg = make_message(content="SECRET PAYLOAD")
        gateway.route_message(msg)
        await gateway.deliver_pending()

        outcomes = gateway.lifecycle_outcomes
        for key, value in outcomes.items():
            assert "SECRET PAYLOAD" not in key
            assert "SECRET PAYLOAD" not in value

    @pytest.mark.asyncio
    async def test_outcomes_track_delivery_status(self, gateway):
        gateway.route_message(make_message(corr_id="c1"))
        await gateway.deliver_pending()

        outcomes = gateway.lifecycle_outcomes
        assert any(v == "delivered" for v in outcomes.values())

    @pytest.mark.asyncio
    async def test_outcomes_track_routed_status(self, gateway):
        gateway.route_message(make_message(corr_id="c1"))
        outcomes = gateway.lifecycle_outcomes
        assert any(v == "routed" for v in outcomes.values())

    @pytest.mark.asyncio
    async def test_outcomes_track_cancelled_status(self, gateway):
        envelope = gateway.route_message(make_message(corr_id="c1"))
        await gateway.cancel_outbox_entry(envelope.outbox_id)

        outcomes = gateway.lifecycle_outcomes
        assert any(v == "cancelled" for v in outcomes.values())

    def test_content_free_lifecycle_function_directly(self):
        events = [
            MeshLifecycleEvent(event_type="message_routed", outbox_id="o1"),
            MeshLifecycleEvent(event_type="message_delivered", outbox_id="o1"),
            MeshLifecycleEvent(event_type="message_failed", outbox_id="o2", failure_reason="test"),
            MeshLifecycleEvent(event_type="message_cancelled", outbox_id="o3", cancel_source="user_stop"),
            MeshLifecycleEvent(event_type="worker_registered", worker_id="w1"),
        ]
        outcomes = content_free_lifecycle_outcomes(events)
        # Worker_registered is not content-free, so only 4 outcomes (o1 appears twice but last wins)
        assert outcomes.get("o1") == "delivered"
        assert outcomes.get("o2") == "failed"
        assert outcomes.get("o3") == "cancelled"


# ── Reconnect cursor behavior tests ──────────────────────────────────────


class TestReconnectCursor:
    """Tests for reconnect cursor behavior under disconnect scenarios."""

    @pytest.mark.asyncio
    async def test_cursor_saved_after_delivery(self, gateway):
        gateway.route_message(make_message())
        await gateway.deliver_pending()
        cursor = gateway.worker_reconnect("beta")
        assert cursor is not None
        assert cursor.cursor.startswith("mesh-cursor-")

    @pytest.mark.asyncio
    async def test_cursor_updates_on_subsequent_delivery(self, gateway):
        # First delivery
        gateway.route_message(make_message(corr_id="c1"))
        await gateway.deliver_pending()
        cursor1 = gateway.worker_reconnect("beta")
        assert cursor1 is not None

        # Second delivery — cursor should update
        gateway.route_message(make_message(corr_id="c2"))
        await gateway.deliver_pending()
        cursor2 = gateway.worker_reconnect("beta")
        assert cursor2 is not None
        assert cursor2.cursor != cursor1.cursor

    @pytest.mark.asyncio
    async def test_no_cursor_before_any_delivery(self, gateway):
        cursor = gateway.worker_reconnect("beta")
        assert cursor is None

    @pytest.mark.asyncio
    async def test_cursor_persists_across_disconnect(self, tmp_path):
        """Verify cursor survives a simulated disconnect/reconnect cycle."""
        from mindroom.mesh.cursor import MeshReconnectCursor, MeshCursorStore

        store = MeshCursorStore(storage_path=tmp_path)
        store.save(MeshReconnectCursor(worker_id="beta", cursor="cursor-before-disconnect", cache_generation="gen-1"))

        # Simulate disconnect: create a new store (like a restarted worker)
        store_after = MeshCursorStore(storage_path=tmp_path)
        cursor = store_after.load("beta")
        assert cursor is not None
        assert cursor.cursor == "cursor-before-disconnect"

    @pytest.mark.asyncio
    async def test_message_delivered_during_disconnect(self, gateway):
        """Messages routed while a worker is 'disconnected' are still delivered."""
        # Route a message
        gateway.route_message(make_message(corr_id="c1"))
        await gateway.deliver_pending()

        # Simulate B going offline — route another message
        gateway.route_message(make_message(corr_id="c2"))

        # Deliver while "disconnected" — transport still delivers to the room queue
        await gateway.deliver_pending()

        # On reconnect, B's cursor should reflect the latest delivery
        cursor = gateway.worker_reconnect("beta")
        assert cursor is not None
        assert cursor.cursor.startswith("mesh-cursor-")

        # Both messages should be in the room queue
        delivered = gateway.transport.get_delivered_messages("!beta:localhost")
        assert len(delivered) == 2


# ── Gateway-only runtime integration tests ───────────────────────────────


class TestGatewayOnlyRuntime:
    """Integration tests for the gateway-only runtime coordinator."""

    def test_start_sets_gateway_only_mode(self):
        runtime = GatewayOnlyRuntime(mode=GatewayRuntimeMode.GATEWAY_ONLY)
        runtime.start()
        assert runtime.is_started
        assert runtime.gate.is_closed
        runtime.stop()

    def test_stop_opens_execution_gate(self):
        runtime = GatewayOnlyRuntime(mode=GatewayRuntimeMode.GATEWAY_ONLY)
        runtime.start()
        runtime.stop()
        assert runtime.gate.is_open

    def test_double_start_raises(self):
        runtime = GatewayOnlyRuntime(mode=GatewayRuntimeMode.GATEWAY_ONLY)
        runtime.start()
        with pytest.raises(MeshGatewayError, match="already started"):
            runtime.start()
        runtime.stop()

    def test_stop_without_start_is_noop(self):
        runtime = GatewayOnlyRuntime(mode=GatewayRuntimeMode.GATEWAY_ONLY)
        runtime.stop()  # Should not raise
        assert not runtime.is_started

    @pytest.mark.asyncio
    async def test_full_runtime_lifecycle(self):
        runtime = GatewayOnlyRuntime(
            mode=GatewayRuntimeMode.GATEWAY_ONLY,
            gateway_room_id="!gw:localhost",
        )
        runtime.start()

        # Register workers
        runtime.gateway.register_worker(
            MeshWorkerRegistration(worker_id="w1", agent_name="a1", room_id="!r1:localhost"),
        )
        runtime.gateway.register_worker(
            MeshWorkerRegistration(worker_id="w2", agent_name="a2", room_id="!r2:localhost"),
        )

        # Route and deliver
        runtime.gateway.route_message(
            MeshMessage(source_worker_id="w1", target_worker_id="w2", content="test", correlation_id="c1"),
        )
        outcomes = await runtime.gateway.deliver_pending()
        assert all(s == "delivered" for s in outcomes.values())

        # Verify cursor
        cursor = runtime.gateway.worker_reconnect("w2")
        assert cursor is not None

        runtime.stop()
        assert runtime.gate.is_open

    @pytest.mark.asyncio
    async def test_gateway_only_blocks_execution_but_allows_routing(self):
        """In gateway-only mode: execution blocked, routing active."""
        runtime = GatewayOnlyRuntime(mode=GatewayRuntimeMode.GATEWAY_ONLY)
        runtime.start()

        # Execution gate blocks
        with pytest.raises(MeshGatewayError, match="gated"):
            runtime.gate.check()

        # But routing still works
        runtime.gateway.register_worker(
            MeshWorkerRegistration(worker_id="w1", agent_name="a1", room_id="!r1:localhost"),
        )
        runtime.gateway.register_worker(
            MeshWorkerRegistration(worker_id="w2", agent_name="a2", room_id="!r2:localhost"),
        )
        envelope = runtime.gateway.route_message(
            MeshMessage(source_worker_id="w1", target_worker_id="w2", content="hi", correlation_id="c1"),
        )
        assert envelope.outbox_id is not None

        outcomes = await runtime.gateway.deliver_pending()
        assert all(s == "delivered" for s in outcomes.values())

        runtime.stop()