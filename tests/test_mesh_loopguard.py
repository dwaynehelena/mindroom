"""Tests for Item 6: mesh loop prevention (MeshLoopGuard + gateway integration)."""

from __future__ import annotations

import time

import pytest

from mindroom.mesh import (
    GatewayExecutionGate,
    GatewayRuntimeMode,
    MatrixMeshTransport,
    MeshCursorStore,
    MeshGateway,
    MeshLifecycleEvent,
    MeshLoopError,
    MeshLoopGuard,
    MeshLoopVerdict,
    MeshMessage,
    MeshWorkerRegistration,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def make_message(
    source="alpha",
    target="beta",
    content="hello",
    corr_id="corr-1",
    *,
    hop_count=0,
    created_at=None,
    trace=(),
):
    """Create a mesh message with optional hop/ttl fields."""
    return MeshMessage(
        source_worker_id=source,
        target_worker_id=target,
        content=content,
        correlation_id=corr_id,
        created_at=created_at if created_at is not None else time.time(),
        hop_count=hop_count,
        trace=trace,
    )


@pytest.fixture
def enabled_guard():
    """Return an enabled loop guard with injectable clock."""
    return MeshLoopGuard(enabled=True, max_hops=8, ttl_seconds=300)


@pytest.fixture
def disabled_guard():
    """Return a disabled loop guard (default)."""
    return MeshLoopGuard()


def _gateway(loop_guard=None):
    """Return a gateway with a loop guard and two registered workers."""
    store = MeshCursorStore()
    transport = MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")
    gw = MeshGateway(
        transport=transport,
        cursor_store=store,
        execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
        gateway_room_id="!gw:localhost",
        loop_guard=loop_guard if loop_guard is not None else MeshLoopGuard(),
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost"),
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="beta", agent_name="beta-agent", room_id="!beta:localhost"),
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="gamma", agent_name="gamma-agent", room_id="!gamma:localhost"),
    )
    gw.register_worker(
        MeshWorkerRegistration(worker_id="delta", agent_name="delta-agent", room_id="!delta:localhost"),
    )
    return gw


# ── Guard: default-OFF no-op ─────────────────────────────────────────────


class TestLoopGuardDefaultOff:
    """When disabled, the guard always allows messages."""

    def test_disabled_allows_even_over_hop_limit(self, disabled_guard):
        verdict = disabled_guard.check(make_message(hop_count=100))
        assert verdict.allowed
        assert not verdict.dropped

    def test_disabled_allows_stale_message(self, disabled_guard):
        verdict = disabled_guard.check(make_message(created_at=time.time() - 9999))
        assert verdict.allowed

    def test_disabled_allows_repeated_edges(self, disabled_guard):
        for _ in range(5):
            assert disabled_guard.check(make_message(corr_id="same")).allowed


# ── Guard: hop exhaustion / TTL ──────────────────────────────────────────


class TestLoopGuardHops:
    def test_hop_exhaustion_dropped(self, enabled_guard):
        verdict = enabled_guard.check(make_message(hop_count=8))  # == max_hops
        assert verdict.dropped
        assert verdict.drop_kind == "loop"

    def test_hop_below_max_allowed(self, enabled_guard):
        verdict = enabled_guard.check(make_message(hop_count=7))
        assert verdict.allowed

    def test_with_hop_advances_counter(self):
        msg = make_message()
        advanced = msg.with_hop()
        assert advanced.hop_count == 1
        assert advanced.trace == ("alpha",)
        twice = advanced.with_hop()
        assert twice.hop_count == 2
        assert twice.trace == ("alpha", "alpha")

    def test_ttl_expiry_dropped(self):
        guard = MeshLoopGuard(enabled=True, max_hops=8, ttl_seconds=300, now=1000.0)
        stale = make_message(created_at=1000.0 - 301)
        verdict = guard.check(stale)
        assert verdict.dropped
        assert verdict.drop_kind == "loop"

    def test_ttl_within_window_allowed(self):
        guard = MeshLoopGuard(enabled=True, max_hops=8, ttl_seconds=300, now=1000.0)
        fresh = make_message(created_at=1000.0 - 299)
        assert guard.check(fresh).allowed


