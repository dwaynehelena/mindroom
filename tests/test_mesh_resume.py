"""Tests for Item 5: mesh reconnect with cursor resume (Phase A, local only).

Covers v1->v2 cursor migration/back-compat, restart->resume idempotency (no
duplicate delivery, no full replay), cursor advance, persistence across a fresh
``MeshCursorStore`` instance over the same temp path (process restart),
session-aware resume, the default-OFF no-op path, and that no network call is
made (Phase B external sync-token replay is gated).
"""

from __future__ import annotations

import pytest

from mindroom.mesh import (
    PHASE_B_RESUME_ENABLED,
    GatewayExecutionGate,
    GatewayRuntimeMode,
    MatrixMeshTransport,
    MeshCursorStore,
    MeshGateway,
    MeshMessage,
    MeshReconnectCoordinator,
    MeshWorkerRegistration,
    resume_flag_enabled,
)
from mindroom.mesh.cursor import MeshReconnectCursor

# ── Helpers ───────────────────────────────────────────────────────────────


def make_message(source="alpha", target="beta", content="hello", corr_id="corr"):
    return MeshMessage(
        source_worker_id=source,
        target_worker_id=target,
        content=content,
        correlation_id=corr_id,
    )


def build_gateway(*, cursor_store=None, resume=None, tmp_path=None):
    """Return a gateway with two registered workers and optional resume coordinator."""
    store = cursor_store if cursor_store is not None else MeshCursorStore(
        storage_path=tmp_path,
    )
    transport = MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")
    gw = MeshGateway(
        transport=transport,
        cursor_store=store,
        execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
        gateway_room_id="!gw:localhost",
        resume=resume,
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost"),
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="beta", agent_name="beta-agent", room_id="!beta:localhost"),
    )
    return gw, store, transport


async def route_and_deliver(gateway, count, corr_prefix="corr"):
    """Route ``count`` messages to beta and deliver them all.

    Returns the outbox_ids in delivery-log order (as appended by the transport).
    """
    for i in range(count):
        gateway.route_message(make_message(corr_id=f"{corr_prefix}-{i}"))
    await gateway.deliver_pending()
    return [
        entry.outbox_id
        for entry, _ in gateway.transport.get_delivered_messages("!beta:localhost")
    ]


# ── v1 -> v2 migration / back-compat ──────────────────────────────────────


class TestCursorMigration:
    """MeshReconnectCursor v1/v2 from_json back-compatibility."""

    def test_v1_record_parses_with_session_none(self):
        v1 = (
            '{"cache_generation":"gen-1","cursor":"mesh-outbox-abc-1",'
            '"saved_at":1.0,"version":"mindroom-mesh-cursor-v1","worker_id":"beta"}'
        )
        cursor = MeshReconnectCursor.from_json(v1)
        assert cursor is not None
        assert cursor.worker_id == "beta"
        assert cursor.cursor == "mesh-outbox-abc-1"
        assert cursor.session_id is None

    def test_v2_record_parses_with_session(self):
        cursor = MeshReconnectCursor(
            worker_id="beta",
            cursor="mesh-outbox-def-1",
            cache_generation="gen-2",
            session_id="session-1",
        )
        parsed = MeshReconnectCursor.from_json(cursor.to_json())
        assert parsed is not None
        assert parsed.session_id == "session-1"

    def test_v2_record_parses_without_session(self):
        cursor = MeshReconnectCursor(worker_id="beta", cursor="c", cache_generation="g")
        parsed = MeshReconnectCursor.from_json(cursor.to_json())
        assert parsed is not None
        assert parsed.session_id is None

    def test_v2_to_json_omits_session_when_none(self):
        cursor = MeshReconnectCursor(worker_id="beta", cursor="c", cache_generation="g")
        payload = cursor.to_json()
        assert "session_id" not in payload

    def test_unknown_version_returns_none(self):
        assert MeshReconnectCursor.from_json('{"version":"mindroom-mesh-cursor-v9"}') is None


# ── Default-OFF no-op path ────────────────────────────────────────────────


