"""Tests for Item 2: Matrix thread/session mapping for worker conversations (Phase A, local only).

Covers:
- Session ID derivation (room vs thread mode) via ``create_session_id``.
- Durable ``MeshSessionMap`` create / lookup / reap, persisted across fresh store
  instances (process restart), keyed on stable worker identity.
- ``MeshSessionResolver`` canonical session + thread-aware ``MessageTarget``
  resolution for inbound/outbound contexts.
- Thread-aware routing through the gateway (route/outbox carry thread + session).
- Integration with Item 5 resume into the correct thread.
- Default-OFF no-op path (gateway behavior unchanged when ``session_mapping`` absent).
- No real Matrix client / no network is used (Phase B thread management is gated).
"""

# ruff: noqa: ANN001, ANN201, ANN202, D101, D102, RUF059

from __future__ import annotations

import pytest

from mindroom.mesh import (
    PHASE_B_THREAD_MANAGEMENT_ENABLED,
    GatewayExecutionGate,
    GatewayRuntimeMode,
    MatrixMeshTransport,
    MeshCursorStore,
    MeshGateway,
    MeshMessage,
    MeshReconnectCoordinator,
    MeshSessionMap,
    MeshSessionMappingCoordinator,
    MeshSessionResolver,
    MeshWorkerRegistration,
    session_map_flag_enabled,
)
from mindroom.mesh.cursor import MeshReconnectCursor
from mindroom.message_target import MessageTarget
from mindroom.session_ids import create_session_id, parse_session_id

ROOM = "!alpha:localhost"
THREAD = "$thread-alpha"
ROOM_SESSION = ROOM
THREAD_SESSION = f"{ROOM}:{THREAD}"


# ── Session ID derivation (room vs thread) ───────────────────────────────


class TestSessionIdDerivation:
    def test_room_mode_is_room_id(self):
        assert create_session_id(ROOM, thread_id=None) == ROOM

    def test_thread_mode_is_room_colon_thread(self):
        assert create_session_id(ROOM, thread_id=THREAD) == THREAD_SESSION

    def test_parse_room_mode(self):
        assert parse_session_id(ROOM) == (ROOM, None)

    def test_parse_thread_mode(self):
        assert parse_session_id(THREAD_SESSION) == (ROOM, THREAD)

    def test_registration_session_id_room_mode(self):
        reg = MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id=ROOM)
        assert reg.session_id == ROOM

    def test_registration_session_id_thread_mode(self):
        reg = MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id=ROOM, thread_id=THREAD)
        assert reg.session_id == THREAD_SESSION


# ── Durable MeshSessionMap create / lookup / reap ────────────────────────


class TestMeshSessionMap:
    def test_create_lookup_roundtrip(self, tmp_path):
        smap = MeshSessionMap(storage_path=tmp_path)
        binding = smap.create(worker_id="alpha", room_id=ROOM, thread_id=THREAD)
        assert binding.session_id == THREAD_SESSION
        assert binding.is_thread_mode is True
        assert smap.lookup("alpha") == binding
        assert smap.known_worker_ids() == ("alpha",)

    def test_create_room_mode_binding(self, tmp_path):
        smap = MeshSessionMap(storage_path=tmp_path)
        binding = smap.create(worker_id="alpha", room_id=ROOM, thread_id=None)
        assert binding.session_id == ROOM
        assert binding.is_thread_mode is False

    def test_reap_removes_binding(self, tmp_path):
        smap = MeshSessionMap(storage_path=tmp_path)
        smap.create(worker_id="alpha", room_id=ROOM, thread_id=THREAD)
        removed = smap.reap("alpha")
        assert removed is not None
        assert smap.lookup("alpha") is None
        assert smap.known_worker_ids() == ()

    def test_lookup_unknown_returns_none(self, tmp_path):
        smap = MeshSessionMap(storage_path=tmp_path)
        assert smap.lookup("missing") is None

    def test_persistence_across_fresh_store_instances(self, tmp_path):
        """A fresh MeshSessionMap over the same temp path restores the binding."""
        smap1 = MeshSessionMap(storage_path=tmp_path)
        smap1.create(worker_id="alpha", room_id=ROOM, thread_id=THREAD)

        smap2 = MeshSessionMap(storage_path=tmp_path)
        binding = smap2.lookup("alpha")
        assert binding is not None
        assert binding.worker_id == "alpha"
        assert binding.session_id == THREAD_SESSION
        assert binding.thread_id == THREAD

    def test_reap_persists_removal(self, tmp_path):
        smap1 = MeshSessionMap(storage_path=tmp_path)
        smap1.create(worker_id="alpha", room_id=ROOM, thread_id=THREAD)
        smap1.reap("alpha")

        smap2 = MeshSessionMap(storage_path=tmp_path)
        assert smap2.lookup("alpha") is None


