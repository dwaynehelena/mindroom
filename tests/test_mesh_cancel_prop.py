"""Tests for Item 4: cancellation propagation to workers (Phase A, local only).

Covers:
- ``MeshCancellationPropagator`` translates an outbox entry + correlation into a
  worker-facing cancel command and awaits its acknowledgment.
- Propagation reaches a fake worker (``FakeMeshCancelTransport`` records the call).
- Terminal 'cancelled' outbox state + ``cancel_source`` recorded.
- Acknowledgment path emits ``worker_cancel_requested`` / ``worker_cancel_acked``.
- Worker-unreachable handling emits ``worker_cancel_failed`` + unacknowledged result.
- TTL on the ``MeshCancelRegistry`` (expiry / pending count).
- Correlation to outbox entries (registry ``outbox_id_for`` / ack path via gateway).
- Default-OFF no-op path (existing ``cancel_outbox_entry`` unchanged, no worker call).
- No real network call: ``OpenClawMeshCancelTransport`` is unreachable / Phase B gated.
"""

# ruff: noqa: ANN001, ANN201, ANN202, D101, D102, RUF059

from __future__ import annotations

import asyncio

import pytest

from mindroom.cancellation import USER_STOP_CANCEL_MSG
from mindroom.mesh import (
    MESH_CANCEL_PROP_ENV,
    PHASE_B_CANCEL_RPC_ENABLED,
    FakeMeshCancelTransport,
    GatewayExecutionGate,
    GatewayRuntimeMode,
    MatrixMeshTransport,
    MeshCancelAck,
    MeshCancelCommand,
    MeshCancelPropagationError,
    MeshCancelRegistry,
    MeshCancellationPropagator,
    MeshCursorStore,
    MeshGateway,
    MeshMessage,
    MeshWorkerRegistration,
    OpenClawMeshCancelTransport,
    cancel_prop_flag_enabled,
)
from mindroom.mesh.models import MeshOutboxEntry

ROOM = "!alpha:localhost"


def _outbox_entry(*, outbox_id="o1", message_id="m1", target="beta") -> MeshOutboxEntry:
    return MeshOutboxEntry(
        outbox_id=outbox_id,
        message_id=message_id,
        source_worker_id="alpha",
        target_worker_id=target,
        source_room_id=ROOM,
        target_room_id="!beta:localhost",
        gateway_room_id="!gw:localhost",
        cancel_source="user_stop",
    )


def _gateway(cancel_prop=None):
    store = MeshCursorStore()
    transport = MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")
    gw = MeshGateway(
        transport=transport,
        cursor_store=store,
        execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
        gateway_room_id="!gw:localhost",
        cancel_prop=cancel_prop,
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id=ROOM),
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="beta", agent_name="b", room_id="!beta:localhost"),
    )
    return gw


# ── Flag / default-OFF ───────────────────────────────────────────────────


class TestCancelPropFlag:
    def test_flag_default_off(self):
        assert cancel_prop_flag_enabled({}) is False
        assert cancel_prop_flag_enabled({MESH_CANCEL_PROP_ENV: "0"}) is False
        assert cancel_prop_flag_enabled({MESH_CANCEL_PROP_ENV: "off"}) is False

    def test_flag_truthy(self):
        for value in ("1", "true", "yes", "on"):
            assert cancel_prop_flag_enabled({MESH_CANCEL_PROP_ENV: value}) is True, value


# ── Propagator translation / propagation ─────────────────────────────────


