"""Mesh models: messages, routing decisions, worker registration, and outbox entries."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from mindroom.session_ids import create_session_id

__all__ = [
    "MeshMessage",
    "MeshMessageEnvelope",
    "MeshOutboxEntry",
    "MeshRouteDecision",
    "MeshWorkerRegistration",
    "MeshWorkerStatus",
]

MeshWorkerStatus = Literal["registered", "active", "disconnected", "deregistered"]
MeshDeliveryStatus = Literal["pending", "delivered", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class MeshWorkerRegistration:
    """Static registration for one mesh worker."""

    worker_id: str
    agent_name: str
    room_id: str
    endpoint: str | None = None
    auth_token: str | None = None  # noqa: S105 - structural, not a literal secret
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        """Return the canonical session ID for this worker's room."""
        return create_session_id(self.room_id, thread_id=None)


@dataclass(frozen=True, slots=True)
class MeshMessage:
    """Payload carried between mesh workers through the gateway."""

    source_worker_id: str
    target_worker_id: str
    content: str
    correlation_id: str
    created_at: float = field(default_factory=time.time)
    hop_count: int = 0
    trace: tuple[str, ...] = ()

    def with_hop(self) -> MeshMessage:
        """Return a copy advanced one hop with an appended trace entry."""
        return MeshMessage(
            source_worker_id=self.source_worker_id,
            target_worker_id=self.target_worker_id,
            content=self.content,
            correlation_id=self.correlation_id,
            created_at=self.created_at,
            hop_count=self.hop_count + 1,
            trace=self.trace + (self.source_worker_id,),
        )


@dataclass(frozen=True, slots=True)
class MeshMessageEnvelope:
    """Envelope wrapping a mesh message with routing metadata and outbox tracking."""

    message: MeshMessage
    route: MeshRouteDecision
    outbox_id: str
    delivery_status: MeshDeliveryStatus = "pending"
    delivered_at: float | None = None
    failure_reason: str | None = None
    cancel_source: str | None = None


@dataclass(frozen=True, slots=True)
class MeshRouteDecision:
    """Gateway routing decision for one mesh message."""

    source_worker_id: str
    target_worker_id: str
    source_room_id: str
    target_room_id: str
    gateway_room_id: str
    relay: bool = True


@dataclass(slots=True)
class MeshOutboxEntry:
    """Durable outbox row tracking one message through the gateway.

    Follows the provenance-memory outbox pattern: the gateway writes an outbox
    row before attempting delivery, marks it delivered after success, and marks
    it failed or cancelled after terminal failure.  The outbox is content-free
    in lifecycle outcomes — only status and metadata are reported, never the
    message body.
    """

    outbox_id: str
    message_id: str
    source_worker_id: str
    target_worker_id: str
    source_room_id: str
    target_room_id: str
    gateway_room_id: str
    status: MeshDeliveryStatus = "pending"
    created_at: float = field(default_factory=time.time)
    delivered_at: float | None = None
    failure_reason: str | None = None
    cancel_source: str | None = None
    cursor: str | None = None
    target_session_id: str | None = None
