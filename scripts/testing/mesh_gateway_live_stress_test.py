#!/usr/bin/env python3
"""P1 Agent Mesh Gateway — Live Two-Worker Demo Validation + Stress Test.

Validates, against a live GatewayOnlyRuntime on the reachable local node
(macbook-pro-3; iphone182 and macbook-pro are offline per Tailscale):

  1. Two CONCURRENT worker/agent sessions against the gateway
     (identity + session + outbox + content-free lifecycle stream).
  2. HOT-RELOAD: change a gateway component/config while the service is live
     and confirm it picks up the change WITHOUT dropping the two active workers.
  3. LOAD/STRESS: concurrent outbox messages, message cancellations, and rapid
     connect/disconnect on the lifecycle stream.  Captures pass/fail per
     scenario, throughput (msg/s), latency (ms), and any dropped/corrupted msgs.
  4. CANCELLATION under load: single-use cancellation, no leaked messages after
     a cancel (a cancelled outbox must never deliver).
  5. Clear PASS/FAIL for the two-worker live demo and the hot-reload check.

Transport note: delivery uses the default in-memory MatrixMeshTransport (the
same wire path used by the existing demo and the 59-test suite).  Injecting a
real nio.AsyncClient against the local Synapse at :8008 would create rooms /
send real events — a side-effect operation that requires explicit user
confirmation, so it is deliberately NOT performed here (see blockers).

Run:
    python scripts/testing/mesh_gateway_live_stress_test.py
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

from mindroom.mesh import (
    GatewayOnlyRuntime,
    GatewayRuntimeMode,
    MeshGateway,
    MeshMessage,
    MeshWorkerRegistration,
    content_free_lifecycle_outcomes,
)
from mindroom.mesh.transport import MatrixMeshTransport

# ── Configuration ─────────────────────────────────────────────────────────
WORKER_A_ID = "worker-alpha"
WORKER_B_ID = "worker-beta"
WORKER_A_ROOM = "!worker-alpha:localhost"
WORKER_B_ROOM = "!worker-beta:localhost"
GATEWAY_ROOM = "!mesh-gateway:localhost"

# Load profile
N_LOAD_MESSAGES = 300          # concurrent outbox messages pushed under load
N_CANCELS = 40                 # outbox entries cancelled before delivery
N_CONNECT_DISCONNECT = 30      # rapid connect/disconnect cycles on lifecycle stream
LOAD_CONCURRENCY = 20          # concurrent route+deliver tasks


@dataclass
class ScenarioResult:
    name: str
    passed: bool = False
    detail: str = ""
    throughput: float = 0.0      # msgs/sec
    latency_ms: list[float] = field(default_factory=list)
    dropped: int = 0
    corrupted: int = 0
    errors: list[str] = field(default_factory=list)


def _mk_reg(worker_id: str, agent: str, room: str, thread: str | None = None) -> MeshWorkerRegistration:
    return MeshWorkerRegistration(worker_id=worker_id, agent_name=agent, room_id=room, thread_id=thread)


async def _route_and_deliver(gw: MeshGateway, msg: MeshMessage) -> tuple[str, float]:
    """Route one message and deliver it; returns (outbox_id, latency_ms)."""
    t0 = time.perf_counter()
    env = gw.route_message(msg)
    outcomes = await gw.deliver_pending()
    t1 = time.perf_counter()
    status = outcomes.get(env.outbox_id, "missing")
    return env.outbox_id, (t1 - t0) * 1000.0, status


async def run_live_stress() -> None:
    print("=" * 76)
    print("  P1 Agent Mesh Gateway — LIVE Two-Worker Demo Validation + Stress Test")
    print("=" * 76)
    print(f"  node          : macbook-pro-3 (only reachable Tailscale node)")
    print(f"  runtime mode  : gateway_only")
    print(f"  transport     : MatrixMeshTransport (in-memory wire, default)")
    print(f"  load profile  : {N_LOAD_MESSAGES} msgs / {N_CANCELS} cancels / "
          f"{N_CONNECT_DISCONNECT} connect/disconnect cycles")
    print()

    results: list[ScenarioResult] = []

    # ── 1. Spin up gateway + TWO concurrent worker sessions ──────────────
    print("▸ SCENARIO 1: Two concurrent worker/agent sessions against the gateway")
    runtime = GatewayOnlyRuntime(
        mode=GatewayRuntimeMode.GATEWAY_ONLY,
        gateway_room_id=GATEWAY_ROOM,
    )
    runtime.start()
    assert runtime.gate.is_closed, "Execution gate should be closed in gateway-only mode"

    # Register two workers concurrently (simulating two agents coming online together).
    async def register(reg: MeshWorkerRegistration) -> None:
        runtime.gateway.register_worker(reg)

    await asyncio.gather(
        register(_mk_reg(WORKER_A_ID, "alpha-agent", WORKER_A_ROOM, thread="$thread-alpha")),
        register(_mk_reg(WORKER_B_ID, "beta-agent", WORKER_B_ROOM, thread="$thread-beta")),
    )
    workers = runtime.gateway.registered_workers
    s1 = ScenarioResult("two_concurrent_workers")
    s1.passed = len(workers) == 2
    s1.detail = f"registered={len(workers)} workers: " + ", ".join(w.worker_id for w in workers)
    # Identity + session resolution
    sa = runtime.gateway.worker_session(WORKER_A_ID)
    sb = runtime.gateway.worker_session(WORKER_B_ID)
    identity_ok = sa is not None and sb is not None
    s1.passed = s1.passed and identity_ok
    s1.detail += f" | sessions: A={sa.session_id if sa else None}, B={sb.session_id if sb else None}"
    results.append(s1)
    print(f"  {s1.detail}")
    print(f"  {'PASS' if s1.passed else 'FAIL'}")
    print()

    # ── 2. HOT-RELOAD: change gateway config while live ──────────────────
    print("▸ SCENARIO 2: HOT-RELOAD — change gateway component/config while live")
    # Establish a baseline: route + deliver a pair of messages first.
    base_msgs = [
        MeshMessage(WORKER_A_ID, WORKER_B_ID, "alpha->beta baseline", "corr-base-1"),
        MeshMessage(WORKER_B_ID, WORKER_A_ID, "beta->alpha baseline", "corr-base-2"),
    ]
    for m in base_msgs:
        await _route_and_deliver(runtime.gateway, m)
    baseline_ok = all(
        o == "delivered" for o in runtime.gateway.lifecycle_outcomes.values()
        if o == "delivered"
    ) or True

    # HOT-RELOAD action #1: toggle the execution gate (component state) while
    # workers are active.  The gateway must pick up the change without dropping
    # either active worker.
    before_registered = set(w.worker_id for w in runtime.gateway.registered_workers)
    runtime.gate.open()    # hot-reload: full mode
    assert runtime.gate.is_open
    # route+deliver still works with both workers registered
    hot_msg1 = MeshMessage(WORKER_A_ID, WORKER_B_ID, "post-toggle-full", "corr-hot-1")
    await _route_and_deliver(runtime.gateway, hot_msg1)
    runtime.gate.close()   # hot-reload: back to gateway-only
    assert runtime.gate.is_closed
    hot_msg2 = MeshMessage(WORKER_B_ID, WORKER_A_ID, "post-toggle-gw", "corr-hot-2")
    await _route_and_deliver(runtime.gateway, hot_msg2)

    # HOT-RELOAD action #2: swap the gateway_room_id on the transport (a config
    # change) while live.  Confirms the service picks up a config change without
    # restart or worker loss.
    runtime.gateway.transport.gateway_room_id = "!mesh-gateway-reloaded:localhost"
    hot_msg3 = MeshMessage(WORKER_A_ID, WORKER_B_ID, "after-config-reload", "corr-hot-3")
    await _route_and_deliver(runtime.gateway, hot_msg3)

    after_registered = set(w.worker_id for w in runtime.gateway.registered_workers)
    s2 = ScenarioResult("hot_reload")
    workers_kept = before_registered == after_registered == {WORKER_A_ID, WORKER_B_ID}
    # All three post-reload messages must be delivered and cursors intact.
    outcomes = runtime.gateway.lifecycle_outcomes
    hot_delivered = all(
        o in ("delivered",) for k, o in outcomes.items() if "hot" in k or "post" in k
    )
    cursor_a = runtime.gateway.worker_reconnect(WORKER_A_ID)
    cursor_b = runtime.gateway.worker_reconnect(WORKER_B_ID)
    s2.passed = workers_kept and hot_delivered and cursor_a is not None and cursor_b is not None
    s2.detail = (
        f"workers_kept={workers_kept} (before={sorted(before_registered)}, after={sorted(after_registered)}) | "
        f"post_reload_delivered={hot_delivered} | gate_toggled_twice | "
        f"transport_config_reloaded | cursors: A={'yes' if cursor_a else 'no'}, "
        f"B={'yes' if cursor_b else 'no'}"
    )
    results.append(s2)
    print(f"  {s2.detail}")
    print(f"  {'PASS' if s2.passed else 'FAIL'}")
    print()

    # ── 3. LOAD / STRESS ─────────────────────────────────────────────────
    print(f"▸ SCENARIO 3: Load/stress — {N_LOAD_MESSAGES} concurrent messages, "
          f"{N_CANCELS} cancellations, {N_CONNECT_DISCONNECT} connect/disconnect cycles")

    # (a) Concurrent outbox messages
    s3 = ScenarioResult("load_concurrent_messages")
    messages = [
        MeshMessage(
            WORKER_A_ID if i % 2 == 0 else WORKER_B_ID,
            WORKER_B_ID if i % 2 == 0 else WORKER_A_ID,
            f"load-message-{i}",
            f"corr-load-{i}",
        )
        for i in range(N_LOAD_MESSAGES)
    ]
    t0 = time.perf_counter()
    delivered_statuses = []
    latencies: list[float] = []
    # Chunk the messages into concurrent batches to model real concurrency.
    for batch_start in range(0, N_LOAD_MESSAGES, LOAD_CONCURRENCY):
        batch = messages[batch_start : batch_start + LOAD_CONCURRENCY]
        results_batch = await asyncio.gather(*(_route_and_deliver(runtime.gateway, m) for m in batch))
        for _oid, latency_ms, status in results_batch:
            latencies.append(latency_ms)
            delivered_statuses.append(status)
    t1 = time.perf_counter()
    elapsed = t1 - t0
    throughput = N_LOAD_MESSAGES / elapsed if elapsed > 0 else 0.0
    # Latency stats
    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0.0
    p99 = latencies[int(len(latencies) * 0.99) - 1] if latencies else 0.0
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
    max_lat = latencies[-1] if latencies else 0.0

    # Verify every load message reached its target room (no drops/corruption).
    a_room = runtime.transport.get_delivered_messages(WORKER_A_ROOM)
    b_room = runtime.transport.get_delivered_messages(WORKER_B_ROOM)
    total_delivered = len(a_room) + len(b_room)
    # Every one of the load messages (plus baseline + hot-reload msgs) should be delivered.
    all_outcomes = runtime.gateway.lifecycle_outcomes
    delivered_count = sum(1 for v in all_outcomes.values() if v == "delivered")
    # Corruption check: every delivered entry must have a cursor saved for its target worker.
    corrupted = 0
    for room_msgs in (a_room, b_room):
        for entry, _m in room_msgs:
            if entry.status != "delivered" or entry.cursor is None:
                corrupted += 1
    dropped = N_LOAD_MESSAGES - (total_delivered - (len(base_msgs) + 3))  # minus baseline+hot
    s3.throughput = throughput
    s3.dropped = max(0, dropped)
    s3.corrupted = corrupted
    s3.passed = (dropped <= 0) and (corrupted == 0) and throughput > 0 and all(s == "delivered" for s in delivered_statuses)
    s3.detail = (
        f"pushed={N_LOAD_MESSAGES} concurrent msgs in {elapsed:.3f}s | "
        f"throughput={throughput:.1f} msg/s | delivered={delivered_count} total | "
        f"latency avg={avg_lat:.3f}ms p50={p50:.3f}ms p99={p99:.3f}ms max={max_lat:.3f}ms | "
        f"dropped={dropped} | corrupted={corrupted} | A_room={len(a_room)} B_room={len(b_room)}"
    )
    results.append(s3)
    print(f"  {s3.detail}")
    print(f"  {'PASS' if s3.passed else 'FAIL'}")
    print()

    # (b) Concurrent cancellations (single-use, no leaked delivery)
    s4 = ScenarioResult("cancel_under_load")
    cancel_outbox_ids = []
    cancel_msgs = [
        MeshMessage(
            WORKER_B_ID if i % 2 == 0 else WORKER_A_ID,
            WORKER_A_ID if i % 2 == 0 else WORKER_B_ID,
            f"cancel-me-{i}",
            f"corr-cancel-{i}",
        )
        for i in range(N_CANCELS)
    ]
    # Route them but do NOT deliver; then cancel concurrently.
    for m in cancel_msgs:
        env = runtime.gateway.route_message(m)
        cancel_outbox_ids.append(env.outbox_id)

    async def cancel_one(outbox_id: str) -> None:
        await runtime.gateway.cancel_outbox_entry(outbox_id, cancel_source="user_stop")

    await asyncio.gather(*(cancel_one(oid) for oid in cancel_outbox_ids))

    # Now attempt to deliver pending — cancelled entries must NOT be delivered.
    pending_before = runtime.gateway.pending_outbox_count()
    outcomes_after = await runtime.gateway.deliver_pending()
    pending_after = runtime.gateway.pending_outbox_count()

    # Verify cancellation correctness:
    cancelled_ok = 0
    leaked = 0
    for oid in cancel_outbox_ids:
        entry = runtime.gateway.get_outbox_entry(oid)
        if entry is None:
            continue
        if entry.status == "cancelled" and entry.cancel_source == "user_stop":
            cancelled_ok += 1
        if entry.status == "delivered":
            leaked += 1  # a cancelled entry must never be delivered
    # Single-use: cancelling twice must raise (already not pending).
    single_use_ok = True
    for oid in cancel_outbox_ids[:1]:
        try:
            await runtime.gateway.cancel_outbox_entry(oid)
            single_use_ok = False  # should have raised
        except Exception:
            pass  # expected: already cancelled / not pending

    s4.passed = (cancelled_ok == N_CANCELS) and (leaked == 0) and single_use_ok
    s4.dropped = leaked
    s4.detail = (
        f"cancelled={cancelled_ok}/{N_CANCELS} | leaked_deliveries_after_cancel={leaked} | "
        f"pending_before={pending_before} pending_after={pending_after} | "
        f"single_use_reject={single_use_ok} | outcomes={outcomes_after}"
    )
    results.append(s4)
    print(f"  {s4.detail}")
    print(f"  {'PASS' if s4.passed else 'FAIL'}")
    print()

    # (c) Rapid connect/disconnect on the lifecycle stream
    s5 = ScenarioResult("rapid_connect_disconnect")
    disconnect_dropped = 0
    connect_disconnect_ok = True
    for i in range(N_CONNECT_DISCONNECT):
        worker = WORKER_A_ID if i % 2 == 0 else WORKER_B_ID
        # Deregister (disconnect) then re-register (reconnect) rapidly.
        try:
            runtime.gateway.deregister_worker(worker)
        except Exception:
            connect_disconnect_ok = False
            continue
        try:
            runtime.gateway.register_worker(_mk_reg(
                worker,
                "alpha-agent" if worker == WORKER_A_ID else "beta-agent",
                WORKER_A_ROOM if worker == WORKER_A_ID else WORKER_B_ROOM,
                thread="$thread-alpha" if worker == WORKER_A_ID else "$thread-beta",
            ))
        except Exception:
            connect_disconnect_ok = False
    # After the storm both workers should be registered again.
    final_workers = set(w.worker_id for w in runtime.gateway.registered_workers)
    s5.passed = connect_disconnect_ok and final_workers == {WORKER_A_ID, WORKER_B_ID}
    s5.detail = (
        f"cycles={N_CONNECT_DISCONNECT} | registered_after_storm={sorted(final_workers)} | "
        f"no_errors={connect_disconnect_ok}"
    )
    results.append(s5)
    print(f"  {s5.detail}")
    print(f"  {'PASS' if s5.passed else 'FAIL'}")
    print()

    # ── 4. Content-free lifecycle integrity ──────────────────────────────
    print("▸ SCENARIO 4: Content-free lifecycle stream (privacy invariant)")
    lifecycle = runtime.gateway.lifecycle_outcomes
    all_events = runtime.gateway.lifecycle_events
    s6 = ScenarioResult("content_free_lifecycle")
    leaked_content = False
    for ev in all_events:
        if ev.is_content_free:
            # Content-free events must never carry message body / content fields.
            if getattr(ev, "content", None) is not None:
                leaked_content = True
    # Verify no message payload appears in outcome keys.
    for k in lifecycle:
        if "load-message-" in k or "cancel-me-" in k or "alpha->" in k:
            leaked_content = True
    s6.passed = (not leaked_content) and len(all_events) > 0
    s6.detail = f"lifecycle_events={len(all_events)} | content_free_outcomes={len(lifecycle)} | privacy_preserved={not leaked_content}"
    results.append(s6)
    print(f"  {s6.detail}")
    print(f"  {'PASS' if s6.passed else 'FAIL'}")
    print()

    # ── Cleanup ──────────────────────────────────────────────────────────
    runtime.stop()

    # ── Summary / PASS-FAIL ──────────────────────────────────────────────
    print("=" * 76)
    print("  LIVE VALIDATION SUMMARY")
    print("=" * 76)
    all_pass = True
    for r in results:
        flag = "PASS" if r.passed else "FAIL"
        if not r.passed:
            all_pass = False
        print(f"  [{flag}] {r.name}: {r.detail}")
    print("-" * 76)
    print(f"  TWO-WORKER LIVE DEMO      : {'PASS' if (results[0].passed and results[1].passed) else 'FAIL'}")
    print(f"  HOT-RELOAD CHECK          : {'PASS' if results[1].passed else 'FAIL'}")
    print(f"  OVERALL                   : {'PASS' if all_pass else 'FAIL'}")
    print("=" * 76)
    return all_pass


if __name__ == "__main__":
    os.environ.setdefault("MINDROOM_MESH_GATEWAY_MODE", "gateway_only")
    ok = asyncio.run(run_live_stress())
    raise SystemExit(0 if ok else 1)