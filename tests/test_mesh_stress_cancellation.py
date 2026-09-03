# ruff: noqa: ANN001, D103, PLR0915

"""P1 Agent Mesh Gateway STRESS — dimension 4: CANCELLATION RACES.

8 workers, 200 messages, ~50 cancelled (~25%), with a controlled
``asyncio.Event`` seam in the transport so ~50 cancels race in-flight
deliveries.

Assertions:
- Cancelled entries have ``status == 'cancelled'``, ``cancel_source ==
  'user_stop'``, and are absent from the wire delivery log.
- Every outbox has exactly ONE terminal state (delivered XOR cancelled, never
  both/neither).
- No double-delivery.
- ``total_routed == total_delivered + total_cancelled + total_failed``.
- Cancel-after-terminal raises ``MeshGatewayError``.
- ``deliver_pending()`` excludes cancelled entries (idempotent re-drain).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from mindroom.mesh import MeshGatewayError
from tests.mesh_stress_helpers import (
    RaceSeamTransport,
    build_mesh_gateway,
    delivery_outbox_ids,
    make_message,
)

NUM_WORKERS = 8
TOTAL = 200  # messages
RACE_COUNT = 50  # ~25% cancelled while in-flight


def _worker_ids() -> list[str]:
    return [f"c{i:02d}" for i in range(NUM_WORKERS)]


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.timeout(30)
async def test_mesh_stress_cancellation_races(tmp_path) -> None:
    gateway, _store, transport = build_mesh_gateway(
        worker_ids=_worker_ids(),
        tmp_path=tmp_path,
        transport=RaceSeamTransport(cursor_store=None),
    )
    seam = transport
    assert isinstance(seam, RaceSeamTransport)
    # Re-point the seam transport's cursor store at the gateway's store so
    # cursor persistence and delivery integration stay consistent.
    seam.cursor_store = gateway.cursor_store

    peers = _worker_ids()

    # Route 200 messages among 8 workers, keeping route order for determinism.
    outbox_ids: list[str] = []
    for i in range(TOTAL):
        source = peers[i % NUM_WORKERS]
        target = peers[(i + 1) % NUM_WORKERS]
        envelope = gateway.route_message(
            make_message(
                source=source,
                target=target,
                content=f"m{i}",
                corr_id=f"m{i}",
            ),
        )
        outbox_ids.append(envelope.outbox_id)
    assert gateway.pending_outbox_count() == TOTAL

    # Choose every 4th outbox (in route order) to be race-cancelled (~25%).
    race_ids = [oid for idx, oid in enumerate(outbox_ids) if idx % 4 == 0]
    assert len(race_ids) == RACE_COUNT
    non_race = [oid for oid in outbox_ids if oid not in set(race_ids)]
    assert len(non_race) == TOTAL - RACE_COUNT

    # Arm the race entries on the transport so their deliveries block in-flight.
    seam.arm(set(race_ids))

    start = time.monotonic()
    drain_task = asyncio.create_task(gateway.deliver_pending())

    # For each race entry (in route order = drain order), cancel it while its
    # delivery is blocked in-flight, then release the seam.
    for oid in race_ids:
        await seam.wait_for_race_entry()
        assert seam.race_entered()
        await gateway.cancel_outbox_entry(oid, cancel_source="user_stop")
        seam.release()

    outcomes = await drain_task
    elapsed = time.monotonic() - start

    assert gateway.pending_outbox_count() == 0

    # ── Terminal-state consistency ─────────────────────────────────────────
    terminal: dict[str, str] = {}
    for oid in outbox_ids:
        entry = gateway.get_outbox_entry(oid)
        assert entry is not None
        assert entry.status in ("delivered", "cancelled"), f"non-terminal {oid}={entry.status}"
        assert oid not in terminal, f"duplicate terminal for {oid}"
        terminal[oid] = entry.status

    # Exactly 50 cancelled, 150 delivered; none failed.
    cancelled_ids = [oid for oid, st in terminal.items() if st == "cancelled"]
    delivered_ids = [oid for oid, st in terminal.items() if st == "delivered"]
    assert set(cancelled_ids) == set(race_ids)
    assert len(delivered_ids) == TOTAL - RACE_COUNT

    # Cancelled entries carry user_stop provenance.
    for oid in cancelled_ids:
        entry = gateway.get_outbox_entry(oid)
        assert entry.cancel_source == "user_stop"

    # Wire delivery log: exactly the delivered ids, no cancelled ids, no dupes.
    log_ids = delivery_outbox_ids(gateway)
    assert set(log_ids) == set(delivered_ids)
    assert len(log_ids) == len(delivered_ids) == len(set(log_ids)), "no double-delivery"
    assert not (set(cancelled_ids) & set(log_ids)), "cancelled entry delivered on wire"

    # ── Accounting invariant ───────────────────────────────────────────────
    events = gateway.lifecycle_events
    routed = sum(1 for e in events if e.event_type == "message_routed")
    delivered_ev = sum(1 for e in events if e.event_type == "message_delivered")
    cancelled_ev = sum(1 for e in events if e.event_type == "message_cancelled")
    failed_ev = sum(1 for e in events if e.event_type == "message_failed")
    assert routed == TOTAL
    assert delivered_ev == TOTAL - RACE_COUNT
    assert cancelled_ev == RACE_COUNT
    assert failed_ev == 0
    assert routed == delivered_ev + cancelled_ev + failed_ev
    assert len(outcomes) == TOTAL  # every entry reached a terminal outcome

    # ── deliver_pending excludes cancelled (idempotent re-drain) ───────────
    again = await gateway.deliver_pending()
    assert again == {}
    assert len(delivery_outbox_ids(gateway)) == len(delivered_ids)

    # ── Cancel-after-terminal raises MeshGatewayError ──────────────────────
    with pytest.raises(MeshGatewayError, match="already delivered"):
        await gateway.cancel_outbox_entry(delivered_ids[0], cancel_source="user_stop")
    with pytest.raises(MeshGatewayError, match="already cancelled"):
        await gateway.cancel_outbox_entry(cancelled_ids[0], cancel_source="user_stop")
    with pytest.raises(MeshGatewayError, match="not found"):
        await gateway.cancel_outbox_entry("bogus-id")

    assert elapsed >= 0
