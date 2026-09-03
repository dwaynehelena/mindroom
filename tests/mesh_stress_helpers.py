# ruff: noqa: ASYNC109

"""Shared helpers for the P1 Agent Mesh Gateway STRESS suite.

Provides the worker-pool harness, outbox reconciliation, throughput / failure /
retry instrumentation, a bounded ``wait_until`` poller, and a cancellation-race
seam transport.

All five stress dimensions are local and in-memory: the fake transport
(``client=None``) is used, no network calls, and no ``requires_matrix`` marker.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from mindroom.mesh import (
    GatewayExecutionGate,
    GatewayRuntimeMode,
    MatrixMeshTransport,
    MeshCursorStore,
    MeshGateway,
    MeshMessage,
    MeshReconnectCoordinator,
    MeshWorkerRegistration,
)

Predicate = Callable[[], bool]


async def wait_until(predicate: Predicate, timeout: float = 10.0, interval: float = 0.01) -> None:
    """Poll ``predicate`` until it is truthy, raising a ``TimeoutError`` on timeout.

    The only synchronization primitive used across the stress suite — never a
    wall-clock ``time.sleep``.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            msg = f"wait_until timed out after {timeout:.1f}s"
            raise TimeoutError(msg)
        await asyncio.sleep(interval)


def build_mesh_gateway(
    *,
    worker_ids: list[str],
    tmp_path: object,
    transport: MatrixMeshTransport | None = None,
    resume: MeshReconnectCoordinator | None = None,
) -> tuple[MeshGateway, MeshCursorStore, MatrixMeshTransport]:
    """Return a ``(gateway, cursor_store, transport)`` with ``worker_ids`` registered."""
    store = MeshCursorStore(storage_path=tmp_path)  # type: ignore[arg-type]
    resolved_transport = (
        transport
        if transport is not None
        else MatrixMeshTransport(
            cursor_store=store,
            gateway_room_id="!gw:localhost",
        )
    )
    gw = MeshGateway(
        transport=resolved_transport,
        cursor_store=store,
        execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
        gateway_room_id="!gw:localhost",
        resume=resume,
    )
    for wid in worker_ids:
        gw.register_worker(
            MeshWorkerRegistration(
                worker_id=wid,
                agent_name=f"{wid}-agent",
                room_id=f"!{wid}:localhost",
            ),
        )
    return gw, store, resolved_transport


def make_message(source: str, target: str, content: str, corr_id: str) -> MeshMessage:
    """Return one mesh message between two registered workers."""
    return MeshMessage(
        source_worker_id=source,
        target_worker_id=target,
        content=content,
        correlation_id=corr_id,
    )


def collect_delivery_log(gateway: MeshGateway) -> list[tuple[str, object, object]]:
    """Return ``(room_id, entry, message)`` triples in wire delivery-log order.

    Walks the fake transport's in-memory per-room queues exactly as the
    production transport appends them.
    """
    transport = gateway.transport
    delivered = getattr(transport, "_delivered_messages", {})
    log: list[tuple[str, object, object]] = []
    for room_id, room_entries in delivered.items():
        for entry, message in room_entries:
            log.append((room_id, entry, message))
    return log


def delivery_outbox_ids(gateway: MeshGateway) -> list[str]:
    """Return the outbox_ids of every wire delivery, in delivery-log order."""
    return [entry.outbox_id for _room, entry, _msg in collect_delivery_log(gateway)]


@dataclass
class StressMetrics:
    """Aggregated instrumentation for one stress dimension."""

    total_routed: int = 0
    total_delivered: int = 0
    total_cancelled: int = 0
    total_failed: int = 0
    total_replayed: int = 0
    duplicate_deliveries: int = 0
    throughput_msg_per_sec: float = 0.0
    wall_seconds: float = 0.0

    @property
    def retries(self) -> int:
        """Re-visits of a non-terminal entry on re-drain == duplicate deliveries."""
        return self.duplicate_deliveries

    def assert_accounting(self) -> None:
        """Assert the core invariant ``routed == delivered + cancelled + failed``."""
        assert self.total_routed == (self.total_delivered + self.total_cancelled + self.total_failed), (
            "accounting invariant violated: "
            f"routed={self.total_routed} delivered={self.total_delivered} "
            f"cancelled={self.total_cancelled} failed={self.total_failed}"
        )


