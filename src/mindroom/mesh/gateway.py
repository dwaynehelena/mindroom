"""Gateway-only runtime mode and execution gating.

The gateway-only runtime mode is the core of the P1 enablement:

1. **Transport active** — Matrix sync loop, event cache, delivery gateway all
   running normally.  Messages flow in and out.

2. **Worker execution gated** — the execution gate blocks any tool execution
   that would normally be routed to a sandbox runner or dedicated worker.
   The gateway routes messages between workers but does not invoke any
   worker-side execution.

This mode is controlled by the ``MINDROOM_MESH_GATEWAY_MODE`` environment
variable, which can be set to ``gateway_only`` or ``full`` (default).
"""

from __future__ import annotations

import enum
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mindroom.mesh.cursor import MeshCursorStore
from mindroom.mesh.lifecycle import (
    MeshLifecycleEvent,
    MeshLifecycleSink,
    content_free_lifecycle_outcomes,
)
from mindroom.mesh.loop_guard import MeshLoopError, MeshLoopGuard
from mindroom.mesh.models import (
    MeshDeliveryStatus,
    MeshMessage,
    MeshMessageEnvelope,
    MeshOutboxEntry,
    MeshRouteDecision,
    MeshWorkerRegistration,
    MeshWorkerStatus,
)
from mindroom.mesh.transport import MatrixMeshTransport, MeshTransport, MeshTransportError

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "GatewayExecutionGate",
    "GatewayOnlyRuntime",
    "GatewayRuntimeMode",
    "MeshGateway",
    "MeshGatewayError",
]

_MESH_GATEWAY_MODE_ENV = "MINDROOM_MESH_GATEWAY_MODE"


class GatewayRuntimeMode(enum.Enum):
    """Runtime mode controlling gateway vs. full execution."""

    FULL = "full"
    GATEWAY_ONLY = "gateway_only"

    @property
    def is_gateway_only(self) -> bool:
        """Return whether worker execution is gated in this mode."""
        return self is GatewayRuntimeMode.GATEWAY_ONLY

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> GatewayRuntimeMode:
        """Resolve the runtime mode from the environment."""
        source = env if env is not None else os.environ
        raw = (source.get(_MESH_GATEWAY_MODE_ENV) or "").strip().lower()
        if raw in ("", "full"):
            return cls.FULL
        if raw in ("gateway_only", "gateway-only", "gatewayonly"):
            return cls.GATEWAY_ONLY
        return cls.FULL


class MeshGatewayError(RuntimeError):
    """Raised when the mesh gateway cannot satisfy a request."""


@dataclass
class GatewayExecutionGate:
    """Execution gate that blocks worker execution in gateway-only mode.

    When closed (gateway-only mode), any attempt to execute tools through
    a worker backend will raise ``MeshGatewayError``.  When open (full mode),
    execution proceeds normally.

    Thread-safe: the gate can be toggled at runtime to enable/disable
    worker execution dynamically.
    """

    mode: GatewayRuntimeMode = GatewayRuntimeMode.FULL
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _closed: bool = False

    def __post_init__(self) -> None:
        self._closed = self.mode.is_gateway_only

    @property
    def is_closed(self) -> bool:
        """Return whether the gate is currently blocking execution."""
        with self._lock:
            return self._closed

    @property
    def is_open(self) -> bool:
        """Return whether the gate allows worker execution."""
        return not self.is_closed

    def close(self) -> None:
        """Close the gate — block worker execution (enter gateway-only mode)."""
        with self._lock:
            self._closed = True
            self.mode = GatewayRuntimeMode.GATEWAY_ONLY
        logger.info("mesh_execution_gate_closed")

    def open(self) -> None:
        """Open the gate — allow worker execution (enter full mode)."""
        with self._lock:
            self._closed = False
            self.mode = GatewayRuntimeMode.FULL
        logger.info("mesh_execution_gate_opened")

    def check(self) -> None:
        """Raise if the gate is closed and execution is blocked."""
        if self.is_closed:
            msg = "Worker execution is gated in gateway-only mode"
            raise MeshGatewayError(msg)


