"""Phase A — cancellation propagation to workers for the Agent Mesh Gateway.

This module is the fully additive, local-only Phase A of mesh cancellation
propagation (Item 4).  When a pending outbox entry is cancelled through
``MeshGateway.cancel_outbox_entry`` and cancellation propagation is enabled
(default-OFF behind ``MINDROOM_MESH_CANCEL_PROP``), the gateway also issues a
worker-facing cancel command to the target worker and awaits its
acknowledgment.  It provides:

- ``MeshCancellationPropagator`` — translates a ``cancel_outbox_entry`` /
  ``request_task_cancel`` into a worker-facing cancel command and awaits the
  acknowledgment, correlating the outbox entry to the target worker +
  ``correlation_id``.  It emits content-free lifecycle events
  ``worker_cancel_requested`` / ``worker_cancel_acked`` / ``worker_cancel_failed``.

- ``MeshCancelTransport`` — the injectable Protocol that issues the cancel.
  Two implementations: ``FakeMeshCancelTransport`` (default, local, no
  network) and ``OpenClawMeshCancelTransport`` (real HTTP, Phase B — present
  but hard-gated/unreachable).

- ``MeshCancelRegistry`` — in-flight cancel requests keyed by
  ``(worker_id, correlation_id)`` with a TTL.

Phase A is purely local: cancellation is propagated through a fake/injected
transport and the registry + lifecycle side, with **no network calls**.
Issuing a real ``/cancel`` RPC/HTTP call to a live OpenClaw worker endpoint is
an external, human-gated Phase B side effect (``PHASE_B_CANCEL_RPC_ENABLED``)
that is NOT performed here (see ``docs/mesh_cancel_prop_phase_b_gate.md``).

Cancel sources reuse ``mindroom.cancellation`` (``user_stop`` / ``sync_restart``)
so provenance is consistent with ``delivery_gateway.deliver_cancelled_visible_note``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from mindroom.cancellation import USER_STOP_CANCEL_MSG

if TYPE_CHECKING:
    from collections.abc import Callable

    from mindroom.mesh.lifecycle import MeshLifecycleEvent
    from mindroom.mesh.models import MeshOutboxEntry

logger = logging.getLogger(__name__)

__all__ = [
    "MESH_CANCEL_PROP_ENV",
    "PHASE_B_CANCEL_RPC_ENABLED",
    "FakeMeshCancelTransport",
    "MeshCancelAck",
    "MeshCancelCommand",
    "MeshCancelPropagationError",
    "MeshCancelPropagationResult",
    "MeshCancelRegistry",
    "MeshCancelTransport",
    "MeshCancellationPropagator",
    "OpenClawMeshCancelTransport",
    "cancel_prop_flag_enabled",
]

#: Env var that enables cancellation propagation to workers.  Default OFF keeps
#: ``MeshGateway.cancel_outbox_entry`` behavior identical to today (pre-delivery
#: outbox cancellation only, no worker-facing cancel command).
MESH_CANCEL_PROP_ENV = "MINDROOM_MESH_CANCEL_PROP"

#: Phase B real worker ``/cancel`` RPC is hard-gated off.  No real HTTP/network
#: call to a live OpenClaw worker endpoint may occur unless an operator
#: explicitly enables it after human review (see
#: docs/mesh_cancel_prop_phase_b_gate.md).  Phase A propagation is local-only.
PHASE_B_CANCEL_RPC_ENABLED = False


def cancel_prop_flag_enabled(env: dict[str, str] | None = None) -> bool:
    """Return whether cancellation propagation is enabled by env/flag (default OFF)."""
    source = env if env is not None else os.environ
    return (source.get(MESH_CANCEL_PROP_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


class MeshCancelPropagationError(RuntimeError):
    """Raised when the cancellation propagation path cannot satisfy a request."""


@dataclass(frozen=True, slots=True)
class MeshCancelCommand:
    """One worker-facing cancel command issued by the propagator.

    Carries the worker identity, the correlation id that ties the command back
    to the originating outbox entry, and the cancel provenance (``user_stop`` /
    ``sync_restart``) so the receiving worker can mirror the same semantics as
    ``request_task_cancel`` / ``deliver_cancelled_visible_note``.
    """

    worker_id: str
    correlation_id: str
    outbox_id: str
    cancel_source: str


@dataclass(frozen=True, slots=True)
class MeshCancelAck:
    """One acknowledgment from a worker-facing cancel transport."""

    worker_id: str
    correlation_id: str
    acknowledged: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class MeshCancelPropagationResult:
    """Result of one cancel propagation attempt."""

    worker_id: str
    correlation_id: str
    outbox_id: str
    propagated: bool
    acknowledged: bool
    failure_reason: str | None = None


class MeshCancelTransport(Protocol):
    """Injectable protocol that issues a worker-facing cancel and awaits ack.

    Phase A supplies ``FakeMeshCancelTransport`` (local).  The real
    ``OpenClawMeshCancelTransport`` (HTTP, Phase B) is present but hard-gated
    off, so the default local path never performs a network call.
    """

    async def request_cancel(self, command: MeshCancelCommand) -> MeshCancelAck:
        """Issue one worker-facing cancel command and return the acknowledgment."""
        ...


class FakeMeshCancelTransport:
    """Local, in-memory cancel transport (default Phase A transport, no network).

    Records every issued command so tests can assert propagation reached the
    fake worker.  ``auto_ack=True`` acknowledges immediately (success path);
    ``fail=True`` returns an unacknowledged result (worker-unreachable path);
    ``delay`` simulates a slow worker for TTL/timeout tests.
    """

    def __init__(
        self,
        *,
        auto_ack: bool = True,
        fail: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.auto_ack = auto_ack
        self.fail = fail
        self.delay = delay
        self.calls: list[MeshCancelCommand] = []

    async def request_cancel(self, command: MeshCancelCommand) -> MeshCancelAck:
        """Record the command and return the configured acknowledgment."""
        self.calls.append(command)
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.fail:
            return MeshCancelAck(
                worker_id=command.worker_id,
                correlation_id=command.correlation_id,
                acknowledged=False,
                reason="worker_unreachable",
            )
        return MeshCancelAck(
            worker_id=command.worker_id,
            correlation_id=command.correlation_id,
            acknowledged=True,
        )


class OpenClawMeshCancelTransport:
    """Real HTTP cancel transport for a live OpenClaw worker (Phase B, gated).

    Present for the Phase B wiring shape but hard-gated: ``request_cancel``
    raises ``MeshCancelPropagationError`` unless the module-level
    ``PHASE_B_CANCEL_RPC_ENABLED`` is flipped after human approval.  Phase A
    never constructs or invokes this transport by default (the propagator
    defaults to ``FakeMeshCancelTransport``).
    """

    def __init__(self, *, endpoint: str, auth_token: str | None = None) -> None:
        self.endpoint = endpoint
        self.auth_token = auth_token

    async def request_cancel(self, command: MeshCancelCommand) -> MeshCancelAck:
        """Issue a real ``/cancel`` RPC — only reachable after Phase B approval."""
        if not PHASE_B_CANCEL_RPC_ENABLED:
            message = "Phase B OpenClaw /cancel RPC is not approved; refusing external side effect"
            raise MeshCancelPropagationError(message)
        # Unreachable under Phase A.  The real HTTP call to ``self.endpoint``
        # (``{endpoint}/cancel``) is deferred to Phase B after human review.
        raise MeshCancelPropagationError("Phase B OpenClaw /cancel RPC is not implemented")


@dataclass(slots=True)
class _CancelEntry:
    """One in-flight cancel request tracked by the registry."""

    outbox_id: str
    created_at: float
    expires_at: float
    acknowledged: bool = False
    acknowledged_at: float | None = None


class MeshCancelRegistry:
    """In-flight cancel requests keyed by ``(worker_id, correlation_id)`` with TTL.

    Thread-safe.  Each entry records the originating ``outbox_id`` (so an ack
    can be correlated back to the outbox entry) and expires after ``ttl_seconds``
    so stale in-flight cancels are reaped.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 60.0,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._now = now if now is not None else time.monotonic
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _CancelEntry] = {}

    @staticmethod
    def _key(worker_id: str, correlation_id: str) -> tuple[str, str]:
        return (worker_id, correlation_id)

    def register(self, worker_id: str, correlation_id: str, outbox_id: str) -> None:
        """Register one in-flight cancel request."""
        now = self._now()
        with self._lock:
            self._entries[self._key(worker_id, correlation_id)] = _CancelEntry(
                outbox_id=outbox_id,
                created_at=now,
                expires_at=now + self._ttl_seconds,
            )

    def outbox_id_for(self, worker_id: str, correlation_id: str) -> str | None:
        """Return the originating outbox_id for one in-flight cancel, or ``None``."""
        with self._lock:
            entry = self._entries.get(self._key(worker_id, correlation_id))
            return entry.outbox_id if entry is not None else None

    def is_active(self, worker_id: str, correlation_id: str) -> bool:
        """Return whether an unexpired, unacknowledged cancel is in flight."""
        now = self._now()
        with self._lock:
            entry = self._entries.get(self._key(worker_id, correlation_id))
            return entry is not None and not entry.acknowledged and entry.expires_at > now

    def acknowledge(self, worker_id: str, correlation_id: str, *, acknowledged: bool) -> None:
        """Record the acknowledgment for one in-flight cancel."""
        with self._lock:
            entry = self._entries.get(self._key(worker_id, correlation_id))
            if entry is not None:
                entry.acknowledged = acknowledged
                entry.acknowledged_at = self._now()

    def is_acked(self, worker_id: str, correlation_id: str) -> bool:
        """Return whether one in-flight cancel was acknowledged (any outcome)."""
        with self._lock:
            entry = self._entries.get(self._key(worker_id, correlation_id))
            return entry is not None and entry.acknowledged

    def expire(self) -> int:
        """Reap all expired in-flight cancels, returning how many were reaped."""
        now = self._now()
        with self._lock:
            stale = [k for k, e in self._entries.items() if e.expires_at <= now]
            for key in stale:
                del self._entries[key]
            return len(stale)

    def pending_count(self) -> int:
        """Return the number of unexpired, unacknowledged in-flight cancels."""
        now = self._now()
        with self._lock:
            return sum(1 for e in self._entries.values() if not e.acknowledged and e.expires_at > now)

    def clear(self) -> None:
        """Drop all tracked cancel requests (used between tests/restarts)."""
        with self._lock:
            self._entries.clear()


