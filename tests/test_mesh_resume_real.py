"""Tests for Phase B Unit 5 — real sync-token replay at the coordinator level.

Covers ``MeshReconnectCoordinator.resume`` when a real ``nio.AsyncClient`` (or
transport adapter) is injected into ``MatrixMeshTransport``: resume replays from
the real Matrix sync token (``cursor.cursor`` as ``since``) and persists the real
sync ``next_batch`` as the advanced resume cursor.

No real network is used in unit tests — a fake nio client serves a fabricated
sync response carrying the mesh wire envelope.  The default-fake path (no client
injected) keeps the Phase A in-memory replay.
"""

from __future__ import annotations

import pytest

from mindroom.mesh import (
    MatrixMeshTransport,
    MeshCursorStore,
    MeshGateway,
    MeshMessage,
    MeshReconnectCoordinator,
    MeshWorkerRegistration,
)
from mindroom.mesh.cursor import MeshReconnectCursor
from mindroom.mesh.models import MeshOutboxEntry
from mindroom.mesh.transport import (
    _build_mesh_content,
    _sync_next_batch,
)

# ── Fake nio client (records sends; serves a scripted sync) ──────────────


class FakeTimeline:
    def __init__(self, events) -> None:
        self.events = events


class FakeRoomInfo:
    def __init__(self, events) -> None:
        self.timeline = FakeTimeline(events)


class FakeRooms:
    def __init__(self, join) -> None:
        self.join = join


class FakeSyncResponse:
    def __init__(self, joined_rooms=None, next_batch=None) -> None:
        self.rooms = FakeRooms(joined_rooms or {})
        self.next_batch = next_batch


class RoomMessageText:
    """Stand-in for ``nio.RoomMessageText`` carrying a wire envelope in source."""

    def __init__(self, content) -> None:
        self.source = content
        self.content = content


class FakeNioClient:
    """Recording fake of the nio client surface used by the transport.

    ``sync`` records the ``since`` token and returns a scripted sync response
    (optionally with a ``next_batch``).
    """

    def __init__(self, sync_response=None) -> None:
        self.sync_calls: list[str | None] = []
        self._sync_response = sync_response

    async def sync(self, since=None, **kwargs):
        self.sync_calls.append(since)
        return self._sync_response


# ── Fixtures / helpers ────────────────────────────────────────────────────


@pytest.fixture
def cursor_store(tmp_path):
    return MeshCursorStore(storage_path=tmp_path)


def make_entry(
    *,
    outbox_id="mesh-outbox-abc",
    target_worker_id="beta",
    target_room_id="!beta:localhost",
    target_thread_id=None,
    target_session_id=None,
):
    return MeshOutboxEntry(
        outbox_id=outbox_id,
        message_id=f"mesh-msg-{outbox_id}",
        source_worker_id="alpha",
        target_worker_id=target_worker_id,
        source_room_id="!alpha:localhost",
        target_room_id=target_room_id,
        gateway_room_id="!gw:localhost",
        target_thread_id=target_thread_id,
        target_session_id=target_session_id,
    )


def make_message(content="hello"):
    return MeshMessage(
        source_worker_id="alpha",
        target_worker_id="beta",
        content=content,
        correlation_id="corr-1",
    )


def make_transport(cursor_store, client):
    return MatrixMeshTransport(
        cursor_store=cursor_store,
        gateway_room_id="!gw:localhost",
        client=client,
    )


def sync_with_deliveries(entries, next_batch=None):
    """Build a fake sync response carrying the given mesh deliveries."""
    events = [RoomMessageText(_build_mesh_content(e, make_message("replayed"))) for e in entries]
    return FakeSyncResponse({"!beta:localhost": FakeRoomInfo(events)}, next_batch=next_batch)


def save_cursor(cursor_store, *, worker_id="beta", cursor_value="s1", session_id=None):
    cursor_store.save(
        MeshReconnectCursor(
            worker_id=worker_id,
            cursor=cursor_value,
            cache_generation=cursor_value,
            session_id=session_id,
        ),
    )


# ── next_batch extraction ─────────────────────────────────────────────────


class TestNextBatchExtraction:
    def test_next_batch_from_real_sync_response(self):
        resp = FakeSyncResponse({}, next_batch="s999")
        assert _sync_next_batch(resp) == "s999"

    def test_next_batch_missing_returns_none(self):
        resp = FakeSyncResponse({})
        assert _sync_next_batch(resp) is None

    def test_next_batch_empty_string_returns_none(self):
        resp = FakeSyncResponse({}, next_batch="")
        assert _sync_next_batch(resp) is None


# ── Coordinator-level real sync-token replay ──────────────────────────────


