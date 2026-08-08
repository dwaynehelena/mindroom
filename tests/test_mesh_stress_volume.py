# ruff: noqa: ANN001, D103

"""P1 Agent Mesh Gateway STRESS — dimension 2: VOLUME.

8 workers, 1200 messages (150 per source), delivered in batches of 200 until
``pending_outbox_count() == 0``.

Assertions:
- ``total_routed == total_delivered == 1200``.
- Every outbox_id exactly-once in the wire delivery log.
- Repeated drain is idempotent (empty outcomes, log length unchanged).
- Per-target delivery order is monotonic.
- ``throughput_msg_per_sec >= 500`` on the fake transport.
- ``failures == 0`` and ``retries == 0`` (no re-visit of a terminal entry).
"""

from __future__ import annotations

import time

import pytest

from tests.mesh_stress_helpers import (
    build_mesh_gateway,
    collect_delivery_log,
    delivery_outbox_ids,
    make_message,
)

NUM_WORKERS = 8
PER_SOURCE = 150
TOTAL = NUM_WORKERS * PER_SOURCE  # 1200
BATCH = 200


def _worker_ids() -> list[str]:
    return [f"w{i:02d}" for i in range(NUM_WORKERS)]


async def _route_and_deliver(gateway, batch: list[tuple[str, str, int]]) -> None:
    """Route one batch and deliver it immediately."""
    for source, target, i in batch:
        gateway.route_message(
            make_message(
                source=source,
                target=target,
                content=f"{source}->{target}#{i}",
                corr_id=f"{source}-{target}-{i}",
            ),
        )
    outcomes = await gateway.deliver_pending()
    assert len(outcomes) == len(batch)
    assert gateway.pending_outbox_count() == 0


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.timeout(30)
async def test_mesh_stress_volume(tmp_path) -> None:
    gateway, _store, _transport = build_mesh_gateway(
        worker_ids=_worker_ids(),
        tmp_path=tmp_path,
    )
    peers = _worker_ids()

    # Route 150 messages per source to every peer, delivering in batches of 200
    # until ``pending_outbox_count() == 0``.  Because ``deliver_pending()``
    # drains everything pending, we route 200 at a time and deliver each chunk.
    start = time.monotonic()
    batch: list[tuple[str, str, int]] = []
    routed_total = 0
    batches = 0
    for source in peers:
        others = [p for p in peers if p != source]
        for i in range(PER_SOURCE):
            target = others[i % len(others)]
            batch.append((source, target, i))
            if len(batch) == BATCH:
                await _route_and_deliver(gateway, batch)
                routed_total += len(batch)
                batches += 1
                batch = []
    if batch:
        await _route_and_deliver(gateway, batch)
        routed_total += len(batch)
        batches += 1
    elapsed = time.monotonic() - start
    assert routed_total == TOTAL
    assert gateway.pending_outbox_count() == 0
    assert batches == TOTAL // BATCH

    # Routed == delivered == 1200 (lifecycle counters).
    events = gateway.lifecycle_events
    assert sum(1 for e in events if e.event_type == "message_routed") == TOTAL
    assert sum(1 for e in events if e.event_type == "message_delivered") == TOTAL

    # Exactly-once in the wire log.
    log_ids = delivery_outbox_ids(gateway)
    assert len(log_ids) == TOTAL
    assert len(set(log_ids)) == TOTAL, "duplicate delivery detected"

    # Idempotent re-drain: empty outcomes, log length unchanged.
    before_log = len(collect_delivery_log(gateway))
    outcomes2 = await gateway.deliver_pending()
    assert outcomes2 == {}
    assert len(collect_delivery_log(gateway)) == before_log
    # No retries: a second drain must not re-deliver any terminal entry.
    assert len(delivery_outbox_ids(gateway)) == TOTAL

    # Per-target delivery order monotonic (delivered_at non-decreasing).
    per_target: dict[str, list[float]] = {}
    for _room_id, entry, _msg in collect_delivery_log(gateway):
        per_target.setdefault(entry.target_room_id, []).append(entry.delivered_at or 0.0)
    for target_room, timestamps in per_target.items():
        assert timestamps == sorted(timestamps), f"non-monotonic delivery order in {target_room}"

    # Throughput + failures + retries.
    delivered = len(delivery_outbox_ids(gateway))
    throughput = delivered / elapsed if elapsed > 0 else 0.0
    assert throughput >= 500.0, f"throughput {throughput:.1f} msg/s below 500"
    assert not any(e.event_type == "message_failed" for e in events)
    assert not any(e.event_type == "message_cancelled" for e in events)
    # No duplicate deliveries -> retries == 0.
    assert delivered == TOTAL
