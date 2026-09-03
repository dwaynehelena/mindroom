"""Tests for Phase B Unit 2 — real nio.AsyncClient transport injection.

Covers the injected-transport path on ``MatrixMeshTransport``: when a real
client (or a transport adapter exposing the same ``room_send`` / ``sync``
surface) is injected, ``_deliver_to_room`` posts a real Matrix event and
``_sync_from_cursor`` replays from a real sync token.  No real network is
used in unit tests — a mock/fake client records what would be sent and serves
a fabricated sync response.
"""

from __future__ import annotations

import pytest

from mindroom.mesh import MatrixMeshTransport, MeshCursorStore, MeshMessage
from mindroom.mesh.models import MeshOutboxEntry
from mindroom.mesh.transport import (
    _build_mesh_content,
    _entry_from_wire_content,
)

# ── Fake nio client (records sends; serves a scripted sync) ──────────────


class FakeRoomInfo:
    """Minimal stand-in for nio's joined-room timeline in a sync response."""

    def __init__(self, events) -> None:
        self.timeline = FakeTimeline(events)


class FakeTimeline:
    def __init__(self, events) -> None:
        self.events = events


class FakeSyncResponse:
    """Minimal sync response exposing ``.rooms.join`` like nio's SyncResponse."""

    def __init__(self, joined_rooms) -> None:
        self.rooms = FakeRooms(joined_rooms)


class FakeRooms:
    def __init__(self, join) -> None:
        self.join = join


class RoomMessageText:
    """Stand-in for ``nio.RoomMessageText`` carrying a wire envelope in source.

    Named identically to nio's event class so the transport's sync parser
    (which keys on the event class name) recognizes it as a plain-text message.
    """

    def __init__(self, content) -> None:
        self.source = content
        self.content = content


class FakeRoomSendResponse:
    """Stand-in for ``nio.RoomSendResponse`` (success)."""

    def __init__(self, event_id) -> None:
        self.event_id = event_id
        self.status_code = 200


class FakeRoomSendError:
    """Stand-in for ``nio.RoomSendError`` (failure)."""

    def __init__(self, status_code=400) -> None:
        self.status_code = status_code
        self.is_error = True


class FakeNioClient:
    """A recording fake of the nio client surface used by the transport.

    ``room_send`` records the (room_id, message_type, content) it was called
    with and returns a scripted response.  ``sync`` records the ``since`` token
    and returns a scripted sync response (or raises).
    """

    def __init__(self, send_response=None, sync_response=None, sync_error=None) -> None:
        self.sent: list[tuple[str, str, dict]] = []
        self.sync_calls: list[str] = []
        self._send_response = send_response
        self._sync_response = sync_response
        self._sync_error = sync_error

    async def room_send(self, room_id, message_type, content, **kwargs):
        self.sent.append((room_id, message_type, content))
        return self._send_response

    async def sync(self, since=None, **kwargs):
        self.sync_calls.append(since)
        if self._sync_error is not None:
            raise self._sync_error
        return self._sync_response


# ── Fixtures / helpers ────────────────────────────────────────────────────


@pytest.fixture
def cursor_store(tmp_path):
    """Return a cursor store with a temp directory."""
    return MeshCursorStore(storage_path=tmp_path)


def make_entry(
    *,
    target_worker_id="beta",
    target_room_id="!beta:localhost",
    target_thread_id=None,
    target_session_id=None,
    outbox_id="mesh-outbox-abc",
    message_id="mesh-msg-xyz",
):
    """Return a fully-populated outbox entry."""
    return MeshOutboxEntry(
        outbox_id=outbox_id,
        message_id=message_id,
        source_worker_id="alpha",
        target_worker_id=target_worker_id,
        source_room_id="!alpha:localhost",
        target_room_id=target_room_id,
        gateway_room_id="!gw:localhost",
        target_thread_id=target_thread_id,
        target_session_id=target_session_id,
    )


def make_message(content="hello"):
    """Return a mesh message."""
    return MeshMessage(
        source_worker_id="alpha",
        target_worker_id="beta",
        content=content,
        correlation_id="corr-1",
    )