class TestCoordinatorRealSyncReplay:
    @pytest.mark.asyncio
    async def test_resume_replays_real_synced_delivery(self, cursor_store):
        """Resume with a real client replays from the sync token, not the in-memory log."""
        entry = make_entry(outbox_id="mesh-outbox-new")
        client = FakeNioClient(sync_response=sync_with_deliveries([entry], next_batch="s2"))
        transport = make_transport(cursor_store, client)
        save_cursor(cursor_store, cursor_value="s1")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coord.resume("beta")

        assert result.resumed is True
        assert result.replayed_outbox_ids == ("mesh-outbox-new",)
        # The transport synced using the real cursor token as ``since``.
        assert client.sync_calls == ["s1"]

    @pytest.mark.asyncio
    async def test_resume_uses_real_client_not_in_memory_log(self, cursor_store):
        """A delivery in the in-memory log but NOT in the sync response is not replayed."""
        # A message exists in the in-memory fake queue for beta (Phase A path,
        # no client injected at build time)...
        mem_entry = make_entry(outbox_id="mesh-outbox-inmem")
        mem_transport = MatrixMeshTransport(cursor_store=cursor_store, gateway_room_id="!gw:localhost")
        await mem_transport._deliver_to_room(mem_entry, make_message("mem"))
        # ...but the real sync response carries a *different* delivery.
        synced_entry = make_entry(outbox_id="mesh-outbox-realsynced")
        client = FakeNioClient(sync_response=sync_with_deliveries([synced_entry], next_batch="s2"))
        transport = make_transport(cursor_store, client)
        save_cursor(cursor_store, cursor_value="s1")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coord.resume("beta")

        # Only the real-synced entry is replayed; the in-memory entry is ignored.
        assert result.replayed_outbox_ids == ("mesh-outbox-realsynced",)

    @pytest.mark.asyncio
    async def test_resume_advances_cursor_to_real_next_batch(self, cursor_store):
        """The saved cursor becomes the real sync next_batch, not a mesh-cursor-* value."""
        entry = make_entry(outbox_id="mesh-outbox-adv")
        client = FakeNioClient(sync_response=sync_with_deliveries([entry], next_batch="s999"))
        transport = make_transport(cursor_store, client)
        save_cursor(cursor_store, cursor_value="s1")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coord.resume("beta")

        saved = cursor_store.load("beta")
        assert saved is not None
        # The advanced cursor is the real Matrix sync token.
        assert saved.cursor == "s999"
        assert result.advanced_cursor == "s999"
        # Not a synthetic mesh cursor.
        assert "mesh-cursor-" not in saved.cursor

    @pytest.mark.asyncio
    async def test_second_resume_is_idempotent_no_duplicate(self, cursor_store):
        """A repeated resume does not re-deliver the same entry (no duplicate)."""
        entry = make_entry(outbox_id="mesh-outbox-dup")
        # Scripted: first sync returns the delivery + next_batch; second sync
        # returns nothing (the token already advanced past it).
        responses = [
            sync_with_deliveries([entry], next_batch="s2"),
            FakeSyncResponse({}, next_batch="s2"),
        ]

        class ScriptedClient(FakeNioClient):
            def __init__(self):
                self.sync_calls = []
                self._idx = 0

            async def sync(self, since=None, **kwargs):
                self.sync_calls.append(since)
                resp = responses[min(self._idx, len(responses) - 1)]
                self._idx += 1
                return resp

        client = ScriptedClient()
        transport = make_transport(cursor_store, client)
        save_cursor(cursor_store, cursor_value="s1")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        first = await coord.resume("beta")
        second = await coord.resume("beta")

        assert first.replayed_outbox_ids == ("mesh-outbox-dup",)
        assert second.replayed_outbox_ids == ()
        assert second.resumed is False
        # Second sync used the advanced real token.
        assert client.sync_calls == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_resume_advances_past_cursor_outbox_in_sync(self, cursor_store):
        """A sync carrying an already-certified outbox resumes strictly after it."""
        e1 = make_entry(outbox_id="mesh-outbox-one")
        e2 = make_entry(outbox_id="mesh-outbox-two")
        client = FakeNioClient(sync_response=sync_with_deliveries([e1, e2], next_batch="s5"))
        transport = make_transport(cursor_store, client)
        # Cursor references the first entry (certified) -> only the second replays.
        save_cursor(cursor_store, cursor_value="mesh-cursor-mesh-outbox-one-123")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coord.resume("beta")

        assert result.replayed_outbox_ids == ("mesh-outbox-two",)

    @pytest.mark.asyncio
    async def test_resume_with_no_delivery_still_advances_token(self, cursor_store):
        """A resume with nothing to replay persists the advanced real token."""
        client = FakeNioClient(sync_response=FakeSyncResponse({}, next_batch="s7"))
        transport = make_transport(cursor_store, client)
        save_cursor(cursor_store, cursor_value="s1")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coord.resume("beta")

        assert result.resumed is False
        assert result.advanced_cursor == "s7"
        saved = cursor_store.load("beta")
        assert saved is not None and saved.cursor == "s7"

    @pytest.mark.asyncio
    async def test_resume_session_scoped_to_real_sync(self, cursor_store):
        """A session-scoped cursor only replays its own session's deliveries."""
        in_entry = make_entry(
            outbox_id="mesh-outbox-sess",
            target_session_id="!beta:localhost:$t",
            target_thread_id="$t",
        )
        other_entry = make_entry(
            outbox_id="mesh-outbox-other",
            target_session_id="!beta:localhost:$other",
            target_thread_id="$other",
        )
        client = FakeNioClient(
            sync_response=sync_with_deliveries([in_entry, other_entry], next_batch="s9"),
        )
        transport = make_transport(cursor_store, client)
        save_cursor(cursor_store, cursor_value="s1", session_id="!beta:localhost:$t")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coord.resume("beta", session_id="!beta:localhost:$t")

        assert result.replayed_outbox_ids == ("mesh-outbox-sess",)

    @pytest.mark.asyncio
    async def test_resume_cross_session_is_noop(self, cursor_store):
        """A resume for a different session than the saved cursor is a no-op."""
        client = FakeNioClient(sync_response=sync_with_deliveries([make_entry()], next_batch="s2"))
        transport = make_transport(cursor_store, client)
        save_cursor(cursor_store, cursor_value="s1", session_id="session-a")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coord.resume("beta", session_id="session-b")

        assert result.resumed is False
        assert result.replayed_outbox_ids == ()
        # No sync call was made for a cross-session no-op.
        assert client.sync_calls == []