class TestDefaultOffNoop:
    """When MINDROOM_MESH_RESUME is OFF / coordinator absent, behavior is unchanged."""

    @pytest.mark.asyncio
    async def test_resume_worker_inert_without_coordinator(self, tmp_path):
        gw, _, _ = build_gateway(tmp_path=tmp_path)
        await route_and_deliver(gw, 3)
        result = await gw.resume_worker("beta")
        assert result.resumed is False
        assert result.replayed_outbox_ids == ()

    @pytest.mark.asyncio
    async def test_disabled_coordinator_is_noop(self, tmp_path):
        gw, store, _ = build_gateway(tmp_path=tmp_path)
        coord = MeshReconnectCoordinator(
            transport=gw.transport,
            cursor_store=store,
            lifecycle_sink=gw.lifecycle_events,
            enabled=False,
        )
        gw.resume = coord
        await route_and_deliver(gw, 3)
        result = await gw.resume_worker("beta")
        assert result.resumed is False

    def test_resume_flag_default_off(self):
        assert resume_flag_enabled({}) is False
        assert resume_flag_enabled({"MINDROOM_MESH_RESUME": "0"}) is False
        assert resume_flag_enabled({"MINDROOM_MESH_RESUME": "off"}) is False

    def test_resume_flag_truthy(self):
        for value in ("1", "true", "yes", "on"):
            assert resume_flag_enabled({"MINDROOM_MESH_RESUME": value}) is True, value


# ── Replay / idempotency / cursor advance ─────────────────────────────────