class TestCancellationPropagator:
    @pytest.mark.asyncio
    async def test_translate_maps_entry_to_worker_command(self):
        propagator = MeshCancellationPropagator()
        entry = _outbox_entry()
        command = propagator.translate(entry, "corr-1")
        assert isinstance(command, MeshCancelCommand)
        assert command.worker_id == "beta"
        assert command.correlation_id == "corr-1"
        assert command.outbox_id == "o1"
        assert command.cancel_source == USER_STOP_CANCEL_MSG

    @pytest.mark.asyncio
    async def test_propagate_reaches_fake_worker(self):
        fake = FakeMeshCancelTransport()
        propagator = MeshCancellationPropagator(transport=fake)
        result = await propagator.propagate(_outbox_entry(), "corr-1")
        assert len(fake.calls) == 1
        assert fake.calls[0].worker_id == "beta"
        assert fake.calls[0].correlation_id == "corr-1"
        assert fake.calls[0].cancel_source == "user_stop"
        assert result.propagated is True
        assert result.acknowledged is True
        assert result.failure_reason is None

    @pytest.mark.asyncio
    async def test_propagate_ack_emits_lifecycle_events(self):
        fake = FakeMeshCancelTransport()
        propagator = MeshCancellationPropagator(transport=fake)
        await propagator.propagate(_outbox_entry(), "corr-1")
        types = [e.event_type for e in propagator.lifecycle_sink]
        assert "worker_cancel_requested" in types
        assert "worker_cancel_acked" in types

    @pytest.mark.asyncio
    async def test_propagate_correlates_to_outbox(self):
        fake = FakeMeshCancelTransport()
        propagator = MeshCancellationPropagator(transport=fake)
        await propagator.propagate(_outbox_entry(outbox_id="o7"), "corr-7")
        assert propagator.registry.outbox_id_for("beta", "corr-7") == "o7"
        assert propagator.registry.is_acked("beta", "corr-7") is True

    @pytest.mark.asyncio
    async def test_propagate_disabled_is_noop(self):
        fake = FakeMeshCancelTransport()
        propagator = MeshCancellationPropagator(transport=fake, enabled=False)
        result = await propagator.propagate(_outbox_entry(), "corr-1")
        assert result.propagated is False
        assert result.acknowledged is False
        assert fake.calls == []  # no worker call issued

    @pytest.mark.asyncio
    async def test_propagate_worker_unreachable(self):
        fake = FakeMeshCancelTransport(fail=True)
        propagator = MeshCancellationPropagator(transport=fake)
        result = await propagator.propagate(_outbox_entry(), "corr-1")
        assert result.propagated is True
        assert result.acknowledged is False
        assert result.failure_reason == "worker_unreachable"
        types = [e.event_type for e in propagator.lifecycle_sink]
        assert "worker_cancel_failed" in types

    @pytest.mark.asyncio
    async def test_propagate_transport_exception_is_unacknowledged(self):
        class BoomTransport:
            async def request_cancel(self, command):
                raise RuntimeError("connection refused")

        propagator = MeshCancellationPropagator(transport=BoomTransport())
        result = await propagator.propagate(_outbox_entry(), "corr-1")
        assert result.acknowledged is False
        assert "connection refused" in (result.failure_reason or "")


# ── MeshCancelRegistry TTL ───────────────────────────────────────────────


class TestMeshCancelRegistry:
    def test_register_and_query(self):
        clock = {"t": 0.0}
        registry = MeshCancelRegistry(ttl_seconds=60.0, now=lambda: clock["t"])
        registry.register("beta", "corr-1", "o1")
        assert registry.outbox_id_for("beta", "corr-1") == "o1"
        assert registry.is_active("beta", "corr-1") is True
        assert registry.pending_count() == 1

    def test_acknowledge_clears_active(self):
        clock = {"t": 0.0}
        registry = MeshCancelRegistry(ttl_seconds=60.0, now=lambda: clock["t"])
        registry.register("beta", "corr-1", "o1")
        registry.acknowledge("beta", "corr-1", acknowledged=True)
        assert registry.is_active("beta", "corr-1") is False
        assert registry.is_acked("beta", "corr-1") is True
        assert registry.pending_count() == 0

    def test_ttl_expiry_reaps_entries(self):
        clock = {"t": 0.0}
        registry = MeshCancelRegistry(ttl_seconds=10.0, now=lambda: clock["t"])
        registry.register("beta", "corr-1", "o1")
        clock["t"] = 20.0
        assert registry.is_active("beta", "corr-1") is False
        assert registry.expire() == 1
        assert registry.outbox_id_for("beta", "corr-1") is None

    def test_ttl_not_expired_stays_active(self):
        clock = {"t": 0.0}
        registry = MeshCancelRegistry(ttl_seconds=10.0, now=lambda: clock["t"])
        registry.register("beta", "corr-1", "o1")
        clock["t"] = 9.0
        assert registry.is_active("beta", "corr-1") is True


# ── Gateway wiring: propagation enabled ──────────────────────────────────


def _gateway_with_cancel_prop(*, enabled=True, transport=None):
    propagator = MeshCancellationPropagator(
        transport=transport if transport is not None else FakeMeshCancelTransport(),
        enabled=enabled,
    )
    gw = _gateway(cancel_prop=propagator)
    return gw, propagator