@dataclass
class MeshGateway:
    """Central message router for the Agent Mesh.

    The gateway:
    - Registers/deregisters workers and their room bindings
    - Routes messages between workers through the transport
    - Maintains an outbox of pending deliveries (content-free lifecycle)
    - Saves reconnect cursors after each successful delivery
    - Emits content-free lifecycle events for observability
    - Respects the execution gate: in gateway-only mode, messages are routed
      but no worker-side execution is triggered

    Thread-safe for worker registration and outbox access.
    """

    transport: MeshTransport
    cursor_store: MeshCursorStore
    execution_gate: GatewayExecutionGate = field(default_factory=GatewayExecutionGate)
    gateway_room_id: str = ""
    loop_guard: MeshLoopGuard = field(default_factory=MeshLoopGuard.from_env)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _workers: dict[str, MeshWorkerRegistration] = field(default_factory=dict, repr=False)
    _outbox: dict[str, MeshOutboxEntry] = field(default_factory=dict, repr=False)
    _messages: dict[str, MeshMessage] = field(default_factory=dict, repr=False)
    _lifecycle_sink: MeshLifecycleSink = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.gateway_room_id:
            self.transport.gateway_room_id = self.gateway_room_id
        # Share the gateway's lifecycle sink with the transport so all events
        # (routing, delivery, cancellation) accumulate in one place.
        self.transport.lifecycle_sink = self._lifecycle_sink

    @property
    def lifecycle_events(self) -> list[MeshLifecycleEvent]:
        """Return all emitted lifecycle events."""
        return list(self._lifecycle_sink)

    @property
    def lifecycle_outcomes(self) -> dict[str, str]:
        """Return content-free lifecycle outcomes (no message bodies)."""
        return content_free_lifecycle_outcomes(self._lifecycle_sink)

    @property
    def registered_workers(self) -> list[MeshWorkerRegistration]:
        """Return all currently registered workers."""
        with self._lock:
            return list(self._workers.values())

    def register_worker(self, registration: MeshWorkerRegistration) -> None:
        """Register one worker with the gateway."""
        with self._lock:
            if registration.worker_id in self._workers:
                msg = f"Worker {registration.worker_id} is already registered"
                raise MeshGatewayError(msg)
            self._workers[registration.worker_id] = registration
        self._lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="worker_registered",
                worker_id=registration.worker_id,
            ),
        )
        logger.info("mesh_worker_registered", extra={"worker_id": registration.worker_id})

    def deregister_worker(self, worker_id: str) -> None:
        """Deregister one worker from the gateway."""
        with self._lock:
            if worker_id not in self._workers:
                msg = f"Worker {worker_id} is not registered"
                raise MeshGatewayError(msg)
            del self._workers[worker_id]
        self.cursor_store.clear(worker_id)
        self._lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="worker_deregistered",
                worker_id=worker_id,
            ),
        )
        logger.info("mesh_worker_deregistered", extra={"worker_id": worker_id})

    def worker_status(self, worker_id: str) -> MeshWorkerStatus:
        """Return the current status of one worker."""
        with self._lock:
            if worker_id not in self._workers:
                return "deregistered"
            return "registered"

    def route_message(self, message: MeshMessage) -> MeshMessageEnvelope:
        """Route one message from source to target worker through the gateway.

        This is the synchronous part: it creates the route decision and
        outbox entry.  Delivery happens asynchronously via ``deliver_pending``.

        In gateway-only mode, the message is still routed (the transport is
        active), but the execution gate prevents any worker-side tool calls.
        """
        with self._lock:
            source = self._workers.get(message.source_worker_id)
            target = self._workers.get(message.target_worker_id)
            if source is None:
                msg = f"Source worker {message.source_worker_id} is not registered"
                raise MeshGatewayError(msg)
            if target is None:
                msg = f"Target worker {message.target_worker_id} is not registered"
                raise MeshGatewayError(msg)

            # Loop prevention: consult the guard BEFORE creating any outbox
            # entry.  When enabled and the message would exceed hop limits,
            # expire its TTL, or is a duplicate echo, emit a content-free
            # drop event and raise — no outbox row is written.
            verdict = self.loop_guard.check(message)
            if verdict.dropped:
                event_type = (
                    "message_dropped_duplicate"
                    if verdict.drop_kind == "duplicate"
                    else "message_dropped_loop"
                )
                self._lifecycle_sink.append(
                    MeshLifecycleEvent(
                        event_type=event_type,
                        source_worker_id=message.source_worker_id,
                        target_worker_id=message.target_worker_id,
                        correlation_id=message.correlation_id,
                        failure_reason=verdict.reason,
                    ),
                )
                raise MeshLoopError(
                    reason=verdict.reason or "loop prevention",
                    drop_kind=verdict.drop_kind or "loop",
                )

            outbox_id = f"mesh-outbox-{uuid.uuid4().hex[:12]}"
            message_id = f"mesh-msg-{uuid.uuid4().hex[:12]}"

            route = MeshRouteDecision(
                source_worker_id=message.source_worker_id,
                target_worker_id=message.target_worker_id,
                source_room_id=source.room_id,
                target_room_id=target.room_id,
                gateway_room_id=self.gateway_room_id,
            )

            entry = MeshOutboxEntry(
                outbox_id=outbox_id,
                message_id=message_id,
                source_worker_id=message.source_worker_id,
                target_worker_id=message.target_worker_id,
                source_room_id=source.room_id,
                target_room_id=target.room_id,
                gateway_room_id=self.gateway_room_id,
            )

            self._outbox[outbox_id] = entry
            self._messages[message_id] = message

        self._lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="message_routed",
                source_worker_id=message.source_worker_id,
                target_worker_id=message.target_worker_id,
                outbox_id=outbox_id,
                correlation_id=message.correlation_id,
            ),
        )

        return MeshMessageEnvelope(
            message=message,
            route=route,
            outbox_id=outbox_id,
        )

    async def deliver_pending(self) -> dict[str, str]:
        """Deliver all pending outbox entries through the transport.

        Returns content-free outcomes keyed by outbox_id, following the
        provenance-memory drain pattern.

        The execution gate does NOT block delivery — the transport is always
        active in gateway-only mode.  The gate only blocks worker-side
        *execution* (tool calls), not message routing.
        """
        outcomes: dict[str, str] = {}
        pending_entries: list[tuple[MeshOutboxEntry, MeshMessage]] = []
        with self._lock:
            for outbox_id, entry in self._outbox.items():
                if entry.status != "pending":
                    continue
                message = self._messages.get(entry.message_id)
                if message is None:
                    outcomes[outbox_id] = "skipped"
                    continue
                pending_entries.append((entry, message))

        for entry, message in pending_entries:
            status = await self.transport.deliver(entry, message)
            outcomes[entry.outbox_id] = status

        return outcomes

    async def cancel_outbox_entry(
        self,
        outbox_id: str,
        *,
        cancel_source: str = "user_stop",
    ) -> None:
        """Cancel one pending outbox entry (follows cancellation module pattern)."""
        with self._lock:
            entry = self._outbox.get(outbox_id)
            if entry is None:
                msg = f"Outbox entry {outbox_id} not found"
                raise MeshGatewayError(msg)
            if entry.status != "pending":
                msg = f"Outbox entry {outbox_id} is already {entry.status}"
                raise MeshGatewayError(msg)
            entry.status = "cancelled"
            entry.cancel_source = cancel_source
        self._lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="message_cancelled",
                source_worker_id=entry.source_worker_id,
                target_worker_id=entry.target_worker_id,
                outbox_id=outbox_id,
                cancel_source=cancel_source,
            ),
        )

    def get_outbox_entry(self, outbox_id: str) -> MeshOutboxEntry | None:
        """Return one outbox entry by ID (for inspection/reconnect)."""
        with self._lock:
            return self._outbox.get(outbox_id)

    def pending_outbox_count(self) -> int:
        """Return the number of pending outbox entries."""
        with self._lock:
            return sum(1 for e in self._outbox.values() if e.status == "pending")

    def worker_reconnect(self, worker_id: str) -> MeshReconnectCursor | None:
        """Return the last reconnect cursor for a worker, if any."""
        return self.cursor_store.load(worker_id)