def make_transport(cursor_store, client):
    """Return a transport with an injected fake client."""
    return MatrixMeshTransport(cursor_store=cursor_store, gateway_room_id="!gw:localhost", client=client)


# ── Wire content building / parsing ───────────────────────────────────────


class TestWireContent:
    def test_build_room_mode_content_embeds_envelope(self):
        entry = make_entry(target_thread_id=None)
        content = _build_mesh_content(entry, make_message("hi"))
        assert content["body"] == "hi"
        assert content["msgtype"] == "m.text"
        env = content["io.mindroom.mesh"]
        assert env["outbox_id"] == entry.outbox_id
        assert env["target_worker_id"] == "beta"
        assert env["target_room_id"] == "!beta:localhost"
        # Room-mode: no thread relation.
        assert "m.relates_to" not in content

    def test_build_thread_mode_content_attaches_thread_relation(self):
        entry = make_entry(target_thread_id="$thread-beta")
        content = _build_mesh_content(entry, make_message("hi"))
        rel = content["m.relates_to"]
        assert rel["rel_type"] == "m.thread"
        assert rel["event_id"] == "$thread-beta"

    def test_entry_from_wire_content_roundtrip(self):
        entry = make_entry(target_thread_id="$thread-beta", target_session_id="!beta:localhost:$thread-beta")
        parsed = _entry_from_wire_content(_build_mesh_content(entry, make_message("hi")))
        assert parsed is not None
        assert parsed.outbox_id == entry.outbox_id
        assert parsed.target_worker_id == "beta"
        assert parsed.target_thread_id == "$thread-beta"
        assert parsed.target_session_id == "!beta:localhost:$thread-beta"

    def test_entry_from_non_mesh_content_is_none(self):
        assert _entry_from_wire_content({"msgtype": "m.text", "body": "no envelope"}) is None
        assert _entry_from_wire_content("not a dict") is None
        assert _entry_from_wire_content({"io.mindroom.mesh": {}}) is None


# ── Injected _deliver_to_room ─────────────────────────────────────────────


class TestInjectedDeliver:
    @pytest.mark.asyncio
    async def test_room_mode_delivers_via_real_client(self, cursor_store):
        client = FakeNioClient(send_response=FakeRoomSendResponse(event_id="$e1"))
        transport = make_transport(cursor_store, client)
        entry = make_entry()
        await transport._deliver_to_room(entry, make_message("hello"))
        assert len(client.sent) == 1
        room_id, msg_type, content = client.sent[0]
        assert room_id == "!beta:localhost"
        assert msg_type == "m.room.message"
        assert content["io.mindroom.mesh"]["outbox_id"] == entry.outbox_id
        # In-memory fake is not used when a real client is injected.
        assert transport.get_delivered_messages("!beta:localhost") == []

    @pytest.mark.asyncio
    async def test_thread_mode_delivery_posts_thread_relation(self, cursor_store):
        client = FakeNioClient(send_response=FakeRoomSendResponse(event_id="$e2"))
        transport = make_transport(cursor_store, client)
        entry = make_entry(target_thread_id="$thread-beta")
        await transport._deliver_to_room(entry, make_message("hi"))
        _, _, content = client.sent[0]
        assert content["m.relates_to"]["rel_type"] == "m.thread"
        assert content["m.relates_to"]["event_id"] == "$thread-beta"

    @pytest.mark.asyncio
    async def test_full_deliver_marks_delivered_and_saves_cursor(self, cursor_store):
        client = FakeNioClient(send_response=FakeRoomSendResponse(event_id="$e3"))
        transport = make_transport(cursor_store, client)
        entry = make_entry()
        status = await transport.deliver(entry, make_message("hi"))
        assert status == "delivered"
        assert entry.status == "delivered"
        assert entry.delivered_at is not None
        assert cursor_store.load("beta") is not None

    @pytest.mark.asyncio
    async def test_send_error_marks_failed(self, cursor_store):
        client = FakeNioClient(send_response=FakeRoomSendError(status_code=403))
        transport = make_transport(cursor_store, client)
        entry = make_entry()
        status = await transport.deliver(entry, make_message("hi"))
        assert status == "failed"
        assert entry.status == "failed"
        assert entry.failure_reason is not None
        assert "matrix_room_send_failed" in entry.failure_reason

    @pytest.mark.asyncio
    async def test_transport_exception_marks_failed(self, cursor_store):
        class Boom(Exception):
            pass

        client = FakeNioClient(send_response=FakeRoomSendResponse(event_id="$e4"))
        transport = make_transport(cursor_store, client)

        async def exploding_send(**_):
            raise Boom("network down")

        client.room_send = exploding_send
        entry = make_entry()
        status = await transport.deliver(entry, make_message("hi"))
        assert status == "failed"
        assert entry.status == "failed"

    @pytest.mark.asyncio
    async def test_client_without_room_send_is_rejected(self, cursor_store):
        transport = make_transport(cursor_store, client=object())
        entry = make_entry()
        status = await transport.deliver(entry, make_message("hi"))
        assert status == "failed"
        assert entry.status == "failed"
        assert "room_send" in (entry.failure_reason or "")


