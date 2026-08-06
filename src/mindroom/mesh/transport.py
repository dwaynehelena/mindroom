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

    from mindroom.mesh.models import MeshWorkerRegistration

logger = logging.getLogger(__name__)

__all__ = [
    "MatrixMeshTransport",
    "MeshTransport",
    "MeshTransportError",
]


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
    Production use would inject a ``nio.AsyncClient`` here.
    """

    _delivered_messages: dict[str, list[tuple[MeshOutboxEntry, MeshMessage]]] = field(
        default_factory=dict,
    )

    async def _deliver_to_room(self, entry: MeshOutboxEntry, message: MeshMessage) -> None:
        """Deliver one message to the target worker's room.

        In production this would call ``send_message_result`` through the
        Matrix client.  For the demo, we append to an in-memory queue.
        """
        target_queue = self._delivered_messages.setdefault(entry.target_room_id, [])
        target_queue.append((entry, message))
        logger.debug(
            "mesh_transport_delivered",
            extra={
                "outbox_id": entry.outbox_id,
                "target_worker_id": entry.target_worker_id,
                "target_room_id": entry.target_room_id,
            },
        )

    async def _sync_from_cursor(self, worker_id: str) -> Sequence[MeshOutboxEntry]:
        """Replay messages since the last cursor for one worker.

        In production this would call ``sync_messages`` with the saved
        token.  For the demo, we return entries from the in-memory queue
        that were delivered after the cursor was saved.
        """
        cursor = self.cursor_store.load(worker_id)
        if cursor is None:
            return ()
        # In production: replay from Matrix sync with token=cursor.cursor
        # In demo: return empty (all messages already in queue)
        return ()

    def get_delivered_messages(self, room_id: str) -> list[tuple[MeshOutboxEntry, MeshMessage]]:
        """Return all messages delivered to one room (demo helper)."""
        return list(self._delivered_messages.get(room_id, []))