class TestCursorResume:
    """Real local replay semantics against the in-memory transport queue."""

    @pytest.mark.asyncio
    async def test_resume_replays_only_entries_after_cursor(self, tmp_path):
        """No full replay: only entries delivered after the saved cursor are returned."""
        gw, store, _ = build_gateway(tmp_path=tmp_path)
        # Deliver 5 messages -> cursor advances to the 5th.
        order = await route_and_deliver(gw, 5)
        # Simulate the worker's last certified point being the 3rd entry (index 2).
        third_outbox = order[2]
        store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=f"mesh-cursor-{third_outbox}-1000",
                cache_generation=f"mesh-cursor-{third_outbox}-1000",
            ),
        )
        coord = MeshReconnectCoordinator(
            transport=gw.transport,
            cursor_store=store,
            lifecycle_sink=gw.lifecycle_events,
            enabled=True,
        )
        result = await coord.resume("beta")
        assert result.resumed is True
        replayed = result.replayed_outbox_ids
        # Exactly the 2 entries delivered after the 3rd certified entry.
        assert len(replayed) == 2
        assert third_outbox not in replayed

    @pytest.mark.asyncio
    async def test_resume_no_duplicate_delivery(self, tmp_path):
        """Each entry is replayed at most once per resume; repeated resume is idempotent."""
        gw, store, _ = build_gateway(tmp_path=tmp_path)
        order = await route_and_deliver(gw, 5)
        third_outbox = order[2]
        store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=f"mesh-cursor-{third_outbox}-1000",
                cache_generation=f"mesh-cursor-{third_outbox}-1000",
            ),
        )
        coord = MeshReconnectCoordinator(
            transport=gw.transport,
            cursor_store=store,
            lifecycle_sink=gw.lifecycle_events,
            enabled=True,
        )
        first = await coord.resume("beta")
        second = await coord.resume("beta")
        # First resume replays the 2 trailing entries; second is a no-op.
        assert len(first.replayed_outbox_ids) == 2
        assert second.replayed_outbox_ids == ()

    @pytest.mark.asyncio
    async def test_resume_advances_cursor(self, tmp_path):
        """After a resume the saved cursor is advanced past the replayed entries."""
        gw, store, _ = build_gateway(tmp_path=tmp_path)
        order = await route_and_deliver(gw, 5)
        third_outbox = order[2]
        store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=f"mesh-cursor-{third_outbox}-1000",
                cache_generation=f"mesh-cursor-{third_outbox}-1000",
            ),
        )
        coord = MeshReconnectCoordinator(
            transport=gw.transport,
            cursor_store=store,
            lifecycle_sink=gw.lifecycle_events,
            enabled=True,
        )
        result = await coord.resume("beta")
        assert result.advanced_cursor is not None
        saved = store.load("beta")
        assert saved is not None
        # Advanced past the last replayed entry.
        assert saved.cursor == result.advanced_cursor

    @pytest.mark.asyncio
    async def test_persistence_across_fresh_store_instance(self, tmp_path):
        """Cursor persists across a fresh MeshCursorStore over the same temp path."""
        store1 = MeshCursorStore(storage_path=tmp_path)
        gw1, _, _ = build_gateway(cursor_store=store1)
        await route_and_deliver(gw1, 3)
        saved1 = store1.load("beta")
        assert saved1 is not None

        # Simulate process restart: brand new store over the same path.
        store2 = MeshCursorStore(storage_path=tmp_path)
        loaded = store2.load("beta")
        assert loaded is not None
        assert loaded.cursor == saved1.cursor

    @pytest.mark.asyncio
    async def test_restart_resume_via_gateway_entrypoint(self, tmp_path):
        """Gateway.resume_worker runs the coordinator and emits worker_reconnected.

        Scenario: 3 messages delivered before the worker goes offline (cursor
        certified at the 3rd).  While offline, 2 more messages are delivered to
        the room queue.  After restart, resume replays exactly those 2.
        """
        store1 = MeshCursorStore(storage_path=tmp_path)
        gw1, _, _ = build_gateway(cursor_store=store1)
        order = await route_and_deliver(gw1, 3)
        third_outbox = order[2]
        store1.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=f"mesh-cursor-{third_outbox}-1000",
                cache_generation=f"mesh-cursor-{third_outbox}-1000",
            ),
        )
        # While "disconnected", two more messages arrive (delivered to queue).
        await route_and_deliver(gw1, 2, corr_prefix="post")

        # Restart: fresh store + fresh gateway + coordinator.  Simulate the
        # durable delivery log being restored into the new transport (as the
        # outbox would be after a process restart), then resume.
        store2 = MeshCursorStore(storage_path=tmp_path)
        transport2 = MatrixMeshTransport(cursor_store=store2, gateway_room_id="!gw:localhost")
        gw2 = MeshGateway(
            transport=transport2,
            cursor_store=store2,
            execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
            gateway_room_id="!gw:localhost",
            resume=MeshReconnectCoordinator(
                transport=transport2,
                cursor_store=store2,
                lifecycle_sink=[],
                enabled=True,
            ),
        )
        gw2.register_worker(
            MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id="!alpha:localhost"),
        )
        gw2.register_worker(
            MeshWorkerRegistration(worker_id="beta", agent_name="b", room_id="!beta:localhost"),
        )
        # Re-feed the delivered messages into the new transport's in-memory
        # queue (restored outbox) WITHOUT re-saving cursors, then set the
        # durable cursor to the 3rd certified point (restored from disk).
        all_delivered = [
            entry.outbox_id for entry, _ in gw1.transport.get_delivered_messages("!beta:localhost")
        ]
        for outbox_id in all_delivered:
            entry = gw1.get_outbox_entry(outbox_id)
            message = gw1._messages[entry.message_id]
            await transport2._deliver_to_room(entry, message)
        store2.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=f"mesh-cursor-{third_outbox}-1000",
                cache_generation=f"mesh-cursor-{third_outbox}-1000",
            ),
        )
        # Replay exactly the 2 entries delivered after the 3rd certified point.
        result = await gw2.resume_worker("beta")
        assert result.resumed is True
        assert len(result.replayed_outbox_ids) == 2
        assert third_outbox not in result.replayed_outbox_ids
        reconnected = [e for e in gw2.lifecycle_events if e.event_type == "worker_reconnected"]
        assert len(reconnected) == 1


# ── Session-aware resume ──────────────────────────────────────────────────