# ── Injected _sync_from_cursor ────────────────────────────────────────────


def _sync_with_delivery(*, worker="beta", session_id=None, outbox_id="mesh-outbox-abc", thread_id=None):
    """Build a fake sync response carrying one mesh delivery to ``worker``."""
    entry = make_entry(
        target_worker_id=worker,
        target_session_id=session_id,
        target_thread_id=thread_id,
        outbox_id=outbox_id,
    )
    event = RoomMessageText(_build_mesh_content(entry, make_message("replayed")))
    return FakeSyncResponse({"!beta:localhost": FakeRoomInfo([event])})


class TestInjectedSync:
    @pytest.mark.asyncio
    async def test_sync_uses_real_cursor_token(self, cursor_store):
        client = FakeNioClient(sync_response=FakeSyncResponse({}))
        transport = make_transport(cursor_store, client)
        cursor_store.save(
            _cursor(cursor_store, worker_id="beta", cursor_value="s123"),
        )
        entries = await transport._sync_from_cursor("beta")
        assert entries == ()
        assert client.sync_calls == ["s123"]

    @pytest.mark.asyncio
    async def test_sync_replays_mesh_delivery_for_worker(self, cursor_store):
        client = FakeNioClient(sync_response=_sync_with_delivery(worker="beta", outbox_id="mesh-outbox-aaa"))
        transport = make_transport(cursor_store, client)
        cursor_store.save(_cursor(cursor_store, worker_id="beta", cursor_value="s1"))
        entries = await transport._sync_from_cursor("beta")
        assert len(entries) == 1
        assert entries[0].outbox_id == "mesh-outbox-aaa"
        assert entries[0].target_worker_id == "beta"

    @pytest.mark.asyncio
    async def test_sync_filters_to_requesting_worker(self, cursor_store):
        # Sync carries a delivery to a different worker -> filtered out.
        client = FakeNioClient(sync_response=_sync_with_delivery(worker="other", outbox_id="mesh-outbox-other"))
        transport = make_transport(cursor_store, client)
        cursor_store.save(_cursor(cursor_store, worker_id="beta", cursor_value="s1"))
        entries = await transport._sync_from_cursor("beta")
        assert entries == ()

    @pytest.mark.asyncio
    async def test_sync_filters_by_session_when_scoped(self, cursor_store):
        client = FakeNioClient(
            sync_response=_sync_with_delivery(
                worker="beta",
                session_id="!beta:localhost:$thread-beta",
                outbox_id="mesh-outbox-in",
                thread_id="$thread-beta",
            ),
        )
        transport = make_transport(cursor_store, client)
        cursor_store.save(
            _cursor(
                cursor_store,
                worker_id="beta",
                cursor_value="s1",
                session_id="!beta:localhost:$thread-beta",
            ),
        )
        entries = await transport._sync_from_cursor("beta")
        assert [e.outbox_id for e in entries] == ["mesh-outbox-in"]

    @pytest.mark.asyncio
    async def test_sync_resumes_strictly_after_cursor_outbox(self, cursor_store):
        # Sync carries two deliveries; the cursor references the first one's
        # outbox id -> only the second is replayed.
        e1 = make_entry(outbox_id="mesh-outbox-one")
        e2 = make_entry(outbox_id="mesh-outbox-two")
        sync = FakeSyncResponse(
            {
                "!beta:localhost": FakeRoomInfo(
                    [
                        RoomMessageText(_build_mesh_content(e1, make_message("a"))),
                        RoomMessageText(_build_mesh_content(e2, make_message("b"))),
                    ],
                ),
            },
        )
        client = FakeNioClient(sync_response=sync)
        transport = make_transport(cursor_store, client)
        cursor_store.save(
            _cursor(
                cursor_store,
                worker_id="beta",
                cursor_value="mesh-cursor-mesh-outbox-one-123",
            ),
        )
        entries = await transport._sync_from_cursor("beta")
        assert [e.outbox_id for e in entries] == ["mesh-outbox-two"]

    @pytest.mark.asyncio
    async def test_sync_without_cursor_returns_empty(self, cursor_store):
        client = FakeNioClient(sync_response=_sync_with_delivery())
        transport = make_transport(cursor_store, client)
        entries = await transport._sync_from_cursor("beta")
        assert entries == ()
        assert client.sync_calls == []

    @pytest.mark.asyncio
    async def test_sync_exception_surfaces(self, cursor_store):
        client = FakeNioClient(sync_error=RuntimeError("no sync"))
        transport = make_transport(cursor_store, client)
        cursor_store.save(_cursor(cursor_store, worker_id="beta", cursor_value="s1"))
        with pytest.raises(Exception):
            await transport._sync_from_cursor("beta")

    @pytest.mark.asyncio
    async def test_resume_replays_real_synced_delivery(self, cursor_store):
        """The resume coordinator drives the injected transport's real sync."""
        from mindroom.mesh.reconnect import MeshReconnectCoordinator

        client = FakeNioClient(sync_response=_sync_with_delivery(outbox_id="mesh-outbox-resume"))
        transport = make_transport(cursor_store, client)
        cursor_store.save(_cursor(cursor_store, worker_id="beta", cursor_value="s1"))
        coordinator = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coordinator.resume("beta")
        assert result.resumed is True
        assert result.replayed_outbox_ids == ("mesh-outbox-resume",)
        assert result.advanced_cursor is not None


