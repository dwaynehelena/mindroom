"""Mesh transport layer: abstract transport and Matrix-backed implementation.

The transport owns the wire-level delivery of mesh messages between workers.
For the gateway-only runtime, the Matrix transport is the active path — it
sends and receives messages through Matrix rooms, using sync tokens as
reconnect cursors.

When the execution gate is closed (gateway-only mode), the transport is fully
functional for routing, but no worker-side execution occurs.  This is the
core of the P1 gateway-only runtime enablement.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mindroom.mesh.cursor import MeshCursorStore, MeshReconnectCursor
from mindroom.mesh.lifecycle import MeshLifecycleEvent, MeshLifecycleSink
from mindroom.mesh.models import MeshDeliveryStatus, MeshMessage, MeshOutboxEntry

if TYPE_CHECKING:
    from collections.abc import Sequence


logger = logging.getLogger(__name__)

#: Content key under which the mesh wire envelope is carried inside a real
#: Matrix ``m.room.message`` so the injected client's sync stream can
#: reconstruct durable ``MeshOutboxEntry`` rows (and resume from a real sync
#: token).  The user-facing body stays under ``body``; routing metadata lives
#: under this key only.
_MESH_WIRE_KEY = "io.mindroom.mesh"

__all__ = [
    "MatrixMeshTransport",
    "MeshTransport",
    "MeshTransportError",
]


def _build_mesh_content(entry: MeshOutboxEntry, message: MeshMessage) -> dict:
    """Build Matrix message content that carries the mesh wire envelope.

    The user-facing body is ``message.content``; routing metadata (outbox id,
    worker ids, rooms, optional thread) is embedded under ``_MESH_WIRE_KEY`` so
    a later real sync can reconstruct the ``MeshOutboxEntry``.  When the target
    is thread-scoped (``target_thread_id`` set), a MSC3440 ``m.relates_to``
    thread relation is attached so the real homeserver threads the message
    correctly.
    """
    content: dict = {
        "msgtype": "m.text",
        "body": message.content,
        "format": "org.matrix.custom.html",
        "formatted_body": message.content,
        _MESH_WIRE_KEY: {
            "outbox_id": entry.outbox_id,
            "message_id": entry.message_id,
            "source_worker_id": entry.source_worker_id,
            "target_worker_id": entry.target_worker_id,
            "source_room_id": entry.source_room_id,
            "target_room_id": entry.target_room_id,
            "gateway_room_id": entry.gateway_room_id,
            "target_thread_id": entry.target_thread_id,
            "target_session_id": entry.target_session_id,
        },
    }
    if entry.target_thread_id:
        content["m.relates_to"] = {
            "rel_type": "m.thread",
            "event_id": entry.target_thread_id,
        }
    return content


def _entry_from_wire_content(content: object) -> MeshOutboxEntry | None:
    """Reconstruct a ``MeshOutboxEntry`` from a synced Matrix message content.

    Returns ``None`` when the content carries no ``io.mindroom.mesh`` envelope
    (i.e. it is not a mesh delivery), preserving the provenance-memory rule:
    only mesh deliveries are replayed, never arbitrary room chatter.
    """
    if not isinstance(content, dict):
        return None
    env = content.get(_MESH_WIRE_KEY)
    if not isinstance(env, dict):
        return None
    try:
        return MeshOutboxEntry(
            outbox_id=env["outbox_id"],
            message_id=env["message_id"],
            source_worker_id=env["source_worker_id"],
            target_worker_id=env["target_worker_id"],
            source_room_id=env["source_room_id"],
            target_room_id=env["target_room_id"],
            gateway_room_id=env["gateway_room_id"],
            target_thread_id=env.get("target_thread_id"),
            target_session_id=env.get("target_session_id"),
        )
    except (KeyError, TypeError):
        return None


def _sync_next_batch(response: object) -> str | None:
    """Extract the ``next_batch`` sync token from a real (or fake) sync response.

    The ``next_batch`` is the authoritative Matrix ``since`` token covering
    everything the sync delivered.  It is what the coordinator persists as the
    advanced resume cursor so a subsequent real sync resumes strictly after the
    replayed window (no duplicate delivery, no full replay).
    """
    nb = getattr(response, "next_batch", None)
    if isinstance(nb, str) and nb:
        return nb
    return None


def _is_room_send_error(response: object) -> bool:
    """Return whether a room_send response represents a Matrix send error.

    Handles nio's ``RoomSendError`` and adapter fakes that expose an
    ``is_error``/``status_code`` marker.  A plain success mapping (e.g. a fake
    returning ``{"event_id": ...}``) is not an error.
    """
    if getattr(response, "is_error", False) is True:
        return True
    # nio error responses carry a non-2xx ``status_code``.
    status = getattr(response, "status_code", None)
    return status is not None and status not in (200, 201, 202)


def _message_content_from_source(source: object) -> dict:
    """Extract the Matrix message ``content`` from an event's raw ``source``.

    nio attaches the full event JSON as ``event.source``; the message content
    (body + wire envelope + relations) lives under ``source["content"]``.
    Adapter fakes may set ``source`` directly to the content mapping.
    """
    if isinstance(source, dict) and isinstance(source.get("content"), dict):
        return source["content"]  # type: ignore[return-value]
    if isinstance(source, dict):
        return source
    return {}


def _entries_from_sync_response(response: object) -> Sequence[MeshOutboxEntry]:
    """Extract mesh outbox entries from a real (or fake) Matrix sync response.

    Walks the joined-room timelines of the sync response and reconstructs a
    durable ``MeshOutboxEntry`` from every ``m.room.message`` event carrying the
    ``io.mindroom.mesh`` wire envelope.  Non-mesh events are ignored so replay
    only touches mesh deliveries (provenance-memory rule).
    """
    entries: list[MeshOutboxEntry] = []
    rooms = getattr(response, "rooms", None)
    if rooms is None:
        return ()
    if isinstance(rooms, dict):
        joined = {rid: room for rid, room in rooms.items() if room is not None}
    else:
        join = getattr(rooms, "join", None)
        joined = join if isinstance(join, dict) else {}
    for joined_room in joined.values():
        timeline = getattr(joined_room, "timeline", None)
        if timeline is None:
            continue
        events = getattr(timeline, "events", ()) or ()
        for event in events:
            if event.__class__.__name__ != "RoomMessageText":
                # Only plain-text mesh deliveries are replayed; ignore reactions,
                # edits, media and non-mesh chatter.
                continue
            # nio's event ``source`` is the full event JSON; the Matrix message
            # content (carrying the wire envelope) lives under ``source["content"]``.
            content = _message_content_from_source(getattr(event, "source", None))
            entry = _entry_from_wire_content(content)
            if entry is not None:
                entries.append(entry)
    return entries


def _cursor_outbox_id(cursor_value: str) -> str | None:
    """Extract the outbox_id embedded in a ``mesh-cursor-<outbox_id>-<ts>`` value.

    Returns ``None`` when the cursor does not reference a mesh outbox.  Used by
    ``_sync_from_cursor`` to locate the exact last-certified entry so replay
    resumes strictly after it (no full replay).
    """
    marker = "mesh-outbox-"
    idx = cursor_value.find(marker)
    if idx == -1:
        return None
    hex_part = cursor_value[idx + len(marker) :].split("-", 1)[0]
    if not hex_part:
        return None
    return f"mesh-outbox-{hex_part}"


class MeshTransportError(RuntimeError):
    """Raised when the mesh transport cannot satisfy a delivery."""


@dataclass
class MeshTransport:
    """Abstract mesh transport: owns wire-level message delivery.

    Concrete implementations (e.g. ``MatrixMeshTransport``) override
    ``_deliver_to_room`` and ``_sync_from_cursor``.
    """

    cursor_store: MeshCursorStore
    lifecycle_sink: MeshLifecycleSink = field(default_factory=list)
    gateway_room_id: str = ""

    async def deliver(
        self,
        entry: MeshOutboxEntry,
        message: MeshMessage,
    ) -> MeshDeliveryStatus:
        """Deliver one outbox entry through the transport.

        Returns the terminal delivery status.  On success, a reconnect
        cursor is saved so the target worker can resume from this point.
        """
        try:
            await self._deliver_to_room(entry, message)
        except asyncio.CancelledError:
            await self._mark_cancelled(entry, cancel_source="sync_restart")
            raise
        except MeshTransportError as exc:
            await self._mark_failed(entry, failure_reason=str(exc))
            return "failed"
        except Exception as exc:
            await self._mark_failed(entry, failure_reason=f"{type(exc).__name__}: {exc}")
            return "failed"

        await self._mark_delivered(entry)
        return "delivered"

    async def _deliver_to_room(self, entry: MeshOutboxEntry, message: MeshMessage) -> None:
        """Send one message to the target room via the concrete transport."""
        raise NotImplementedError

    async def _sync_from_cursor(self, worker_id: str) -> Sequence[MeshOutboxEntry]:
        """Replay messages since the last cursor for one worker."""
        raise NotImplementedError

    async def _mark_delivered(self, entry: MeshOutboxEntry) -> None:
        """Record successful delivery, save cursor, and emit lifecycle event."""
        entry.status = "delivered"
        entry.delivered_at = time.time()
        cursor = self._next_cursor(entry)
        entry.cursor = cursor
        self.cursor_store.save(
            MeshReconnectCursor(
                worker_id=entry.target_worker_id,
                cursor=cursor,
                cache_generation=cursor,
            ),
        )
        self.lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="message_delivered",
                source_worker_id=entry.source_worker_id,
                target_worker_id=entry.target_worker_id,
                outbox_id=entry.outbox_id,
                correlation_id=entry.message_id if hasattr(entry, "message_id") else None,
                cursor=cursor,
            ),
        )

    async def _mark_failed(self, entry: MeshOutboxEntry, *, failure_reason: str) -> None:
        """Record delivery failure and emit lifecycle event."""
        entry.status = "failed"
        entry.failure_reason = failure_reason
        self.lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="message_failed",
                source_worker_id=entry.source_worker_id,
                target_worker_id=entry.target_worker_id,
                outbox_id=entry.outbox_id,
                failure_reason=failure_reason,
            ),
        )

    async def _mark_cancelled(self, entry: MeshOutboxEntry, *, cancel_source: str) -> None:
        """Record delivery cancellation and emit lifecycle event."""
        entry.status = "cancelled"
        entry.cancel_source = cancel_source
        self.lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="message_cancelled",
                source_worker_id=entry.source_worker_id,
                target_worker_id=entry.target_worker_id,
                outbox_id=entry.outbox_id,
                cancel_source=cancel_source,
            ),
        )

    def _next_cursor(self, entry: MeshOutboxEntry) -> str:
        """Generate the next cursor value for a delivered entry."""
        return f"mesh-cursor-{entry.outbox_id}-{int(entry.delivered_at or time.time())}"


@dataclass
class MatrixMeshTransport(MeshTransport):
    """Matrix-room-backed mesh transport.

    Uses Matrix rooms as the wire protocol: messages are sent to the target
    worker's room, and sync tokens serve as reconnect cursors.  When the
    execution gate is closed, this transport is fully active for routing
    but the receiving worker does not execute any tools.

    For the live demo, this transport uses an in-memory message queue to
    simulate Matrix room delivery without requiring a real homeserver.

    Phase B Unit 2 (real transport injection): when a real ``nio.AsyncClient``
    (or a transport adapter exposing the same ``room_send`` / ``sync`` surface)
    is injected via ``client``, ``_deliver_to_room`` posts a real Matrix event
    into the target room/thread and ``_sync_from_cursor`` replays from a real
    sync token.  The in-memory fake remains the default when no client is
    injected, preserving full backwards compatibility.
    """

    _delivered_messages: dict[str, list[tuple[MeshOutboxEntry, MeshMessage]]] = field(
        default_factory=dict,
    )
    #: Real Matrix client (or transport adapter).  When ``None`` (default) the
    #: transport uses the in-memory fake queue.  When set, delivery and sync go
    #: through this client against a real homeserver.
    client: object | None = None

    async def _deliver_to_room(self, entry: MeshOutboxEntry, message: MeshMessage) -> None:
        """Deliver one message to the target worker's room/thread.

        When a real client is injected, this posts a real Matrix ``m.room.message``
        carrying the mesh wire envelope (with a thread relation when the target is
        thread-scoped) into the target room/thread.  A ``RoomSendError`` or a
        transport failure raises ``MeshTransportError`` so the outbox is marked
        failed.  When no client is injected (default), this appends to the
        in-memory fake queue keyed by room (and honors ``target_thread_id`` so
        thread-aware routing is faithfully simulated locally).
        """
        if self.client is not None:
            await self._deliver_to_room_real(entry, message)
            return
        target_queue = self._delivered_messages.setdefault(entry.target_room_id, [])
        target_queue.append((entry, message))
        logger.debug(
            "mesh_transport_delivered",
            extra={
                "outbox_id": entry.outbox_id,
                "target_worker_id": entry.target_worker_id,
                "target_room_id": entry.target_room_id,
                "target_thread_id": entry.target_thread_id,
            },
        )

    async def _deliver_to_room_real(self, entry: MeshOutboxEntry, message: MeshMessage) -> None:
        """Post one mesh delivery as a real Matrix event via the injected client."""
        send = getattr(self.client, "room_send", None)
        if send is None or not callable(send):
            msg = "Injected mesh transport client has no room_send method"
            raise MeshTransportError(msg)
        content = _build_mesh_content(entry, message)
        try:
            response = await send(
                room_id=entry.target_room_id,
                message_type="m.room.message",
                content=content,
                ignore_unverified_devices=True,
            )
        except Exception as exc:  # surface transport failures
            reason = f"{type(exc).__name__}: {exc}"
            raise MeshTransportError(reason) from exc
        if response is None:
            msg = "mesh_transport_real_send_returned_none"
            raise MeshTransportError(msg)
        # Normalize the response: a RoomSendResponse carries a real event_id; an
        # error response raises so the outbox is marked failed.  Adapter fakes may
        # return a plain mapping with an event_id or a status.
        if _is_room_send_error(response):
            err = f"matrix_room_send_failed: {response}"
            raise MeshTransportError(err)
        logger.debug(
            "mesh_transport_real_delivered",
            extra={
                "outbox_id": entry.outbox_id,
                "target_room_id": entry.target_room_id,
                "target_thread_id": entry.target_thread_id,
            },
        )

    async def _sync_from_cursor(self, worker_id: str) -> Sequence[MeshOutboxEntry]:
        """Replay outbox entries delivered to a worker since the last cursor.

        When a real client is injected, this replays from the real Matrix sync
        token stored in the worker's cursor (``cursor.cursor``) against the live
        homeserver, reconstructing durable ``MeshOutboxEntry`` rows from the
        mesh wire envelope carried in synced ``m.room.message`` events.  A
        session-scoped cursor only replays entries for its own session.  When no
        client is injected (default), this walks the in-memory delivery log as in
        Phase A.
        """
        if self.client is not None:
            entries, _token = await self._sync_from_cursor_real(worker_id)
            return entries
        cursor = self.cursor_store.load(worker_id)
        if cursor is None:
            return ()
        # Locate the exact last-certified entry (if the cursor references one)
        # so replay resumes strictly after it — no full replay.
        last_outbox_id = _cursor_outbox_id(cursor.cursor)
        # Session scoping comes from the cursor itself.
        target_session = cursor.session_id

        # Build the worker's delivered entries in delivery-log order (the same
        # order they were appended by ``_deliver_to_room``), filtered by the
        # optional target session.
        ordered: list[MeshOutboxEntry] = []
        for entries in self._delivered_messages.values():
            for entry, _message in entries:
                if entry.target_worker_id != worker_id:
                    continue
                if target_session is not None and entry.target_session_id != target_session:
                    continue
                ordered.append(entry)

        if last_outbox_id is None:
            # No mesh outbox referenced by the cursor — resume conservatively
            # from the first delivered entry (still idempotent at the
            # coordinator: it skips entries already certified elsewhere).
            return tuple(ordered)

        # Resume strictly after the last-certified entry.
        for idx, entry in enumerate(ordered):
            if entry.outbox_id == last_outbox_id:
                return tuple(ordered[idx + 1 :])
        # The certified entry is not in the local log (e.g. it was delivered on
        # a previous process whose in-memory log was rebuilt).  Replaying the
        # whole ordered log is not a "full replay" of undelivered work — the
        # coordinator still skips already-certified entries — but here we
        # conservatively replay nothing to avoid any duplication.
        return ()

    async def _sync_from_cursor_real(
        self, worker_id: str,
    ) -> tuple[Sequence[MeshOutboxEntry], str | None]:
        """Replay mesh deliveries from a real Matrix sync token.

        Uses the worker's saved cursor token (``cursor.cursor``) as the ``since``
        for a real sync against the injected client, then reconstructs durable
        ``MeshOutboxEntry`` rows from the mesh wire envelope carried in synced
        ``m.room.message`` events.  A session-scoped cursor filters to its own
        session; a mesh outbox reference embedded in the cursor token resumes
        strictly after that certified entry (no full replay).

        Returns ``(entries, next_batch)``: the replayed entries (in sync order)
        and the authoritative ``next_batch`` sync token from the response.  The
        ``next_batch`` is what the coordinator persists as the advanced resume
        cursor so a subsequent real sync resumes strictly after the replayed
        window — the cursor is a real Matrix sync ``since`` token, not a synthetic
        ``mesh-cursor-*`` value.
        """
        sync = getattr(self.client, "sync", None)
        if sync is None or not callable(sync):
            msg = "Injected mesh transport client has no sync method"
            raise MeshTransportError(msg)

        cursor = self.cursor_store.load(worker_id)
        if cursor is None:
            return (), None
        since = cursor.cursor
        target_session = cursor.session_id
        last_outbox_id = _cursor_outbox_id(cursor.cursor)

        try:
            response = await sync(since=since, timeout=0)
        except Exception as exc:  # surface transport failures
            reason = f"{type(exc).__name__}: {exc}"
            raise MeshTransportError(reason) from exc
        if response is None:
            msg = "mesh_transport_real_sync_returned_none"
            raise MeshTransportError(msg)

        entries = _entries_from_sync_response(response)
        next_batch = _sync_next_batch(response)
        # Filter to the requesting worker and (when session-scoped) its session.
        ordered = [
            entry
            for entry in entries
            if entry.target_worker_id == worker_id
            and (target_session is None or entry.target_session_id == target_session)
        ]

        if last_outbox_id is None:
            return tuple(ordered), next_batch
        for idx, entry in enumerate(ordered):
            if entry.outbox_id == last_outbox_id:
                return tuple(ordered[idx + 1 :]), next_batch
        # The certified entry is not in the synced window (e.g. it was delivered
        # before this cursor).  Resume conservatively from the first synced
        # entry; the coordinator still skips already-certified entries.
        return tuple(ordered), next_batch

    def get_delivered_messages(self, room_id: str) -> list[tuple[MeshOutboxEntry, MeshMessage]]:
        """Return all messages delivered to one room (demo helper)."""
        return list(self._delivered_messages.get(room_id, []))