# Re-export for __init__
from mindroom.mesh.cursor import MeshReconnectCursor  # noqa: E402


@dataclass
class GatewayOnlyRuntime:
    """Top-level gateway-only runtime coordinator.

    Bundles the execution gate, mesh gateway, cursor store, and transport
    into one object that can be started/stopped.  This is the entrypoint
    for the live two-worker demo.
    """

    mode: GatewayRuntimeMode = GatewayRuntimeMode.GATEWAY_ONLY
    gateway_room_id: str = "!mesh-gateway:localhost"
    storage_path: object | None = None  # Path or None
    _gate: GatewayExecutionGate | None = field(default=None, repr=False)
    _cursor_store: MeshCursorStore | None = field(default=None, repr=False)
    _transport: MatrixMeshTransport | None = field(default=None, repr=False)
    _gateway: MeshGateway | None = field(default=None, repr=False)
    _started: bool = field(default=False, repr=False)

    @property
    def gate(self) -> GatewayExecutionGate:
        """Return the execution gate."""
        if self._gate is None:
            self._gate = GatewayExecutionGate(mode=self.mode)
        return self._gate

    @property
    def cursor_store(self) -> MeshCursorStore:
        """Return the cursor store."""
        if self._cursor_store is None:
            from pathlib import Path

            sp = Path(self.storage_path) if self.storage_path else None
            self._cursor_store = MeshCursorStore(storage_path=sp)
        return self._cursor_store

    @property
    def transport(self) -> MatrixMeshTransport:
        """Return the mesh transport."""
        if self._transport is None:
            self._transport = MatrixMeshTransport(
                cursor_store=self.cursor_store,
                gateway_room_id=self.gateway_room_id,
            )
        return self._transport

    @property
    def gateway(self) -> MeshGateway:
        """Return the mesh gateway."""
        if self._gateway is None:
            self._gateway = MeshGateway(
                transport=self.transport,
                cursor_store=self.cursor_store,
                execution_gate=self.gate,
                gateway_room_id=self.gateway_room_id,
            )
        return self._gateway

    @property
    def is_started(self) -> bool:
        """Return whether the runtime has been started."""
        return self._started

    def start(self) -> None:
        """Start the gateway-only runtime."""
        if self._started:
            msg = "Gateway-only runtime is already started"
            raise MeshGatewayError(msg)
        self.gate.close() if self.mode.is_gateway_only else self.gate.open()
        self._lifecycle_emit("gateway_started")
        self._started = True
        logger.info("mesh_gateway_runtime_started", extra={"mode": self.mode.value})

    def stop(self) -> None:
        """Stop the gateway-only runtime."""
        if not self._started:
            return
        self.gate.open()
        self._lifecycle_emit("gateway_stopped")
        self._started = False
        logger.info("mesh_gateway_runtime_stopped")

    def _lifecycle_emit(self, event_type: str) -> None:
        """Emit a gateway-level lifecycle event."""
        if self._gateway is not None:
            self._gateway._lifecycle_sink.append(
                MeshLifecycleEvent(event_type=event_type),  # type: ignore[arg-type]
            )