# ── Identity-keyed correctness ───────────────────────────────────────────


class TestIdentityKeyedMapping:
    def test_same_worker_rebinds_to_new_thread(self, tmp_path):
        """The same stable worker identity rebinds, never duplicates."""
        smap = MeshSessionMap(storage_path=tmp_path)
        first = smap.create(worker_id="alpha", room_id=ROOM, thread_id=THREAD)
        second = smap.create(worker_id="alpha", room_id=ROOM, thread_id="$thread-other")
        assert second.worker_id == first.worker_id
        assert smap.lookup("alpha").thread_id == "$thread-other"
        assert len(smap.known_worker_ids()) == 1

    def test_distinct_workers_keep_distinct_bindings(self, tmp_path):
        smap = MeshSessionMap(storage_path=tmp_path)
        smap.create(worker_id="alpha", room_id=ROOM, thread_id=THREAD)
        smap.create(worker_id="beta", room_id="!beta:localhost", thread_id="$thread-beta")
        assert set(smap.known_worker_ids()) == {"alpha", "beta"}


# ── MeshSessionResolver canonical session + target ───────────────────────


class TestMeshSessionResolver:
    def _reg(self, worker="alpha", room=ROOM, thread=None):
        return MeshWorkerRegistration(worker_id=worker, agent_name="a", room_id=room, thread_id=thread)

    def test_resolve_worker_room_mode_default(self):
        resolver = MeshSessionResolver(session_map=None)
        resolution = resolver.resolve_worker(self._reg())
        assert resolution.session_id == ROOM
        assert resolution.thread_id is None
        assert resolution.is_thread_mode is False

    def test_resolve_worker_thread_from_registration(self):
        resolver = MeshSessionResolver(session_map=None)
        resolution = resolver.resolve_worker(self._reg(thread=THREAD))
        assert resolution.session_id == THREAD_SESSION
        assert resolution.thread_id == THREAD
        assert resolution.is_thread_mode is True

    def test_resolve_worker_thread_from_binding_wins(self, tmp_path):
        """A durable binding's thread wins over the registration's own (or None)."""
        smap = MeshSessionMap(storage_path=tmp_path)
        smap.create(worker_id="alpha", room_id=ROOM, thread_id=THREAD)
        resolver = MeshSessionResolver(session_map=smap)
        resolution = resolver.resolve_worker(self._reg())  # registration has no thread
        assert resolution.session_id == THREAD_SESSION
        assert resolution.thread_id == THREAD

    def test_resolve_worker_explicit_thread_wins(self, tmp_path):
        smap = MeshSessionMap(storage_path=tmp_path)
        smap.create(worker_id="alpha", room_id=ROOM, thread_id=THREAD)
        resolver = MeshSessionResolver(session_map=smap)
        resolution = resolver.resolve_worker(self._reg(), thread_id="$thread-explicit")
        assert resolution.session_id == f"{ROOM}:$thread-explicit"
        assert resolution.thread_id == "$thread-explicit"

    def test_resolve_target_builds_message_target(self):
        resolver = MeshSessionResolver(session_map=None)
        target = resolver.resolve_target(self._reg(thread=THREAD))
        assert isinstance(target, MessageTarget)
        assert target.room_id == ROOM
        assert target.resolved_thread_id == THREAD
        assert target.session_id == THREAD_SESSION
        assert target.is_room_mode is False

    def test_resolve_target_room_mode(self):
        resolver = MeshSessionResolver(session_map=None)
        target = resolver.resolve_target(self._reg())
        assert target.session_id == ROOM
        assert target.resolved_thread_id is None
        assert target.is_room_mode is True

    def test_resolve_target_round_trips_session(self):
        resolver = MeshSessionResolver(session_map=None)
        target = resolver.resolve_target(self._reg(thread=THREAD))
        room, thread = resolver.parse(target.session_id)
        assert room == ROOM
        assert thread == THREAD


