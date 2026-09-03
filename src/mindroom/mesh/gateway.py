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
import uuid
from dataclasses import dataclass, field

from mindroom.mesh.cursor import MeshCursorStore
from mindroom.mesh.lifecycle import (
    MeshLifecycleEvent,
    MeshLifecycleSink,
    content_free_lifecycle_outcomes,
)
from mindroom.mesh.loop_guard import MeshLoopError, MeshLoopGuard
from mindroom.mesh.models import (
    MeshMessage,
    MeshMessageEnvelope,
    MeshOutboxEntry,
    MeshRouteDecision,
    MeshWorkerRegistration,
    MeshWorkerStatus,
)
from mindroom.mesh.session_map import (
    MeshSessionResolution,
    MeshSessionResolver,
)
from mindroom.mesh.transport import MatrixMeshTransport, MeshTransport
from mindroom.message_target import MessageTarget

logger = logging.getLogger(__name__)

__all__ = [
    "GatewayExecutionGate",
    "GatewayOnlyRuntime",
    "GatewayRuntimeMode",
    "MeshGateway",
    "MeshGatewayError",
    "MeshResumeResult",
    "MeshSessionResolution",
    "MeshSessionResolver",
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
    # Phase A enrollment coordinator.  When ``None`` (default-OFF) the gateway
    # keeps its exact static registration behavior.  When set and enabled, the
    # gateway admits/re-admits workers through the coordinator.
    enrollment: object | None = None
    # Phase A cursor-resume coordinator.  When ``None`` or not ``enabled``
    # (default-OFF) ``resume_worker`` is inert and the gateway keeps its exact
    # behavior; ``worker_reconnect`` still returns the saved cursor unchanged.
    resume: object | None = None
    # Phase A thread/session mapping coordinator.  When ``None`` or not
    # ``enabled`` (default-OFF) routing is unchanged (room-mode only) and the
    # gateway keeps its exact behavior.
    session_mapping: object | None = None
    # Phase A cancellation-propagation coordinator.  When ``None`` or not
    # ``enabled`` (default-OFF) ``cancel_outbox_entry`` behaves exactly as today
    # (pre-delivery outbox cancellation only); when present and enabled it also
    # issues a worker-facing cancel command and awaits the acknowledgment
    # (additive side effect).
    cancel_prop: object | None = None
    # Phase A tool-state streaming coordinator.  When ``None`` or not ``enabled``
    # (default-OFF) no worker tool-state is forwarded and the gateway keeps its
    # exact behavior; when present and enabled, worker tool start/completed
    # events are normalized, sequenced, redacted, and forwarded into the
    # worker's mapped thread (additive side effect).
    tool_state: object | None = None
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
        # Share the gateway's lifecycle sink with the resume coordinator so
        # replayed entries emit into the same lifecycle stream.
        if self.resume is not None and hasattr(self.resume, "lifecycle_sink"):
            self.resume.lifecycle_sink = self._lifecycle_sink
        # When session mapping is enabled, share the gateway's lifecycle sink
        # with the coordinator too so binding events are observable.
        if self.session_mapping is not None and hasattr(self.session_mapping, "lifecycle_sink"):
            self.session_mapping.lifecycle_sink = self._lifecycle_sink
        # Share the gateway's lifecycle sink with the cancellation-propagation
        # coordinator so worker_cancel_requested / worker_cancel_acked /
        # worker_cancel_failed events accumulate in the same lifecycle stream.
        if self.cancel_prop is not None and hasattr(self.cancel_prop, "lifecycle_sink"):
            self.cancel_prop.lifecycle_sink = self._lifecycle_sink
        # Share the gateway's lifecycle sink with the tool-state streaming
        # coordinator so content-free tool_state_streamed events accumulate in
        # the same lifecycle stream (the tool-state payload never carries a
        # message/tool body — privacy invariant).
        if self.tool_state is not None and hasattr(self.tool_state, "lifecycle_sink"):
            self.tool_state.lifecycle_sink = self._lifecycle_sink

    def _session_resolver(self) -> MeshSessionResolver:
        """Return the active thread/session resolver (default-OFF aware).

        When the Phase A session-mapping coordinator is present and enabled,
        its resolver (which consults the durable map) is used.  Otherwise a
        default room-mode resolver is returned so routing stays unchanged
        (``thread_id=None`` everywhere).
        """
        coordinator = self.session_mapping
        if coordinator is not None and getattr(coordinator, "enabled", False):
            resolver = getattr(coordinator, "resolver", None)
            if resolver is not None:
                return resolver
        return MeshSessionResolver(session_map=None)

    @property
    def _session_mapping_active(self) -> bool:
        """Return whether thread/session mapping is enabled (default-OFF)."""
        coordinator = self.session_mapping
        return coordinator is not None and bool(getattr(coordinator, "enabled", False))

    @property
    def _cancel_prop_active(self) -> bool:
        """Return whether cancellation propagation is enabled (default-OFF)."""
        coordinator = self.cancel_prop
        return coordinator is not None and bool(getattr(coordinator, "enabled", False))

    @property
    def _tool_state_active(self) -> bool:
        """Return whether tool-state streaming is enabled (default-OFF)."""
        coordinator = self.tool_state
        return coordinator is not None and bool(getattr(coordinator, "enabled", False))

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
        """Register one worker with the gateway.

        When the optional Phase A enrollment coordinator is present and
        enabled, admission is routed through it (stable worker identity +
        enrollment token), emitting ``worker_enrolled`` on first admission and
        ``worker_registered`` / ``worker_reconnected`` as appropriate.  When
        enrollment is default-OFF (coordinator is ``None`` or ``enabled`` is
        False), this behaves exactly as before — static registration.
        """
        coordinator = self.enrollment
        if coordinator is not None and getattr(coordinator, "enabled", False):
            self._register_worker_enrolled(registration, coordinator)
            return
        self._register_worker_static(registration)

    def _maybe_bind_session(self, registration: MeshWorkerRegistration) -> None:
        """Bind a worker's stable identity to its room/thread when mapping is on.

        Default-OFF: when the Phase A session-mapping coordinator is absent or
        disabled, this is a no-op and registration behavior is unchanged.
        """
        coordinator = self.session_mapping
        if coordinator is None or not getattr(coordinator, "enabled", False):
            return
        bind = getattr(coordinator, "bind", None)
        if bind is not None:
            bind(registration)

    def _register_worker_static(self, registration: MeshWorkerRegistration) -> None:
        """Legacy static registration path (default-OFF, unchanged behavior)."""
        with self._lock:
            if registration.worker_id in self._workers:
                msg = f"Worker {registration.worker_id} is already registered"
                raise MeshGatewayError(msg)
            self._workers[registration.worker_id] = registration
        self._maybe_bind_session(registration)
        self._lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="worker_registered",
                worker_id=registration.worker_id,
            ),
        )
        logger.info("mesh_worker_registered", extra={"worker_id": registration.worker_id})

    def _register_worker_enrolled(
        self,
        registration: MeshWorkerRegistration,
        coordinator: object,
    ) -> None:
        """Admit/re-admit a worker through the Phase A enrollment coordinator."""
        result = coordinator.admit(
            worker_id=registration.worker_id,
            agent_name=registration.agent_name,
            room_id=registration.room_id,
            capabilities=tuple(registration.metadata.get("capabilities", "mesh.worker").split(","))
            if registration.metadata.get("capabilities")
            else ("mesh.worker",),
            token=registration.auth_token,
        )
        if result.status == "rejected":
            msg = f"Worker {registration.worker_id} enrollment was rejected: {result.reason}"
            raise MeshGatewayError(msg)
        with self._lock:
            known = registration.worker_id in self._workers
            self._workers[registration.worker_id] = registration
        self._maybe_bind_session(registration)
        if result.status == "enrolled":
            self._lifecycle_sink.append(
                MeshLifecycleEvent(event_type="worker_enrolled", worker_id=registration.worker_id),
            )
            self._lifecycle_sink.append(
                MeshLifecycleEvent(event_type="worker_registered", worker_id=registration.worker_id),
            )
        elif not known:
            # Registry reports "reconnected" (same identity file re-admitted)
            # but the in-memory worker map had no entry yet -> treat as reconnect.
            self._lifecycle_sink.append(
                MeshLifecycleEvent(event_type="worker_reconnected", worker_id=registration.worker_id),
            )
        # else: already known in-memory -> no duplicate event (idempotent).
        logger.info("mesh_worker_enrollment_admitted", extra={"worker_id": registration.worker_id, "status": result.status})

    def deregister_worker(self, worker_id: str) -> None:
        """Deregister one worker from the gateway."""
        with self._lock:
            if worker_id not in self._workers:
                msg = f"Worker {worker_id} is not registered"
                raise MeshGatewayError(msg)
            del self._workers[worker_id]
        self.cursor_store.clear(worker_id)
        self._maybe_unbind_session(worker_id)
        self._lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="worker_deregistered",
                worker_id=worker_id,
            ),
        )
        logger.info("mesh_worker_deregistered", extra={"worker_id": worker_id})

    def _maybe_unbind_session(self, worker_id: str) -> None:
        """Reap a worker's durable session binding when mapping is on (default-OFF)."""
        coordinator = self.session_mapping
        if coordinator is None or not getattr(coordinator, "enabled", False):
            return
        unbind = getattr(coordinator, "unbind", None)
        if unbind is not None:
            unbind(worker_id)

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

            # Resolve canonical session + thread context for source and target.
            # When session mapping is default-OFF this resolves to room-mode
            # (``thread_id=None``) and the route/outbox keep their exact legacy
            # values (``target_thread_id=None``, ``target_session_id=None``).
            resolver = self._session_resolver()
            mapping_active = self._session_mapping_active
            if mapping_active:
                source_session = resolver.resolve_worker(source)
                target_session = resolver.resolve_worker(target)
            else:
                source_session = MeshSessionResolution(
                    worker_id=source.worker_id,
                    session_id=source.room_id,
                    room_id=source.room_id,
                    thread_id=None,
                )
                target_session = MeshSessionResolution(
                    worker_id=target.worker_id,
                    session_id=target.room_id,
                    room_id=target.room_id,
                    thread_id=None,
                )

            route = MeshRouteDecision(
                source_worker_id=message.source_worker_id,
                target_worker_id=message.target_worker_id,
                source_room_id=source.room_id,
                target_room_id=target.room_id,
                gateway_room_id=self.gateway_room_id,
                source_thread_id=source_session.thread_id,
                target_thread_id=target_session.thread_id,
            )

            entry = MeshOutboxEntry(
                outbox_id=outbox_id,
                message_id=message_id,
                source_worker_id=message.source_worker_id,
                target_worker_id=message.target_worker_id,
                source_room_id=source.room_id,
                target_room_id=target.room_id,
                gateway_room_id=self.gateway_room_id,
                target_session_id=target_session.session_id if mapping_active else None,
                target_thread_id=target_session.thread_id,
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
        """Cancel one pending outbox entry (follows cancellation module pattern).

        Fully additive: when cancellation propagation is enabled (default-OFF,
        ``MINDROOM_MESH_CANCEL_PROP`` / a present + enabled ``cancel_prop``
        coordinator) this additionally issues a worker-facing cancel command to
        the target worker and awaits its acknowledgment, emitting content-free
        ``worker_cancel_requested`` / ``worker_cancel_acked`` lifecycle events.
        When propagation is default-OFF, this behaves exactly as today.
        """
        message_id: str | None = None
        correlation_id: str | None = None
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
            message_id = entry.message_id
            # Correlate the cancel to the originating message's correlation_id
            # (the durable message row carries the user-facing correlation).
            message = self._messages.get(message_id)
            correlation_id = message.correlation_id if message is not None else None
        self._lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="message_cancelled",
                source_worker_id=entry.source_worker_id,
                target_worker_id=entry.target_worker_id,
                outbox_id=outbox_id,
                cancel_source=cancel_source,
            ),
        )
        # Additive Phase A side effect (default-OFF): propagate the cancel to
        # the target worker and await its acknowledgment.  When propagation is
        # OFF this delegates to a benign no-op and behavior is unchanged.
        if self._cancel_prop_active:
            propagate = getattr(self.cancel_prop, "propagate", None)
            if propagate is not None:
                await propagate(entry, correlation_id)

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

    def worker_session(self, worker_id: str) -> MeshSessionResolution | None:
        """Resolve the canonical session context for one registered worker.

        When thread/session mapping is active this reflects the durable
        binding; when default-OFF it resolves to room-mode.  Returns ``None``
        for an unregistered worker.
        """
        with self._lock:
            registration = self._workers.get(worker_id)
        if registration is None:
            return None
        return self._session_resolver().resolve_worker(registration)

    def stream_tool_start(
        self,
        worker_id: str,
        tool: object | None,
    ) -> object | None:
        """Stream one worker tool-start delta into the worker's mapped thread.

        Fully additive: when tool-state streaming is enabled (default-OFF, a
        present + enabled ``tool_state`` coordinator), this resolves the
        worker's canonical session/thread (Item 2 mapping) and forwards the
        normalized, sequenced, redacted trace into the correct thread.  When
        default-OFF, this is a benign no-op returning ``None``.
        """
        if not self._tool_state_active or self.tool_state is None or tool is None:
            return None
        resolution = self.worker_session(worker_id)
        if resolution is None:
            return None
        forward = getattr(self.tool_state, "forward_start", None)
        if forward is None:
            return None
        return forward(resolution, tool)

    def stream_tool_complete(
        self,
        worker_id: str,
        tool: object | None,
    ) -> object | None:
        """Stream one worker tool-completed delta into the worker's mapped thread.

        Fully additive and symmetric with ``stream_tool_start``.  Default-OFF is
        a benign no-op returning ``None``.
        """
        if not self._tool_state_active or self.tool_state is None or tool is None:
            return None
        resolution = self.worker_session(worker_id)
        if resolution is None:
            return None
        forward = getattr(self.tool_state, "forward_complete", None)
        if forward is None:
            return None
        return forward(resolution, tool)

    def delivery_target(self, entry: MeshOutboxEntry) -> MessageTarget:
        """Build a thread-aware ``MessageTarget`` for delivering one outbox entry.

        Reuses ``MessageTarget.resolve`` so the resolved delivery target is
        canonical and consistent with the codebase-wide message-target rules.
        When session mapping is default-OFF the entry carries
        ``target_thread_id=None`` and this resolves to room-mode delivery,
        matching the legacy behavior.
        """
        return MessageTarget.resolve(
            room_id=entry.target_room_id,
            thread_id=entry.target_thread_id,
            reply_to_event_id=None,
            room_mode=entry.target_thread_id is None,
        )

    async def resume_worker(
        self,
        worker_id: str,
        *,
        session_id: str | None = None,
    ) -> MeshResumeResult:
        """Resume a worker from its saved cursor, replaying undelivered entries.

        Fully additive: delegates to the Phase A ``resume`` coordinator.  When
        the coordinator is absent or not ``enabled`` (default-OFF) this returns
        a benign no-op result and the gateway behavior is unchanged.  When
        enabled, this emits a ``worker_reconnected`` lifecycle event and
        advances the worker's cursor after replaying the entries delivered
        after the last certified point (no duplicate delivery, no full replay).
        """
        coordinator = self.resume
        if coordinator is None or not getattr(coordinator, "enabled", False):
            return MeshResumeResult(
                worker_id=worker_id,
                session_id=session_id,
                replayed_outbox_ids=(),
                skipped_outbox_ids=(),
                advanced_cursor=None,
                resumed=False,
            )
        # Integrate with Item 5 session-aware cursor resume: when thread/session
        # mapping is active, resolve the worker's canonical session and hand it
        # to the resume coordinator so replay stays in the correct thread.
        effective_session = session_id
        if effective_session is None and self._session_mapping_active:
            session = self.worker_session(worker_id)
            if session is not None:
                effective_session = session.session_id
        result = await coordinator.resume(worker_id, session_id=effective_session)
        self._lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="worker_reconnected",
                worker_id=worker_id,
            ),
        )
        return result


# Re-export for __init__
from mindroom.mesh.cursor import MeshReconnectCursor  # noqa: E402
from mindroom.mesh.reconnect import MeshResumeResult  # noqa: E402


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
