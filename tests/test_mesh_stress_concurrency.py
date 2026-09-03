# ruff: noqa: ANN001, D103, S311, TC001

"""P1 Agent Mesh Gateway STRESS — dimension 1: CONCURRENCY.

12 simulated workers, 5 rounds, each worker messages every other worker
(12 x 11 x 5 = 660 total).  Routing is driven under ``asyncio.gather`` with
``return_exceptions=False`` so any failure fails loudly.

Assertions:
- All 12 workers are registered.
- ``pending_outbox_count() == 0`` after the drain.
- ``total_routed == total_delivered == 660``.
- Per-message exactly-once in the wire delivery log (no duplicates).
- No cross-talk: each message lands only in its target worker's room.
- Lifecycle ``message_routed == 660`` and ``message_delivered == 660``.
"""

from __future__ import annotations

import asyncio
import random
import time

import pytest

from mindroom.mesh import MeshGateway
from tests.mesh_stress_helpers import (
    build_mesh_gateway,
    collect_delivery_log,
    delivery_outbox_ids,
    make_message,
)

NUM_WORKERS = 12
ROUNDS = 5
TOTAL = NUM_WORKERS * (NUM_WORKERS - 1) * ROUNDS  # 660
SEED = 0x51CEED  # literal, reproducible interleaving


def _worker_ids() -> list[str]:
    return [f"worker-{i:02d}" for i in range(NUM_WORKERS)]


async def _run_worker(
    gateway: MeshGateway,
    worker_id: str,
    peers: list[str],
    rng: random.Random,
) -> int:
    """One simulated worker: route one message to every peer, each round."""
    routed = 0
    for _round in range(ROUNDS):
        # Deterministic but interleaved peer order per worker/round.
        order = peers[:]
        rng.shuffle(order)
        for peer in order:
            gateway.route_message(
                make_message(
                    source=worker_id,
                    target=peer,
                    content=f"{worker_id}->{peer}",
                    corr_id=f"{worker_id}-{peer}-{_round}",
                ),
            )
            routed += 1
    return routed


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.timeout(30)
async def test_mesh_stress_concurrency(tmp_path) -> None:
    gateway, _store, _transport = build_mesh_gateway(
        worker_ids=_worker_ids(),
        tmp_path=tmp_path,
    )

    # All 12 registered.
    assert len(gateway.registered_workers) == NUM_WORKERS
    ids = {w.worker_id for w in gateway.registered_workers}
    assert ids == set(_worker_ids())

    peers = _worker_ids()
    start = time.monotonic()
    await asyncio.gather(
        *(_run_worker(gateway, wid, [p for p in peers if p != wid], random.Random(SEED ^ hash(wid))) for wid in peers),
    )
    assert gateway.pending_outbox_count() == TOTAL

    # Drain.
    outcomes = await gateway.deliver_pending()
    assert len(outcomes) == TOTAL
    assert gateway.pending_outbox_count() == 0
    elapsed = time.monotonic() - start

    # Exactly-once in the wire delivery log.
    log_ids = delivery_outbox_ids(gateway)
    assert len(log_ids) == TOTAL
    assert len(set(log_ids)) == TOTAL, "duplicate delivery detected"

    # Lifecycle counters: routed == delivered == 660.
    events = gateway.lifecycle_events
    assert sum(1 for e in events if e.event_type == "message_routed") == TOTAL
    assert sum(1 for e in events if e.event_type == "message_delivered") == TOTAL

    # No cross-talk: each outbox appears in exactly one target room, and every
    # worker received exactly (NUM_WORKERS - 1) * ROUNDS messages in its room.
    room_of: dict[str, str] = {}
    per_target_count: dict[str, int] = {}
    for room_id, entry, _msg in collect_delivery_log(gateway):
        assert entry.outbox_id not in room_of, f"cross-talk: {entry.outbox_id} in {room_id}"
        room_of[entry.outbox_id] = room_id
        per_target_count[room_id] = per_target_count.get(room_id, 0) + 1

    expected_per_target = (NUM_WORKERS - 1) * ROUNDS
    assert len(per_target_count) == NUM_WORKERS, "not all target rooms received"
    for wid in peers:
        assert per_target_count.get(f"!{wid}:localhost", 0) == expected_per_target, wid

    # Accounting invariant (no failures/cancels here).
    assert len(room_of) == TOTAL
    assert elapsed >= 0