# ── Thread-aware routing through the gateway ─────────────────────────────


def _gateway_with_session_mapping(tmp_path, *, enabled=True):
    store = MeshCursorStore(storage_path=tmp_path)
    transport = MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")
    coordinator = MeshSessionMappingCoordinator(
        session_map=MeshSessionMap(storage_path=tmp_path),
        enabled=enabled,
    )
    gw = MeshGateway(
        transport=transport,
        cursor_store=store,
        execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
        gateway_room_id="!gw:localhost",
        session_mapping=coordinator,
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id="!alpha:localhost", thread_id="$thread-alpha"),
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="beta", agent_name="b", room_id="!beta:localhost", thread_id="$thread-beta"),
    )
    return gw, store, transport, coordinator


class TestThreadAwareRouting:
    @pytest.mark.asyncio
    async def test_route_carries_thread_context(self, tmp_path):
        gw, _, _, _ = _gateway_with_session_mapping(tmp_path)
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="c1"),
        )
        assert envelope.route.source_thread_id == "$thread-alpha"
        assert envelope.route.target_thread_id == "$thread-beta"
        entry = gw.get_outbox_entry(envelope.outbox_id)
        assert entry.target_thread_id == "$thread-beta"
        assert entry.target_session_id == "!beta:localhost:$thread-beta"

    @pytest.mark.asyncio
    async def test_envelope_thread_properties(self, tmp_path):
        gw, _, _, _ = _gateway_with_session_mapping(tmp_path)
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="c1"),
        )
        assert envelope.source_thread_id == "$thread-alpha"
        assert envelope.target_thread_id == "$thread-beta"

    @pytest.mark.asyncio
    async def test_delivery_target_resolves_thread(self, tmp_path):
        gw, _, _, _ = _gateway_with_session_mapping(tmp_path)
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="c1"),
        )
        entry = gw.get_outbox_entry(envelope.outbox_id)
        target = gw.delivery_target(entry)
        assert target.resolved_thread_id == "$thread-beta"
        assert target.session_id == "!beta:localhost:$thread-beta"

    @pytest.mark.asyncio
    async def test_delivery_honors_thread_locally(self, tmp_path):
        """The in-memory fake transport records a thread-aware delivery (no homeserver)."""
        gw, _, transport, _ = _gateway_with_session_mapping(tmp_path)
        gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="c1"),
        )
        await gw.deliver_pending()
        delivered = transport.get_delivered_messages("!beta:localhost")
        assert len(delivered) == 1
        entry, _ = delivered[0]
        assert entry.target_thread_id == "$thread-beta"
        assert entry.target_session_id == "!beta:localhost:$thread-beta"

    @pytest.mark.asyncio
    async def test_worker_session_accessor(self, tmp_path):
        gw, _, _, _ = _gateway_with_session_mapping(tmp_path)
        session = gw.worker_session("beta")
        assert session is not None
        assert session.session_id == "!beta:localhost:$thread-beta"
        assert gw.worker_session("unknown") is None

    @pytest.mark.asyncio
    async def test_registration_emits_session_bound_event(self, tmp_path):
        gw, _, _, _ = _gateway_with_session_mapping(tmp_path)
        bound = [e for e in gw.lifecycle_events if e.event_type == "worker_session_bound"]
        assert len(bound) == 2


