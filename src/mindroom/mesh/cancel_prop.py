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
  network) and ``OpenClawMeshCancelTransport`` (real HTTP, Phase B — the
  ``/cancel`` RPC body + transport logic implemented and unit-tested against a
  documented loopback fake gateway, but hard-gated behind
  ``PHASE_B_CANCEL_RPC_ENABLED``).

- ``MeshCancelRegistry`` — in-flight cancel requests keyed by
  ``(worker_id, correlation_id)`` with a TTL.

Phase A is purely local: cancellation is propagated through a fake/injected
transport and the registry + lifecycle side, with **no network calls**.
Issuing a real ``/cancel`` RPC/HTTP call to a live OpenClaw worker endpoint is
an external, human-gated Phase B side effect (``PHASE_B_CANCEL_RPC_ENABLED``)
that is NOT performed here (see ``docs/mesh_cancel_prop_phase_b_gate.md``).
The OpenClaw ``/cancel`` RPC body and transport logic ARE implemented in
``OpenClawMeshCancelTransport`` and verified locally against a documented
loopback fake gateway, but the network call is hard-gated until the real
OpenClaw gateway exposes a live ``/cancel`` route.

Cancel sources reuse ``mindroom.cancellation`` (``user_stop`` / ``sync_restart``)
so provenance is consistent with ``delivery_gateway.deliver_cancelled_visible_note``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from mindroom.cancellation import USER_STOP_CANCEL_MSG

if TYPE_CHECKING:
    from collections.abc import Callable

    from mindroom.mesh.lifecycle import MeshLifecycleEvent
    from mindroom.mesh.models import MeshOutboxEntry

logger = logging.getLogger(__name__)

_CANCEL_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

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

    Implements the OpenClaw ``/cancel`` RPC: it builds a cancel request body
    (``worker_id``, ``correlation_id``, ``cancel_source``, ``outbox_id``),
    POSTs it to ``{endpoint}/cancel`` on the target worker, parses the JSON
    response into a ``MeshCancelAck``, and surfaces error/timeout conditions as
    ``MeshCancelPropagationError``.

    The network side effect is hard-gated behind the module-level
    ``PHASE_B_CANCEL_RPC_ENABLED``: ``request_cancel`` refuses to fire any HTTP
    call while the gate is closed.  Phase A/the default local path never
    constructs this transport (the propagator defaults to
    ``FakeMeshCancelTransport``).  The wire body construction and response
    parsing are exposed as pure methods (``build_cancel_body`` /
    ``parse_ack``) so the RPC contract is unit-testable locally against a
    documented loopback fake OpenClaw gateway without a real gateway or the
    gate being open.

    ``transport`` is an injectable ``(url, body, auth_token, timeout) ->
    (status, payload)`` callable (default ``_openclaw_urllib_transport``, a
    blocking urllib client dispatched via ``asyncio.to_thread``).  Endpoint
    policy mirrors the edge-node rule: HTTPS or loopback HTTP only.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        auth_token: str | None = None,
        timeout_seconds: float = 10.0,
        transport: Callable[[str, dict[str, object], str | None, float], tuple[int, object | None]] | None = None,
    ) -> None:
        _validate_cancel_endpoint(endpoint, timeout_seconds)
        self.endpoint = endpoint.rstrip("/")
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds
        self._transport = transport if transport is not None else _openclaw_urllib_transport

    def build_cancel_body(self, command: MeshCancelCommand) -> dict[str, str]:
        """Build the OpenClaw ``/cancel`` RPC request body from one command.

        The body carries the worker identity, the correlation id tying the
        cancel back to the originating outbox entry, the cancel provenance
        (``user_stop`` / ``sync_restart``), and the originating ``outbox_id``.
        """
        return {
            "worker_id": command.worker_id,
            "correlation_id": command.correlation_id,
            "cancel_source": command.cancel_source,
            "outbox_id": command.outbox_id,
        }

    def parse_ack(self, command: MeshCancelCommand, status: int, payload: object | None) -> MeshCancelAck:
        """Parse a ``/cancel`` HTTP response into a ``MeshCancelAck``.

        A 2xx status with an ``acknowledged`` field (or any 2xx) is treated as
        acknowledged.  A 2xx body carrying ``acknowledged: false`` (or an
        explicit ``reason``) is treated as unacknowledged.  A non-2xx status is
        an error and raises ``MeshCancelPropagationError``.
        """
        if not (200 <= status < 300):
            message = f"OpenClaw /cancel RPC returned HTTP {status}: {payload!r}"
            raise MeshCancelPropagationError(message)
        acknowledged = True
        reason: str | None = None
        if isinstance(payload, dict):
            acknowledged = bool(payload.get("acknowledged", True))
            reason = payload.get("reason")
        return MeshCancelAck(
            worker_id=command.worker_id,
            correlation_id=command.correlation_id,
            acknowledged=acknowledged,
            reason=str(reason) if reason is not None else None,
        )

    async def request_cancel(self, command: MeshCancelCommand) -> MeshCancelAck:
        """Issue a real ``/cancel`` RPC — only reachable after Phase B approval."""
        if not PHASE_B_CANCEL_RPC_ENABLED:
            message = "Phase B OpenClaw /cancel RPC is not approved; refusing external side effect"
            raise MeshCancelPropagationError(message)
        url = f"{self.endpoint}/cancel"
        body = self.build_cancel_body(command)
        try:
            status, payload = await asyncio.to_thread(
                self._transport,
                url,
                body,
                self.auth_token,
                self.timeout_seconds,
            )
        except (OSError, TimeoutError) as exc:
            message = f"OpenClaw /cancel RPC transport failed for worker {command.worker_id}: {exc}"
            raise MeshCancelPropagationError(message) from exc
        return self.parse_ack(command, status, payload)


def _validate_cancel_endpoint(endpoint: str, timeout_seconds: float) -> None:
    """Enforce the HTTPS-or-loopback-HTTP endpoint policy for the /cancel RPC.

    Mirrors the edge-node URL rule so a real cancel call never targets an
    arbitrary remote HTTP host.
    """
    parsed = urllib.parse.urlsplit(endpoint)
    secure_remote = parsed.scheme == "https"
    loopback = parsed.scheme == "http" and parsed.hostname in _CANCEL_LOOPBACK_HOSTS
    if parsed.scheme not in {"http", "https"} or parsed.query or parsed.fragment or timeout_seconds <= 0:
        message = "OpenClaw /cancel endpoint must be an http(s) URL with a positive timeout"
        raise MeshCancelPropagationError(message)
    if not (secure_remote or loopback):
        message = "OpenClaw /cancel endpoint must use HTTPS or loopback HTTP"
        raise MeshCancelPropagationError(message)


def _openclaw_urllib_transport(
    url: str,
    body: dict[str, object],
    auth_token: str | None,
    timeout: float,
) -> tuple[int, object | None]:
    """Blocking urllib POST used by the OpenClaw cancel transport (thread-dispatched)."""
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request = urllib.request.Request(  # noqa: S310 - endpoint validated by transport constructor
        url,
        data=json.dumps(body, separators=(",", ":"), sort_keys=True).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL validated by transport
            payload: object | None = json.load(response) if response.status != 204 else None
            return response.status, payload
    except urllib.error.HTTPError as exc:
        return exc.code, None


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