# ── Default-fake path (backwards compatibility) ───────────────────────────


class TestDefaultFakePath:
    @pytest.mark.asyncio
    async def test_no_client_keeps_in_memory_replay(self, cursor_store):
        """Without an injected client, resume uses the Phase A in-memory log."""
        transport = MatrixMeshTransport(cursor_store=cursor_store, gateway_room_id="!gw:localhost")
        assert transport.client is None
        entry = make_entry(outbox_id="mesh-outbox-mem")
        await transport._deliver_to_room(entry, make_message("hi"))
        save_cursor(cursor_store, cursor_value="s1")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coord.resume("beta")

        assert result.resumed is True
        assert result.replayed_outbox_ids == ("mesh-outbox-mem",)

    @pytest.mark.asyncio
    async def test_no_client_uses_mesh_cursor_advance(self, cursor_store):
        """Without a client, the advanced cursor stays a synthetic mesh cursor."""
        transport = MatrixMeshTransport(cursor_store=cursor_store, gateway_room_id="!gw:localhost")
        entry = make_entry(outbox_id="mesh-outbox-mem")
        await transport._deliver_to_room(entry, make_message("hi"))
        save_cursor(cursor_store, cursor_value="s1")

        coord = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coord.resume("beta")

        saved = cursor_store.load("beta")
        assert saved is not None
        assert "mesh-cursor-" in saved.cursor


# ── Gateway.resume_worker end-to-end with injected client ─────────────────


class TestGatewayResumeReal:
    @pytest.mark.asyncio
    async def test_resume_worker_end_to_end_real_sync(self, cursor_store):
        """Gateway.resume_worker drives real sync replay through the coordinator."""
        entry = make_entry(outbox_id="mesh-outbox-e2e")
        client = FakeNioClient(sync_response=sync_with_deliveries([entry], next_batch="s42"))
        transport = make_transport(cursor_store, client)
        save_cursor(cursor_store, cursor_value="s1")

        gw = MeshGateway(
            transport=transport,
            cursor_store=cursor_store,
            gateway_room_id="!gw:localhost",
            resume=MeshReconnectCoordinator(
                transport=transport,
                cursor_store=cursor_store,
                lifecycle_sink=[],
                enabled=True,
            ),
        )
        gw.register_worker(
            MeshWorkerRegistration(worker_id="alpha", agent_name="a", room_id="!alpha:localhost"),
        )
        gw.register_worker(
            MeshWorkerRegistration(worker_id="beta", agent_name="b", room_id="!beta:localhost"),
        )

        result = await gw.resume_worker("beta")

        assert result.resumed is True
        assert result.replayed_outbox_ids == ("mesh-outbox-e2e",)
        saved = cursor_store.load("beta")
        assert saved is not None and saved.cursor == "s42"