# ── Guard: dedup / cycle detection / no false positives ──────────────────


class TestLoopGuardDedup:
    def test_duplicate_echo_dropped(self, enabled_guard):
        first = enabled_guard.check(make_message(corr_id="c"))
        assert first.allowed
        second = enabled_guard.check(make_message(corr_id="c"))
        assert second.dropped
        assert second.drop_kind == "duplicate"

    def test_no_false_positive_distinct_messages(self, enabled_guard):
        for corr in ("c1", "c2", "c3", "c4"):
            assert enabled_guard.check(make_message(corr_id=corr)).allowed

    def test_no_false_positive_across_edges(self, enabled_guard):
        # Distinct logical messages across different edges all pass.
        assert enabled_guard.check(make_message(corr_id="c1")).allowed  # alpha->beta
        assert enabled_guard.check(make_message(source="beta", target="alpha", corr_id="c2")).allowed
        assert enabled_guard.check(make_message(corr_id="c3")).allowed

    def test_cycle_a_b_a_b_detected(self, enabled_guard):
        # A->B, B->A, then the echoing A->B is caught as a duplicate.
        assert enabled_guard.check(make_message(corr_id="c")).allowed  # A->B
        assert enabled_guard.check(make_message(source="beta", target="alpha", corr_id="c")).allowed  # B->A
        verdict = enabled_guard.check(make_message(corr_id="c"))  # A->B echo
        assert verdict.dropped
        assert verdict.drop_kind == "duplicate"

    def test_legit_multihop_preserved(self, enabled_guard):
        assert enabled_guard.check(make_message(corr_id="c")).allowed  # alpha->beta
        assert enabled_guard.check(
            make_message(source="beta", target="gamma", corr_id="c"),
        ).allowed  # beta->gamma
        assert enabled_guard.check(
            make_message(source="gamma", target="delta", corr_id="c"),
        ).allowed  # gamma->delta


# ── Guard as verdict container ───────────────────────────────────────────


class TestLoopVerdict:
    def test_allow_verdict(self):
        v = MeshLoopVerdict.allow()
        assert v.allowed and not v.dropped

    def test_drop_verdict(self):
        v = MeshLoopVerdict.drop(drop_kind="loop", reason="r")
        assert v.dropped and v.drop_kind == "loop" and v.reason == "r"

    def test_mesh_loop_error_attributes(self):
        err = MeshLoopError(reason="r", drop_kind="duplicate")
        assert err.reason == "r"
        assert err.drop_kind == "duplicate"


# ── Gateway integration: default-OFF no-op path ──────────────────────────


class TestGatewayLoopGuardDefaultOff:
    def test_default_off_route_unchanged(self):
        gw = _gateway()  # default MeshLoopGuard() is disabled
        env = gw.route_message(make_message())
        assert env.outbox_id is not None
        assert gw.pending_outbox_count() == 1

    def test_default_off_ignores_high_hop_count(self):
        gw = _gateway()
        env = gw.route_message(make_message(hop_count=100))
        assert env.outbox_id is not None

    def test_default_off_no_drop_events(self):
        gw = _gateway()
        gw.route_message(make_message())
        gw.route_message(make_message())
        drops = [e for e in gw.lifecycle_events if e.event_type.startswith("message_dropped")]
        assert drops == []


# ── Gateway integration: loop prevention active ──────────────────────────