class TestGatewayCancelPropagation:
    @pytest.mark.asyncio
    async def test_cancel_outbox_entry_propagates_to_worker(self):
        fake = FakeMeshCancelTransport()
        gw, propagator = _gateway_with_cancel_prop(transport=fake)
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="corr-1"),
        )
        await gw.cancel_outbox_entry(envelope.outbox_id, cancel_source="user_stop")

        entry = gw.get_outbox_entry(envelope.outbox_id)
        assert entry.status == "cancelled"
        assert entry.cancel_source == "user_stop"
        # Propagation reached the fake worker with the correlated command.
        assert len(fake.calls) == 1
        assert fake.calls[0].worker_id == "beta"
        assert fake.calls[0].correlation_id == "corr-1"
        assert fake.calls[0].outbox_id == envelope.outbox_id

    @pytest.mark.asyncio
    async def test_cancel_propagation_emits_lifecycle_events(self):
        gw, propagator = _gateway_with_cancel_prop()
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="corr-1"),
        )
        await gw.cancel_outbox_entry(envelope.outbox_id)
        types = [e.event_type for e in gw.lifecycle_events]
        assert "message_cancelled" in types
        assert "worker_cancel_requested" in types
        assert "worker_cancel_acked" in types

    @pytest.mark.asyncio
    async def test_cancel_propagation_registry_correlates_to_outbox(self):
        gw, propagator = _gateway_with_cancel_prop()
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="corr-9"),
        )
        await gw.cancel_outbox_entry(envelope.outbox_id)
        assert propagator.registry.outbox_id_for("beta", "corr-9") == envelope.outbox_id
        assert propagator.registry.is_acked("beta", "corr-9") is True


# ── Default-OFF no-op path (existing behavior unchanged) ─────────────────


class TestDefaultOffNoop:
    @pytest.mark.asyncio
    async def test_no_cancel_prop_coordinator_is_unchanged(self):
        """Without a cancel_prop coordinator, cancellation matches legacy behavior."""
        gw = _gateway(cancel_prop=None)
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="corr-1"),
        )
        await gw.cancel_outbox_entry(envelope.outbox_id, cancel_source="user_stop")
        entry = gw.get_outbox_entry(envelope.outbox_id)
        assert entry.status == "cancelled"
        assert entry.cancel_source == "user_stop"
        # No worker-facing cancel events emitted on the legacy path.
        types = [e.event_type for e in gw.lifecycle_events]
        assert "worker_cancel_requested" not in types
        assert "worker_cancel_acked" not in types

    @pytest.mark.asyncio
    async def test_disabled_coordinator_is_unchanged(self):
        """A disabled cancel_prop coordinator leaves cancellation unchanged."""
        gw, propagator = _gateway_with_cancel_prop(enabled=False)
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="corr-1"),
        )
        await gw.cancel_outbox_entry(envelope.outbox_id)
        entry = gw.get_outbox_entry(envelope.outbox_id)
        assert entry.status == "cancelled"
        assert propagator.registry.pending_count() == 0
        types = [e.event_type for e in gw.lifecycle_events]
        assert "worker_cancel_requested" not in types

    @pytest.mark.asyncio
    async def test_cancel_error_paths_unchanged(self):
        """Not-found / already-delivered cancellation still raises exactly as before."""
        gw = _gateway(cancel_prop=None)
        with pytest.raises(Exception, match="not found"):
            await gw.cancel_outbox_entry("bogus-id")
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="corr-1"),
        )
        await gw.deliver_pending()
        with pytest.raises(Exception, match="already delivered"):
            await gw.cancel_outbox_entry(envelope.outbox_id)


# ── No real network call (Phase B gated) ─────────────────────────────────


class TestPhaseBCancelRPCGate:
    def test_phase_b_constant_is_false(self):
        assert PHASE_B_CANCEL_RPC_ENABLED is False

    def test_propagator_defaults_to_fake_transport(self):
        """The default propagator uses the local fake transport — never real HTTP."""
        propagator = MeshCancellationPropagator()
        assert isinstance(propagator.transport, FakeMeshCancelTransport)

    @pytest.mark.asyncio
    async def test_openclaw_cancel_transport_is_gated(self):
        """OpenClawMeshCancelTransport refuses any cancel until Phase B approved."""
        transport = OpenClawMeshCancelTransport(endpoint="http://worker/cancel")
        with pytest.raises(MeshCancelPropagationError, match="not approved"):
            await transport.request_cancel(
                MeshCancelCommand(
                    worker_id="beta",
                    correlation_id="corr-1",
                    outbox_id="o1",
                    cancel_source="user_stop",
                ),
            )

    @pytest.mark.asyncio
    async def test_gateway_default_path_makes_no_network_call(self):
        """With cancellation propagation OFF, no worker-facing cancel is issued."""
        gw = _gateway(cancel_prop=None)
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="corr-1"),
        )
        await gw.cancel_outbox_entry(envelope.outbox_id)
        # Legacy path only: outbox cancelled, no worker cancel transport invoked.
        types = [e.event_type for e in gw.lifecycle_events]
        assert "worker_cancel_requested" not in types
        assert "worker_cancel_acked" not in types
        assert "worker_cancel_failed" not in types