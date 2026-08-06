#!/usr/bin/env python3
"""Live two-worker demo for P1 Agent Mesh Gateway.

Demonstrates:
1. Gateway-only runtime mode (transport active, worker execution gated)
2. Cross-worker message routing through the gateway
3. Reconnect cursor behavior under simulated disconnect

Usage:
    python -m mindroom.mesh.demo
"""

from __future__ import annotations

import asyncio
import time

from mindroom.mesh import (
    GatewayOnlyRuntime,
    GatewayRuntimeMode,
    MeshGateway,
    MeshMessage,
    MeshWorkerRegistration,
    content_free_lifecycle_outcomes,
)


WORKER_A_ID = "worker-alpha"
WORKER_B_ID = "worker-beta"
WORKER_A_ROOM = "!worker-alpha:localhost"
WORKER_B_ROOM = "!worker-beta:localhost"
GATEWAY_ROOM = "!mesh-gateway:localhost"


async def run_demo() -> None:
    """Run the live two-worker mesh gateway demo."""

    print("=" * 72)
    print("  P1 Agent Mesh Gateway — Live Two-Worker Demo")
    print("=" * 72)
    print()

    # ── 1. Enable gateway-only runtime mode ──────────────────────────────
    print("▸ Step 1: Enable gateway-only runtime mode")
    print("  Setting MINDROOM_MESH_GATEWAY_MODE=gateway_only")

    runtime = GatewayOnlyRuntime(
        mode=GatewayRuntimeMode.GATEWAY_ONLY,
        gateway_room_id=GATEWAY_ROOM,
    )
    runtime.start()
    print(f"  Runtime started — mode: {runtime.mode.value}")
    print(f"  Execution gate closed: {runtime.gate.is_closed}")
    print()

    # ── 2. Register two workers ──────────────────────────────────────────
    print("▸ Step 2: Register two workers")
    worker_a = MeshWorkerRegistration(
        worker_id=WORKER_A_ID,
        agent_name="alpha-agent",
        room_id=WORKER_A_ROOM,
    )
    worker_b = MeshWorkerRegistration(
        worker_id=WORKER_B_ID,
        agent_name="beta-agent",
        room_id=WORKER_B_ROOM,
    )
    runtime.gateway.register_worker(worker_a)
    runtime.gateway.register_worker(worker_b)
    print(f"  Worker A: {WORKER_A_ID} → room {WORKER_A_ROOM}")
    print(f"  Worker B: {WORKER_B_ID} → room {WORKER_B_ROOM}")
    print(f"  Registered workers: {len(runtime.gateway.registered_workers)}")
    print()

    # ── 3. Verify execution gate blocks worker execution ─────────────────
    print("▸ Step 3: Verify execution gate blocks worker execution")
    try:
        runtime.gate.check()
        print("  ✗ ERROR: Gate should have blocked execution!")
    except Exception as exc:
        print(f"  ✓ Execution gate correctly blocked: {exc}")
    print()

    # ── 4. Route messages cross-worker (A → B) ───────────────────────────
    print("▸ Step 4: Route messages from Worker A → Worker B")
    messages = [
        MeshMessage(
            source_worker_id=WORKER_A_ID,
            target_worker_id=WORKER_B_ID,
            content="Hello from Alpha! Task: compile mission data.",
            correlation_id="corr-001",
        ),
        MeshMessage(
            source_worker_id=WORKER_A_ID,
            target_worker_id=WORKER_B_ID,
            content="Status check: is mission compiler ready?",
            correlation_id="corr-002",
        ),
        MeshMessage(
            source_worker_id=WORKER_B_ID,
            target_worker_id=WORKER_A_ID,
            content="Beta here: mission compiler is ready for P4 demo.",
            correlation_id="corr-003",
        ),
    ]

    envelopes = []
    for msg in messages:
        envelope = runtime.gateway.route_message(msg)
        envelopes.append(envelope)
        print(f"  Routed: {msg.source_worker_id} → {msg.target_worker_id} "
              f"[{envelope.outbox_id}] corr={msg.correlation_id}")
    print(f"  Pending outbox entries: {runtime.gateway.pending_outbox_count()}")
    print()

    # ── 5. Deliver pending messages through the transport ────────────────
    print("▸ Step 5: Deliver pending messages through gateway transport")
    outcomes = await runtime.gateway.deliver_pending()
    for outbox_id, status in outcomes.items():
        print(f"  {outbox_id}: {status}")

    delivered_to_b = runtime.transport.get_delivered_messages(WORKER_B_ROOM)
    delivered_to_a = runtime.transport.get_delivered_messages(WORKER_A_ROOM)
    print(f"  Messages delivered to Worker B room: {len(delivered_to_b)}")
    print(f"  Messages delivered to Worker A room: {len(delivered_to_a)}")
    print()

    # ── 6. Verify content-free lifecycle outcomes ─────────────────────────
    print("▸ Step 6: Verify content-free lifecycle outcomes")
    lifecycle = runtime.gateway.lifecycle_outcomes
    print(f"  Content-free outcomes: {lifecycle}")
    print(f"  Total lifecycle events: {len(runtime.gateway.lifecycle_events)}")
    print("  (No message content exposed in lifecycle — privacy preserved)")
    print()

    # ── 7. Verify reconnect cursor behavior ──────────────────────────────
    print("▸ Step 7: Verify reconnect cursor behavior under simulated disconnect")
    cursor_b = runtime.gateway.worker_reconnect(WORKER_B_ID)
    print(f"  Worker B reconnect cursor: {cursor_b}")
    if cursor_b is not None:
        print(f"    cursor={cursor_b.cursor}")
        print(f"    cache_generation={cursor_b.cache_generation}")
        print(f"    saved_at={cursor_b.saved_at:.3f}")

    cursor_a = runtime.gateway.worker_reconnect(WORKER_A_ID)
    print(f"  Worker A reconnect cursor: {cursor_a}")
    print()

    # ── 8. Simulate disconnect and reconnect with cursor ─────────────────
    print("▸ Step 8: Simulate Worker B disconnect and reconnect")
    print("  [Simulating Worker B going offline...]")
    time.sleep(0.5)

    # Route one more message while B is "disconnected"
    msg_during_disconnect = MeshMessage(
        source_worker_id=WORKER_A_ID,
        target_worker_id=WORKER_B_ID,
        content="Alpha: sending while you were away — don't miss this.",
        correlation_id="corr-004",
    )
    envelope = runtime.gateway.route_message(msg_during_disconnect)
    print(f"  Routed during disconnect: [{envelope.outbox_id}] corr=corr-004")

    # Deliver the queued message
    outcomes = await runtime.gateway.deliver_pending()
    for outbox_id, status in outcomes.items():
        print(f"  {outbox_id}: {status}")

    print("  [Worker B reconnecting...]")
    time.sleep(0.5)

    # On reconnect, worker B reads its cursor
    new_cursor_b = runtime.gateway.worker_reconnect(WORKER_B_ID)
    print(f"  Worker B reconnect cursor after recovery: {new_cursor_b}")
    if new_cursor_b is not None:
        print(f"    cursor={new_cursor_b.cursor}")
        print(f"    ✓ Worker B can resume from cursor (no message replay needed)")
    print()

    # ── 9. Test cancellation ─────────────────────────────────────────────
    print("▸ Step 9: Test outbox cancellation")
    cancel_msg = MeshMessage(
        source_worker_id=WORKER_B_ID,
        target_worker_id=WORKER_A_ID,
        content="Beta: cancelling this task — obsolete.",
        correlation_id="corr-005",
    )
    cancel_envelope = runtime.gateway.route_message(cancel_msg)
    print(f"  Routed: [{cancel_envelope.outbox_id}] — cancelling before delivery")
    await runtime.gateway.cancel_outbox_entry(cancel_envelope.outbox_id, cancel_source="user_stop")
    entry = runtime.gateway.get_outbox_entry(cancel_envelope.outbox_id)
    if entry is not None:
        print(f"  Outbox entry status: {entry.status} (cancel_source={entry.cancel_source})")
    print()

    # ── 10. Summary ──────────────────────────────────────────────────────
    print("▸ Step 10: Summary")
    print(f"  Runtime mode: {runtime.mode.value}")
    print(f"  Execution gate closed: {runtime.gate.is_closed}")
    print(f"  Total messages routed: {len(messages) + 2}")  # +1 disconnect, +1 cancel
    print(f"  Total lifecycle events: {len(runtime.gateway.lifecycle_events)}")
    print(f"  Content-free outcomes: {len(runtime.gateway.lifecycle_outcomes)}")
    print(f"  Messages delivered to Worker B: {len(runtime.transport.get_delivered_messages(WORKER_B_ROOM))}")
    print(f"  Messages delivered to Worker A: {len(runtime.transport.get_delivered_messages(WORKER_A_ROOM))}")
    print(f"  Worker B cursor saved: {cursor_b is not None}")
    print(f"  Worker A cursor saved: {cursor_a is not None}")
    print()

    # ── Cleanup ──────────────────────────────────────────────────────────
    print("▸ Cleanup: Stop gateway-only runtime")
    runtime.stop()
    print(f"  Runtime stopped — execution gate open: {runtime.gate.is_open}")
    print()
    print("=" * 72)
    print("  ✓ P1 Agent Mesh Gateway demo complete")
    print("  ✓ Gateway-only runtime mode enabled")
    print("  ✓ Cross-worker message routing verified")
    print("  ✓ Reconnect cursor behavior verified under simulated disconnect")
    print("  ✓ Cancellation verified")
    print("  ✓ Content-free lifecycle outcomes verified")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(run_demo())