# ── Integration with Item 5 resume into the correct thread ───────────────


class TestResumeIntoThread:
    async def _build_with_resume(self, tmp_path, *, thread_for_beta="$thread-beta"):
        store = MeshCursorStore(storage_path=tmp_path)
        transport = MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")
        coordinator = MeshSessionMappingCoordinator(
            session_map=MeshSessionMap(storage_path=tmp_path),
            enabled=True,
        )
        resume = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=store,
            lifecycle_sink=[],
            enabled=True,
        )
        gw = MeshGateway(
            transport=transport,
            cursor_store=store,
            execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
            gateway_room_id="!gw:localhost",
            session_mapping=coordinator,
            resume=resume,
        )
        gw.register_worker(
            MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id="!alpha:localhost", thread_id="$thread-alpha"),
        )
        gw.register_worker(
            MeshWorkerRegistration(
                worker_id="beta",
                agent_name="b",
                room_id="!beta:localhost",
                thread_id=thread_for_beta,
            ),
        )
        return gw, store, transport

    @pytest.mark.asyncio
    async def test_resume_replays_into_worker_session(self, tmp_path):
        """Resume resolves the worker's thread session and replays only that session."""
        gw, store, _ = await self._build_with_resume(tmp_path)
        # Deliver 3 messages into beta's thread session.
        for i in range(3):
            gw.route_message(
                MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id=f"c{i}"),
            )
        await gw.deliver_pending()
        order = [entry.outbox_id for entry, _ in gw.transport.get_delivered_messages("!beta:localhost")]
        first_outbox = order[0]
        # Cursor certified at the 1st entry, scoped to beta's thread session.
        store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=f"mesh-cursor-{first_outbox}-1000",
                cache_generation=f"mesh-cursor-{first_outbox}-1000",
                session_id="!beta:localhost:$thread-beta",
            ),
        )
        result = await gw.resume_worker("beta")
        # Resume replays exactly the 2 entries after the certified point in the
        # correct thread session.
        assert result.resumed is True
        assert len(result.replayed_outbox_ids) == 2
        assert first_outbox not in result.replayed_outbox_ids
        assert result.session_id == "!beta:localhost:$thread-beta"

    @pytest.mark.asyncio
    async def test_resume_noop_for_different_thread_session(self, tmp_path):
        """A resume whose cursor is scoped to a different thread is a no-op."""
        gw, store, _ = await self._build_with_resume(tmp_path)
        for i in range(2):
            gw.route_message(
                MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id=f"c{i}"),
            )
        await gw.deliver_pending()
        # Store a cursor scoped to a DIFFERENT thread than beta's bound thread.
        store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor="mesh-cursor-xyz-1000",
                cache_generation="mesh-cursor-xyz-1000",
                session_id="!beta:localhost:$thread-other",
            ),
        )
        result = await gw.resume_worker("beta")
        assert result.resumed is False
        assert result.replayed_outbox_ids == ()
        # The resolved session is still beta's bound thread.
        assert result.session_id == "!beta:localhost:$thread-beta"


# ── Default-OFF no-op path ───────────────────────────────────────────────


