"""Content-free mesh lifecycle events and outcome streaming.

Following the provenance-memory outbox pattern: the gateway emits lifecycle
events that carry *no message content* — only status, worker IDs, and timing.
This means a reconnecting worker or an observing controller can reconstruct
delivery state without seeing message payloads, preserving privacy and
reducing replay bandwidth.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "MeshLifecycleEvent",
    "MeshLifecycleEventType",
    "MeshLifecycleSink",
    "content_free_lifecycle_outcomes",
]

from collections.abc import MutableSequence

MeshLifecycleEventType = Literal[
    "worker_enrolled",
    "worker_registered",
    "worker_deregistered",
    "worker_disconnected",
    "worker_reconnected",
    "worker_session_bound",
    "message_routed",
    "message_delivered",
    "message_replayed",
    "message_failed",
    "message_cancelled",
    "message_dropped_loop",
    "message_dropped_duplicate",
    "gateway_started",
    "gateway_stopped",
]

_CONTENT_FREE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "message_routed",
        "message_delivered",
        "message_replayed",
        "message_failed",
        "message_cancelled",
        "message_dropped_loop",
        "message_dropped_duplicate",
    },
)


@dataclass(slots=True)
class MeshLifecycleEvent:
    """One content-free lifecycle event from the mesh gateway."""

    event_type: MeshLifecycleEventType
    worker_id: str | None = None
    source_worker_id: str | None = None
    target_worker_id: str | None = None
    outbox_id: str | None = None
    correlation_id: str | None = None
    timestamp: float = field(default_factory=time.time)
    cursor: str | None = None
    failure_reason: str | None = None
    cancel_source: str | None = None

    @property
    def is_content_free(self) -> bool:
        """Return whether this event carries no message content."""
        return self.event_type in _CONTENT_FREE_EVENT_TYPES

    def to_outcome(self) -> dict[str, str]:
        """Return a content-free outcome dict (no message body)."""
        return {
            "event_type": self.event_type,
            "outbox_id": self.outbox_id or "",
            "status": self.event_type.replace("message_", ""),
            "worker_id": self.worker_id or "",
            "source_worker_id": self.source_worker_id or "",
            "target_worker_id": self.target_worker_id or "",
            "cursor": self.cursor or "",
            "failure_reason": self.failure_reason or "",
            "cancel_source": self.cancel_source or "",
        }


MeshLifecycleSink = MutableSequence[MeshLifecycleEvent]


def content_free_lifecycle_outcomes(events: list[MeshLifecycleEvent]) -> dict[str, str]:
    """Reduce lifecycle events into content-free outcomes keyed by outbox_id.

    Mirrors ``MemoryPropagator.drain()`` which returns ``dict[action_id, outcome]``
    without exposing propagation payloads.
    """
    outcomes: dict[str, str] = {}
    for event in events:
        if not event.is_content_free:
            continue
        key = event.outbox_id or event.correlation_id or f"lifecycle-{event.timestamp}"
        if event.event_type == "message_delivered":
            outcomes[key] = "delivered"
        elif event.event_type == "message_replayed":
            outcomes[key] = "replayed"
        elif event.event_type == "message_failed":
            outcomes[key] = "failed"
        elif event.event_type == "message_cancelled":
            outcomes[key] = "cancelled"
        elif event.event_type == "message_routed":
            outcomes[key] = "routed"
        elif event.event_type == "message_dropped_loop":
            outcomes[key] = "dropped_loop"
        elif event.event_type == "message_dropped_duplicate":
            outcomes[key] = "dropped_duplicate"
    return outcomes