class TestGatewayLoopGuardEnabled:
    def test_hop_exhaustion_raises_and_writes_no_outbox(self):
        guard = MeshLoopGuard(enabled=True, max_hops=2, ttl_seconds=300)
        gw = _gateway(guard)
        with pytest.raises(MeshLoopError):
            gw.route_message(make_message(hop_count=2))
        assert gw.pending_outbox_count() == 0

    def test_hop_exhaustion_emits_drop_event(self):
        guard = MeshLoopGuard(enabled=True, max_hops=2, ttl_seconds=300)
        gw = _gateway(guard)
        with pytest.raises(MeshLoopError):
            gw.route_message(make_message(hop_count=2))
        events = [e for e in gw.lifecycle_events if e.event_type == "message_dropped_loop"]
        assert len(events) == 1
        assert events[0].source_worker_id == "alpha"
        assert events[0].target_worker_id == "beta"

    def test_ttl_expiry_raises_and_writes_no_outbox(self):
        guard = MeshLoopGuard(enabled=True, max_hops=8, ttl_seconds=300)
        gw = _gateway(guard)
        stale = make_message(created_at=time.time() - 1000)
        with pytest.raises(MeshLoopError):
            gw.route_message(stale)
        assert gw.pending_outbox_count() == 0

    def test_duplicate_echo_raises_and_writes_no_outbox(self):
        guard = MeshLoopGuard(enabled=True, max_hops=8, ttl_seconds=300)
        gw = _gateway(guard)
        gw.route_message(make_message(corr_id="c"))
        with pytest.raises(MeshLoopError):
            gw.route_message(make_message(corr_id="c"))
        assert gw.pending_outbox_count() == 1  # only the first survived

    def test_duplicate_echo_emits_drop_event(self):
        guard = MeshLoopGuard(enabled=True, max_hops=8, ttl_seconds=300)
        gw = _gateway(guard)
        gw.route_message(make_message(corr_id="c"))
        with pytest.raises(MeshLoopError):
            gw.route_message(make_message(corr_id="c"))
        events = [e for e in gw.lifecycle_events if e.event_type == "message_dropped_duplicate"]
        assert len(events) == 1

    def test_drop_events_are_content_free(self):
        guard = MeshLoopGuard(enabled=True, max_hops=2, ttl_seconds=300)
        gw = _gateway(guard)
        with pytest.raises(MeshLoopError):
            gw.route_message(make_message(content="SECRET", hop_count=2))
        for event in gw.lifecycle_events:
            assert "SECRET" not in (event.correlation_id or "")

    def test_legit_multihop_routed_through(self):
        guard = MeshLoopGuard(enabled=True, max_hops=8, ttl_seconds=300)
        gw = _gateway(guard)
        env = gw.route_message(make_message(corr_id="c1"))
        assert env.outbox_id is not None
        # Distinct messages continue to route.
        env2 = gw.route_message(make_message(corr_id="c2"))
        assert env2.outbox_id is not None
        assert gw.pending_outbox_count() == 2

    def test_distinct_edges_not_false_positive(self):
        guard = MeshLoopGuard(enabled=True, max_hops=8, ttl_seconds=300)
        gw = _gateway(guard)
        # beta->alpha then alpha->beta with distinct corr ids.
        gw.route_message(make_message(source="beta", target="alpha", corr_id="x"))
        env = gw.route_message(make_message(source="alpha", target="beta", corr_id="y"))
        assert env.outbox_id is not None
        assert gw.pending_outbox_count() == 2

    def test_hop_count_preserved_in_routed_message(self):
        guard = MeshLoopGuard(enabled=True, max_hops=8, ttl_seconds=300)
        gw = _gateway(guard)
        msg = make_message(corr_id="c", hop_count=3)
        env = gw.route_message(msg)
        assert env.message.hop_count == 3

    def test_can_enable_guard_after_construction(self):
        # A disabled guard can be flipped on and starts enforcing.
        guard = MeshLoopGuard(enabled=False, max_hops=2, ttl_seconds=300)
        gw = _gateway(guard)
        gw.route_message(make_message(hop_count=0))
        guard.enabled = True
        with pytest.raises(MeshLoopError):
            gw.route_message(make_message(hop_count=2))


class TestLoopGuardFromEnv:
    """MINDROOM_MESH_LOOPGUARD env-flag gating."""

    def test_absent_env_defaults_off(self):
        guard = MeshLoopGuard.from_env({})
        assert guard.enabled is False

    def test_falsy_env_defaults_off(self):
        for value in ("", "0", "false", "no", "off"):
            guard = MeshLoopGuard.from_env({"MINDROOM_MESH_LOOPGUARD": value})
            assert guard.enabled is False, value

    def test_truthy_env_enables(self):
        for value in ("1", "true", "yes", "on", "enabled"):
            guard = MeshLoopGuard.from_env({"MINDROOM_MESH_LOOPGUARD": value})
            assert guard.enabled is True, value

    def test_default_factory_in_gateway_is_off(self):
        gw = _gateway()
        assert gw.loop_guard.enabled is False