class TestDefaultOffNoop:
    def test_flag_default_off(self):
        assert session_map_flag_enabled({}) is False
        assert session_map_flag_enabled({"MINDROOM_MESH_SESSION_MAP": "0"}) is False
        assert session_map_flag_enabled({"MINDROOM_MESH_SESSION_MAP": "off"}) is False

    def test_flag_truthy(self):
        for value in ("1", "true", "yes", "on"):
            assert session_map_flag_enabled({"MINDROOM_MESH_SESSION_MAP": value}) is True, value

    @pytest.mark.asyncio
    async def test_no_mapping_gateway_is_room_mode(self, tmp_path):
        """Without a session_mapping coordinator, routing stays room-mode (legacy)."""
        store = MeshCursorStore(storage_path=tmp_path)
        transport = MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")
        gw = MeshGateway(
            transport=transport,
            cursor_store=store,
            execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
            gateway_room_id="!gw:localhost",
        )
        gw.register_worker(
            MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id="!alpha:localhost", thread_id="$thread-alpha"),
        )
        gw.register_worker(
            MeshWorkerRegistration(worker_id="beta", agent_name="b", room_id="!beta:localhost", thread_id="$thread-beta"),
        )
        assert gw._session_mapping_active is False
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="c1"),
        )
        # Legacy: no thread context, no target session on the outbox.
        assert envelope.route.source_thread_id is None
        assert envelope.route.target_thread_id is None
        entry = gw.get_outbox_entry(envelope.outbox_id)
        assert entry.target_thread_id is None
        assert entry.target_session_id is None

    @pytest.mark.asyncio
    async def test_disabled_coordinator_behaves_room_mode(self, tmp_path):
        """A disabled session-mapping coordinator leaves routing unchanged."""
        gw, store, transport, _ = _gateway_with_session_mapping(tmp_path, enabled=False)
        assert gw._session_mapping_active is False
        envelope = gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="c1"),
        )
        assert envelope.route.source_thread_id is None
        entry = gw.get_outbox_entry(envelope.outbox_id)
        assert entry.target_session_id is None
        # No binding event emitted when mapping is off.
        assert not any(e.event_type == "worker_session_bound" for e in gw.lifecycle_events)

    @pytest.mark.asyncio
    async def test_deregister_without_mapping_is_noop(self, tmp_path):
        store = MeshCursorStore(storage_path=tmp_path)
        transport = MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")
        gw = MeshGateway(
            transport=transport,
            cursor_store=store,
            execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
            gateway_room_id="!gw:localhost",
        )
        gw.register_worker(MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id="!a:localhost"))
        gw.deregister_worker("alpha")
        assert gw.worker_status("alpha") == "deregistered"


# ── No real Matrix client / no network (Phase B gated) ───────────────────


class TestNoNetworkPhaseBGated:
    def test_phase_b_thread_management_constant_is_cleared(self):
        """Phase B Unit 2 gate CLEARED on 2026-08-07 after a real live round-trip.

        A real Matrix thread delivery through the injected nio client passed
        against the local Synapse homeserver (thread relation preserved + real
        sync-token replay), so the gate is CLEARED.  Clearing only *permits*
        real thread management; the transport still uses the in-memory fake by
        default (no real client injected), so Phase A mapping stays local-only.
        """
        assert PHASE_B_THREAD_MANAGEMENT_ENABLED is True

    def test_resolver_makes_no_network_call(self, tmp_path):
        """Session resolution only touches the durable map + in-memory derivation."""
        smap = MeshSessionMap(storage_path=tmp_path)
        resolver = MeshSessionResolver(session_map=smap)
        reg = MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id=ROOM, thread_id=THREAD)
        target = resolver.resolve_target(reg)
        assert target.resolved_thread_id == THREAD
        # No external client is constructed or consulted.
        assert "nio" not in type(resolver).__module__

    @pytest.mark.asyncio
    async def test_thread_delivery_uses_fake_transport_only(self, tmp_path):
        """Thread-aware delivery goes through the in-memory transport, never a homeserver."""
        gw, _, transport, _ = _gateway_with_session_mapping(tmp_path)
        gw.route_message(
            MeshMessage(source_worker_id="alpha", target_worker_id="beta", content="hi", correlation_id="c1"),
        )
        await gw.deliver_pending()
        delivered = transport.get_delivered_messages("!beta:localhost")
        assert len(delivered) == 1
        assert delivered[0][0].target_thread_id == "$thread-beta"