class TestSessionAwareResume:
    """A resume for a different session than the saved cursor is a no-op."""

    @pytest.mark.asyncio
    async def test_cross_session_resume_is_noop(self, tmp_path):
        gw, store, _ = build_gateway(tmp_path=tmp_path)
        await route_and_deliver(gw, 3)
        store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor="mesh-cursor-xyz-1000",
                cache_generation="mesh-cursor-xyz-1000",
                session_id="session-a",
            ),
        )
        coord = MeshReconnectCoordinator(
            transport=gw.transport,
            cursor_store=store,
            lifecycle_sink=gw.lifecycle_events,
            enabled=True,
        )
        result = await coord.resume("beta", session_id="session-b")
        assert result.resumed is False
        assert result.replayed_outbox_ids == ()

    @pytest.mark.asyncio
    async def test_same_session_resume_replays(self, tmp_path):
        gw, store, _ = build_gateway(tmp_path=tmp_path)
        order = await route_and_deliver(gw, 3)
        # Deliveries happened under session-a, so mark them session-scoped.
        for outbox_id in order:
            gw.get_outbox_entry(outbox_id).target_session_id = "session-a"
        # Cursor certified at the 1st entry -> 2 trailing session-a entries replay.
        first_outbox = order[0]
        store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=f"mesh-cursor-{first_outbox}-1000",
                cache_generation=f"mesh-cursor-{first_outbox}-1000",
                session_id="session-a",
            ),
        )
        coord = MeshReconnectCoordinator(
            transport=gw.transport,
            cursor_store=store,
            lifecycle_sink=gw.lifecycle_events,
            enabled=True,
        )
        result = await coord.resume("beta", session_id="session-a")
        assert result.resumed is True
        assert len(result.replayed_outbox_ids) == 2

    @pytest.mark.asyncio
    async def test_cross_session_scoped_cursor_noop(self, tmp_path):
        """A cursor scoped to session-a replays nothing for session-b deliveries."""
        gw, store, _ = build_gateway(tmp_path=tmp_path)
        order = await route_and_deliver(gw, 3)
        for outbox_id in order:
            gw.get_outbox_entry(outbox_id).target_session_id = "session-b"
        first_outbox = order[0]
        store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=f"mesh-cursor-{first_outbox}-1000",
                cache_generation=f"mesh-cursor-{first_outbox}-1000",
                session_id="session-a",
            ),
        )
        coord = MeshReconnectCoordinator(
            transport=gw.transport,
            cursor_store=store,
            lifecycle_sink=gw.lifecycle_events,
            enabled=True,
        )
        result = await coord.resume("beta", session_id="session-b")
        # The cursor is scoped to session-a; there are no session-a deliveries
        # to replay, so the resume is a no-op.
        assert result.resumed is False
        assert result.replayed_outbox_ids == ()

    @pytest.mark.asyncio
    async def test_sessionless_cursor_ignores_session_filter(self, tmp_path):
        gw, store, _ = build_gateway(tmp_path=tmp_path)
        order = await route_and_deliver(gw, 3)
        first_outbox = order[0]
        store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=f"mesh-cursor-{first_outbox}-1000",
                cache_generation=f"mesh-cursor-{first_outbox}-1000",
            ),
        )
        coord = MeshReconnectCoordinator(
            transport=gw.transport,
            cursor_store=store,
            lifecycle_sink=gw.lifecycle_events,
            enabled=True,
        )
        # A v1-style (no session) cursor is not scoped, so any session replays.
        result = await coord.resume("beta", session_id="anything")
        assert result.resumed is True


# ── No-network (Phase B gated) ────────────────────────────────────────────


class TestNoNetwork:
    """Resume makes no external network call (Phase B external sync-token replay is gated)."""

    @pytest.mark.asyncio
    async def test_resume_makes_no_network_calls(self, tmp_path, monkeypatch):
        """The local resume path only touches the store, transport log, and sink.

        Replaying from a real Matrix sync token against a live homeserver is a
        Phase B external side effect; the Phase A path must never attempt a
        wire-level delivery (``_deliver_to_room``) or contact a homeserver.
        """
        gw, store, transport = build_gateway(tmp_path=tmp_path)
        await route_and_deliver(gw, 3)

        delivered_calls: list[str] = []

        async def spy_deliver(entry, message):
            delivered_calls.append(entry.outbox_id)
            return await transport._deliver_to_room(entry, message)

        monkeypatch.setattr(gw.transport, "_deliver_to_room", spy_deliver)
        coord = MeshReconnectCoordinator(
            transport=gw.transport,
            cursor_store=store,
            lifecycle_sink=gw.lifecycle_events,
            enabled=True,
        )
        await coord.resume("beta")
        # No wire-level delivery is attempted during local resume.
        assert delivered_calls == []

    @pytest.mark.asyncio
    async def test_phase_b_constant_is_false(self):
        """The external replay gate constant must remain False until approved."""
        assert PHASE_B_RESUME_ENABLED is False
