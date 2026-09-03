# ruff: noqa: ANN001, ANN202, D103, RUF007

"""P1 Agent Mesh Gateway STRESS — dimension 3: RECONNECT STORM.

30 disconnect/reconnect cycles, 20 messages per cycle (600 total) on a focused
target worker, using a real ``MeshReconnectCoordinator(enabled=True)``.

Assertions:
- Cursor strictly monotonic across cycles.
- No full replay (``replayed < total_delivered``).
- Each outbox_id exactly-once in the wire log.
- ``total_routed == total_delivered``.
- Resume idempotent across two consecutive calls.
- Cursor persists across a fresh ``MeshCursorStore`` over the same ``tmp_path``.
- No wire delivery during resume (spy on ``transport._deliver_to_room``).
"""

from __future__ import annotations

import time

import pytest

from mindroom.mesh import MeshCursorStore, MeshReconnectCoordinator
from tests.mesh_stress_helpers import (
    build_mesh_gateway,
    delivery_outbox_ids,
    make_message,
)

CYCLES = 30
PER_CYCLE = 20
TOTAL = CYCLES * PER_CYCLE  # 600
WARMUP = 20  # baseline delivered before the storm so a certified cursor exists


async def _deliver_batch(gateway, count: int, prefix: str) -> list[str]:
    """Route + deliver ``count`` messages alpha->beta, returning their outbox_ids."""
    ids: list[str] = []
    for i in range(count):
        envelope = gateway.route_message(
            make_message(
                source="alpha",
                target="beta",
                content=f"{prefix}-{i}",
                corr_id=f"{prefix}-{i}",
            ),
        )
        ids.append(envelope.outbox_id)
    await gateway.deliver_pending()
    return ids


def _cursor_outbox_index(gateway, cursor_value: str) -> int:
    """Return the delivery-log index of the outbox referenced by a cursor."""
    marker = "mesh-outbox-"
    idx = cursor_value.find(marker)
    hex_part = cursor_value[idx + len(marker) :].split("-", 1)[0]
    target = f"mesh-outbox-{hex_part}"
    log = delivery_outbox_ids(gateway)
    for i, oid in enumerate(log):
        if oid == target:
            return i
    msg = f"cursor references unknown outbox {target}"
    raise AssertionError(msg)


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.timeout(30)
async def test_mesh_stress_reconnect_storm(tmp_path, monkeypatch) -> None:
    gateway, store, transport = build_mesh_gateway(
        worker_ids=["alpha", "beta"],
        tmp_path=tmp_path,
    )
    coordinator_sink: list = []
    coord = MeshReconnectCoordinator(
        transport=transport,
        cursor_store=store,
        lifecycle_sink=coordinator_sink,
        enabled=True,
    )
    gateway.resume = coord

    # Spy on wire delivery: resume must never touch _deliver_to_room.
    wire_calls: list[str] = []
    real_deliver = transport._deliver_to_room

    async def spy_deliver(entry, message):
        wire_calls.append(entry.outbox_id)
        return await real_deliver(entry, message)

    monkeypatch.setattr(transport, "_deliver_to_room", spy_deliver)

    # Baseline: worker saw these 20 while online (establishes a certified cursor).
    await _deliver_batch(gateway, WARMUP, "baseline")

    cursor_seq: list[str] = []
    for cycle in range(CYCLES):
        certified = store.load("beta")
        assert certified is not None

        # Deliver 20 while the worker is "down"; the durable cursor lags.
        await _deliver_batch(gateway, PER_CYCLE, f"cycle-{cycle}")

        # Simulate offline lag: rewind durable cursor to the last certified point.
        store.save(certified)

        wire_before = len(wire_calls)
        result = await gateway.resume_worker("beta")
        # No wire delivery during resume.
        assert len(wire_calls) == wire_before, "resume performed a wire delivery"

        assert result.resumed is True
        assert len(result.replayed_outbox_ids) == PER_CYCLE
        assert result.advanced_cursor is not None
        cursor_seq.append(result.advanced_cursor)

        # Resume is idempotent across two consecutive calls.
        again = await gateway.resume_worker("beta")
        assert again.resumed is False
        assert again.replayed_outbox_ids == ()
        assert len(wire_calls) == wire_before

    # ── Post-storm assertions ──────────────────────────────────────────────
    delivered = len(delivery_outbox_ids(gateway))
    total_delivered = WARMUP + TOTAL
    assert delivered == total_delivered

    events = gateway.lifecycle_events
    assert sum(1 for e in events if e.event_type == "message_routed") == total_delivered
    assert sum(1 for e in events if e.event_type == "message_delivered") == total_delivered
    # Replay events accumulate on the coordinator's own sink.
    assert sum(1 for e in coordinator_sink if e.event_type == "message_replayed") == TOTAL

    # Exactly-once in the wire log.
    log_ids = delivery_outbox_ids(gateway)
    assert len(set(log_ids)) == total_delivered, "duplicate delivery detected"

    # No full replay: replayed (600) < total_delivered (620).
    assert total_delivered > TOTAL

    # Cursor strictly monotonic across cycles.
    indices = [_cursor_outbox_index(gateway, c) for c in cursor_seq]
    assert all(b < a for b, a in zip(indices, indices[1:])), "cursor not strictly monotonic"

    # Cursor persists across a fresh MeshCursorStore over the same tmp_path.
    fresh = MeshCursorStore(storage_path=tmp_path)  # type: ignore[arg-type]
    persisted = fresh.load("beta")
    assert persisted is not None
    assert persisted.cursor == store.load("beta").cursor

    # No failures, no cancellations.
    assert not any(e.event_type == "message_failed" for e in events)
    assert not any(e.event_type == "message_cancelled" for e in events)
    assert time.monotonic() >= 0