class MeshCancellationPropagator:
    """Orchestrate cancellation propagation from the gateway to a target worker.

    ``translate`` maps an outbox entry + correlation id to a worker-facing
    ``MeshCancelCommand``.  ``propagate`` registers the cancel in the registry,
    issues it through the injectable ``MeshCancelTransport``, awaits the
    acknowledgment, records it in the registry, and emits content-free
    lifecycle events.  The propagation path is gated by ``enabled`` (default-OFF
    in the gateway wiring); when disabled it is a benign no-op.
    """

    def __init__(
        self,
        *,
        transport: MeshCancelTransport | None = None,
        registry: MeshCancelRegistry | None = None,
        ttl_seconds: float = 60.0,
        enabled: bool = True,
        lifecycle_sink: list[MeshLifecycleEvent] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        # Phase A default: local fake transport — never a real network call.
        self.transport = transport if transport is not None else FakeMeshCancelTransport()
        self.registry = registry if registry is not None else MeshCancelRegistry(
            ttl_seconds=ttl_seconds,
            now=now,
        )
        self.enabled = enabled
        self.lifecycle_sink = lifecycle_sink if lifecycle_sink is not None else []
        self._now = now

    def translate(self, entry: MeshOutboxEntry, correlation_id: str | None) -> MeshCancelCommand:
        """Translate one outbox entry + correlation into a worker-facing cancel command.

        Uses the entry's ``target_worker_id`` as the cancel destination and the
        entry's ``cancel_source`` (falling back to ``user_stop``) for provenance.
        """
        return MeshCancelCommand(
            worker_id=entry.target_worker_id,
            correlation_id=correlation_id or entry.message_id,
            outbox_id=entry.outbox_id,
            cancel_source=entry.cancel_source or USER_STOP_CANCEL_MSG,
        )

    def _emit(self, event_type: str, command: MeshCancelCommand, *, failure_reason: str | None = None) -> None:
        """Emit one content-free worker-cancel lifecycle event."""
        from mindroom.mesh.lifecycle import MeshLifecycleEvent

        self.lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type=event_type,  # type: ignore[arg-type]
                worker_id=command.worker_id,
                outbox_id=command.outbox_id,
                correlation_id=command.correlation_id,
                cancel_source=command.cancel_source,
                failure_reason=failure_reason,
            ),
        )

    async def propagate(
        self,
        entry: MeshOutboxEntry,
        correlation_id: str | None = None,
    ) -> MeshCancelPropagationResult:
        """Propagate the cancel for one outbox entry and await worker ack.

        Fully additive: when ``enabled`` is False (default-OFF gateway wiring),
        this returns a benign no-op result and the gateway's existing
        ``cancel_outbox_entry`` behavior is unchanged.
        """
        if not self.enabled:
            return MeshCancelPropagationResult(
                worker_id=entry.target_worker_id,
                correlation_id=correlation_id or entry.message_id,
                outbox_id=entry.outbox_id,
                propagated=False,
                acknowledged=False,
            )

        command = self.translate(entry, correlation_id)
        self.registry.register(command.worker_id, command.correlation_id, command.outbox_id)
        self._emit("worker_cancel_requested", command)

        try:
            ack = await self.transport.request_cancel(command)
        except Exception as exc:  # noqa: BLE001 - transport failure -> unacknowledged
            self.registry.acknowledge(command.worker_id, command.correlation_id, acknowledged=False)
            reason = f"{type(exc).__name__}: {exc}"
            self._emit("worker_cancel_failed", command, failure_reason=reason)
            return MeshCancelPropagationResult(
                worker_id=command.worker_id,
                correlation_id=command.correlation_id,
                outbox_id=command.outbox_id,
                propagated=True,
                acknowledged=False,
                failure_reason=reason,
            )

        self.registry.acknowledge(command.worker_id, command.correlation_id, acknowledged=ack.acknowledged)
        if ack.acknowledged:
            self._emit("worker_cancel_acked", command)
        else:
            self._emit("worker_cancel_failed", command, failure_reason=ack.reason)
        return MeshCancelPropagationResult(
            worker_id=command.worker_id,
            correlation_id=command.correlation_id,
            outbox_id=command.outbox_id,
            propagated=True,
            acknowledged=ack.acknowledged,
            failure_reason=ack.reason,
        )