def collect_metrics(gateway: MeshGateway, *, elapsed: float) -> StressMetrics:
    """Compute stress metrics from the gateway lifecycle events and delivery log."""
    events = gateway.lifecycle_events
    routed = sum(1 for e in events if e.event_type == "message_routed")
    delivered = sum(1 for e in events if e.event_type == "message_delivered")
    cancelled = sum(1 for e in events if e.event_type == "message_cancelled")
    failed = sum(1 for e in events if e.event_type == "message_failed")
    replayed = sum(1 for e in events if e.event_type == "message_replayed")

    log_ids = delivery_outbox_ids(gateway)
    dupes = len(log_ids) - len(set(log_ids))
    return StressMetrics(
        total_routed=routed,
        total_delivered=delivered,
        total_cancelled=cancelled,
        total_failed=failed,
        total_replayed=replayed,
        duplicate_deliveries=dupes,
        throughput_msg_per_sec=(delivered / elapsed) if elapsed > 0 else 0.0,
        wall_seconds=elapsed,
    )


def assert_no_duplicate_delivery(gateway: MeshGateway) -> None:
    """Assert every outbox_id appears at most once in the wire delivery log."""
    ids = delivery_outbox_ids(gateway)
    assert len(ids) == len(set(ids)), "duplicate delivery detected in wire log"


class RaceSeamTransport(MatrixMeshTransport):
    """Fake transport with a deterministic cancellation-race seam.

    Deliveries of armed entries (``_armed``) pause on an internal gate so a
    test can issue a ``user_stop`` cancel while that delivery is in-flight.
    When the gate is released, a cancelled armed entry is aborted: no wire
    delivery is appended and no ``message_delivered`` event is emitted — the
    outbox keeps its single ``cancelled`` terminal state (delivered XOR
    cancelled, never both/neither).

    The gate is re-armed after each cancelled armed entry so the next armed
    delivery blocks again (deterministic, sequential interleaving — no wall
    clock involved).
    """

    def __init__(
        self,
        *,
        cursor_store: MeshCursorStore,
        gateway_room_id: str = "",
        client: object | None = None,
    ) -> None:
        super().__init__(cursor_store=cursor_store, gateway_room_id=gateway_room_id, client=client)
        self._armed: set[str] = set()
        self._gate = asyncio.Event()
        self._entered = asyncio.Event()

    def arm(self, ids: set[str]) -> None:
        """Arm entries whose deliveries should pause on the race seam."""
        self._armed = set(ids)
        # Clear the gate so the first armed delivery blocks on it.
        self._gate.clear()
        self._entered.clear()

    def race_entered(self) -> bool:
        """Return whether an armed delivery is currently blocked on the seam."""
        return self._entered.is_set()

    async def wait_for_race_entry(self, timeout: float = 5.0) -> None:
        """Wait until an armed delivery is blocked on the seam (bounded)."""
        await asyncio.wait_for(self._entered.wait(), timeout=timeout)

    def release(self) -> None:
        """Release one blocked armed delivery; the next armed one blocks again.

        ``set()`` wakes the currently-blocked delivery, then ``clear()`` re-arms
        the gate so the next armed delivery blocks.  The woken delivery has
        already passed its ``await`` (its waiter future was resolved), so
        clearing immediately after is safe.
        """
        self._gate.set()
        self._gate.clear()
        self._entered.clear()

    async def deliver(self, entry: object, message: object) -> str:
        """Override the delivery path with the cancellation-race seam."""
        if entry.outbox_id in self._armed:  # type: ignore[attr-defined]
            self._entered.set()
            await self._gate.wait()
            if entry.status == "cancelled":  # type: ignore[attr-defined]
                return "cancelled"
            self._armed.discard(entry.outbox_id)  # type: ignore[attr-defined]
        return await super().deliver(entry, message)  # type: ignore[arg-type]