def _cursor(cursor_store, *, worker_id, cursor_value, session_id=None):
    """Return a durable reconnect cursor (and save helper)."""
    from mindroom.mesh import MeshReconnectCursor

    return MeshReconnectCursor(
        worker_id=worker_id,
        cursor=cursor_value,
        cache_generation=cursor_value,
        session_id=session_id,
    )


# ── Backwards compatibility: no client injected → in-memory fake ──────────


class TestBackwardsCompatibility:
    @pytest.mark.asyncio
    async def test_default_transport_still_uses_fake_queue(self, cursor_store):
        transport = MatrixMeshTransport(cursor_store=cursor_store, gateway_room_id="!gw:localhost")
        assert transport.client is None
        entry = make_entry()
        await transport._deliver_to_room(entry, make_message("hello"))
        delivered = transport.get_delivered_messages("!beta:localhost")
        assert len(delivered) == 1
        assert delivered[0][0].outbox_id == entry.outbox_id

    @pytest.mark.asyncio
    async def test_default_sync_uses_in_memory_log(self, cursor_store):
        transport = MatrixMeshTransport(cursor_store=cursor_store, gateway_room_id="!gw:localhost")
        entry = make_entry(outbox_id="mesh-outbox-mem")
        await transport._deliver_to_room(entry, make_message("hi"))
        cursor_store.save(_cursor(cursor_store, worker_id="beta", cursor_value="mesh-cursor-mesh-outbox-mem-1"))
        entries = await transport._sync_from_cursor("beta")
        assert [e.outbox_id for e in entries] == []
