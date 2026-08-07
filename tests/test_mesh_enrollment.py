"""Tests for Phase A mesh worker identity + enrollment (stable identity, authority, registry).

Covers:
- Identity persistence across store/registry instances (same file -> same worker_id).
- Enrollment handshake (issue -> verify -> admit).
- Duplicate/stale token rejection.
- Re-admission on restart producing the same stable worker_id.
- Default-OFF no-op path (static registration unchanged).
- Phase B real-handshake path is NOT called / no network by default.
"""

# ruff: noqa: ANN001, ANN201, D101, D102, RUF043

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mindroom.edge_fleet import _b64
from mindroom.mesh import (
    PHASE_B_HANDSHAKE_ENABLED,
    MatrixMeshTransport,
    MeshCursorStore,
    MeshEnrollmentAuthority,
    MeshEnrollmentCoordinator,
    MeshEnrollmentError,
    MeshEnrollmentRegistry,
    MeshGateway,
    MeshGatewayError,
    MeshWorkerIdentity,
    MeshWorkerRegistration,
)
from mindroom.mesh.gateway import GatewayExecutionGate, GatewayRuntimeMode

NOW = datetime(2026, 8, 7, tzinfo=UTC)


def _authority() -> MeshEnrollmentAuthority:
    return MeshEnrollmentAuthority(b"m" * 32)


def _registry(tmp_path) -> MeshEnrollmentRegistry:
    reg = MeshEnrollmentRegistry(tmp_path / "mesh_enrollment.db")
    reg.open()
    return reg


def _identity_path(tmp_path) -> Path:
    return Path(tmp_path) / "worker_identity.json"


def _coordinator(tmp_path, *, enabled=True, handshake=None, handshake_enabled=False) -> MeshEnrollmentCoordinator:
    return MeshEnrollmentCoordinator(
        authority=_authority(),
        registry=_registry(tmp_path),
        identity_path=_identity_path(tmp_path),
        enabled=enabled,
        handshake=handshake,
        handshake_enabled=handshake_enabled,
        now=lambda: NOW,
    )


def _gateway(coordinator: MeshEnrollmentCoordinator | None) -> MeshGateway:
    store = MeshCursorStore()
    transport = MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")
    return MeshGateway(
        transport=transport,
        cursor_store=store,
        execution_gate=GatewayExecutionGate(mode=GatewayRuntimeMode.GATEWAY_ONLY),
        gateway_room_id="!gw:localhost",
        enrollment=coordinator,
    )


# ── Identity persistence ──────────────────────────────────────────────────


class TestMeshWorkerIdentity:
    def test_generate_persists_mode_0600(self, tmp_path):
        path = _identity_path(tmp_path)
        identity = MeshWorkerIdentity.generate(
            path,
            worker_id="w1",
            agent_name="alpha",
            capabilities=("mesh.worker", "research"),
        )
        assert identity.worker_id == "w1"
        assert identity.agent_name == "alpha"
        assert identity.runtime == "openclaw"
        assert set(identity.capabilities) == {"mesh.worker", "research"}
        assert identity.public_key
        assert path.exists()

        mode = path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_load_roundtrip_preserves_identity(self, tmp_path):
        path = _identity_path(tmp_path)
        first = MeshWorkerIdentity.generate(path, worker_id="w1", agent_name="alpha")
        loaded = MeshWorkerIdentity.load(path)
        assert loaded.worker_id == first.worker_id
        assert loaded.agent_name == first.agent_name
        assert loaded.public_key == first.public_key
        assert loaded.capabilities == first.capabilities

    def test_persistence_across_instances_same_worker_id(self, tmp_path):
        """Same identity file, loaded by a new store/coordinator, yields the same worker_id."""
        path = _identity_path(tmp_path)
        MeshWorkerIdentity.generate(path, worker_id="stable-1", agent_name="alpha")
        # Simulate a fresh process/registry instance loading the same file.
        identity2 = MeshWorkerIdentity.load(path)
        assert identity2.worker_id == "stable-1"

    def test_load_rejects_world_readable_file(self, tmp_path):
        path = _identity_path(tmp_path)
        MeshWorkerIdentity.generate(path, worker_id="w1", agent_name="alpha")
        path.chmod(0o644)
        with pytest.raises(MeshEnrollmentError, match="mode 0600"):
            MeshWorkerIdentity.load(path)

    def test_load_rejects_tampered_shape(self, tmp_path):
        path = _identity_path(tmp_path)
        MeshWorkerIdentity.generate(path, worker_id="w1", agent_name="alpha")
        path.write_text("garbage", encoding="utf-8")
        with pytest.raises(MeshEnrollmentError):
            MeshWorkerIdentity.load(path)


# ── Enrollment authority / handshake ──────────────────────────────────────


class TestMeshEnrollmentHandshake:
    def test_issue_verify_roundtrip(self, tmp_path):
        auth = _authority()
        identity = MeshWorkerIdentity.generate(
            _identity_path(tmp_path),
            worker_id="w2",
            agent_name="beta",
        )
        token = auth.issue(
            worker_id="w2",
            agent_name="beta",
            runtime="openclaw",
            public_key=identity.public_key,
            capabilities=("mesh.worker",),
            expires_at=NOW + timedelta(minutes=5),
        )
        claim = auth.verify(token, observed_at=NOW)
        assert claim["worker_id"] == "w2"
        assert claim["agent_name"] == "beta"
        assert claim["public_key"] == identity.public_key

    def test_expired_token_rejected(self):
        auth = _authority()
        token = auth.issue(
            worker_id="w2",
            agent_name="beta",
            runtime="openclaw",
            public_key=_b64(b"\x00" * 32),
            capabilities=("mesh.worker",),
            expires_at=NOW,
        )
        with pytest.raises(MeshEnrollmentError, match="expired"):
            auth.verify(token, observed_at=NOW + timedelta(seconds=1))

    def test_tampered_token_rejected(self):
        auth = _authority()
        token = auth.issue(
            worker_id="w2",
            agent_name="beta",
            runtime="openclaw",
            public_key=_b64(b"\x00" * 32),
            capabilities=("mesh.worker",),
            expires_at=NOW + timedelta(minutes=5),
        )
        with pytest.raises(MeshEnrollmentError, match="signature|malformed"):
            auth.verify(token[:-1] + ("A" if token[-1] != "A" else "B"), observed_at=NOW)


# ── Registry / admission logic ────────────────────────────────────────────


class TestMeshEnrollmentRegistry:
    def test_first_admission_enrolled(self, tmp_path):
        coord = _coordinator(tmp_path)
        result = coord.admit(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost")
        assert result.status == "enrolled"
        assert result.worker_id == "alpha"
        assert coord.registry.worker("alpha") is not None

    def test_duplicate_token_rejected_as_stale(self, tmp_path):
        """Reusing an already-consumed enrollment token is rejected (stale/duplicate)."""
        coord = _coordinator(tmp_path)
        identity = MeshWorkerIdentity.generate(
            coord.identity_path,
            worker_id="alpha",
            agent_name="alpha-agent",
        )
        token = coord.issue_token(identity, expires_at=NOW + timedelta(minutes=5))
        # Consume the token once.
        coord.admit(
            worker_id="alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=token,
        )
        # Replaying the exact same token must be rejected as already consumed.
        result = coord.admit(
            worker_id="alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=token,
        )
        assert result.status == "rejected"
        assert "already consumed" in (result.reason or "")

    def test_expired_token_rejected_by_coordinator(self, tmp_path):
        coord = _coordinator(tmp_path)
        identity = MeshWorkerIdentity.generate(
            coord.identity_path,
            worker_id="alpha",
            agent_name="alpha-agent",
        )
        stale = coord.issue_token(identity, expires_at=NOW - timedelta(seconds=5))
        result = coord.admit(
            worker_id="alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=stale,
        )
        assert result.status == "rejected"
        assert "expired" in (result.reason or "")

    def test_re_admission_same_worker_id_reconnected(self, tmp_path):
        coord = _coordinator(tmp_path)
        coord.admit(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost")
        # A fresh token for the SAME identity file/worker_id -> re-admission.
        identity = MeshWorkerIdentity.load(coord.identity_path)
        token = coord.issue_token(identity, expires_at=NOW + timedelta(minutes=5))
        result = coord.admit(
            worker_id="alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=token,
        )
        assert result.status == "reconnected"
        assert coord.registry.known_worker_ids() == ("alpha",)

    def test_re_admission_after_restart_same_stable_worker_id(self, tmp_path):
        """On restart (new coordinator), same identity file -> same worker_id, no duplicate."""
        coord1 = _coordinator(tmp_path)
        coord1.admit(worker_id="stable-9", agent_name="alpha", room_id="!alpha:localhost")
        worker_id_before = coord1.identity_path  # file path stays

        # New coordinator instance over the SAME registry + identity file (simulated restart).
        coord2 = MeshEnrollmentCoordinator(
            authority=_authority(),
            registry=_registry(tmp_path),
            identity_path=coord1.identity_path,
            enabled=True,
            now=lambda: NOW,
        )
        result = coord2.admit(worker_id="stable-9", agent_name="alpha", room_id="!alpha:localhost")
        assert result.status == "reconnected"
        assert result.worker_id == "stable-9"
        # No duplicate row.
        assert coord2.registry.known_worker_ids() == ("stable-9",)
        assert worker_id_before == coord2.identity_path

    def test_equivocation_denied(self, tmp_path):
        """Same worker_id with a different public key is denied as equivocation."""
        coord = _coordinator(tmp_path)
        coord.admit(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost")
        # A token from the SAME authority carrying a DIFFERENT public key but
        # the same worker_id + agent_name -> the registry must detect
        # public-key equivocation (identity file matches, public key differs).
        token = coord.authority.issue(
            worker_id="alpha",
            agent_name="alpha-agent",
            runtime="openclaw",
            public_key=_b64(b"\x01" * 32),
            capabilities=("mesh.worker",),
            expires_at=NOW + timedelta(minutes=5),
        )
        result = coord.admit(
            worker_id="alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=token,
        )
        assert result.status == "rejected"
        assert "equivocation" in (result.reason or "")

    def test_gateway_admit_emits_lifecycle_events(self, tmp_path):
        coord = _coordinator(tmp_path)
        gw = _gateway(coord)
        gw.register_worker(
            MeshWorkerRegistration(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost"),
        )
        types = [e.event_type for e in gw.lifecycle_events]
        assert "worker_enrolled" in types
        assert "worker_registered" in types

    def test_gateway_restart_reconnect_emits_reconnected(self, tmp_path):
        """A restarted gateway re-admits the same identity and emits reconnected, not enrolled."""
        coord1 = _coordinator(tmp_path)
        gw1 = _gateway(coord1)
        gw1.register_worker(
            MeshWorkerRegistration(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost"),
        )
        enrolled1 = sum(1 for e in gw1.lifecycle_events if e.event_type == "worker_enrolled")
        assert enrolled1 == 1

        # Restart: new coordinator + new gateway over the SAME identity file + registry.
        coord2 = MeshEnrollmentCoordinator(
            authority=_authority(),
            registry=_registry(tmp_path),
            identity_path=coord1.identity_path,
            enabled=True,
            now=lambda: NOW,
        )
        gw2 = _gateway(coord2)
        gw2.register_worker(
            MeshWorkerRegistration(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost"),
        )
        types2 = [e.event_type for e in gw2.lifecycle_events]
        # Re-admission must NOT re-enroll; it reconnects instead.
        assert "worker_enrolled" not in types2
        assert "worker_reconnected" in types2
        # Same stable worker_id on restart.
        assert gw2.worker_status("alpha") == "registered"
        assert coord2.registry.known_worker_ids() == ("alpha",)


# ── Default-OFF no-op path ────────────────────────────────────────────────


class TestDefaultOffNoop:
    def test_no_enrollment_coordinator_uses_static_path(self):
        gw = _gateway(None)
        gw.register_worker(
            MeshWorkerRegistration(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost"),
        )
        assert gw.worker_status("alpha") == "registered"
        # Static path raises on duplicate.
        with pytest.raises(MeshGatewayError, match="already registered"):
            gw.register_worker(
                MeshWorkerRegistration(
                    worker_id="alpha",
                    agent_name="alpha-agent",
                    room_id="!alpha:localhost",
                ),
            )
        # No worker_enrolled event on the static path.
        assert not any(e.event_type == "worker_enrolled" for e in gw.lifecycle_events)

    def test_disabled_coordinator_behaves_static(self, tmp_path):
        coord = _coordinator(tmp_path, enabled=False)
        gw = _gateway(coord)
        gw.register_worker(
            MeshWorkerRegistration(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost"),
        )
        assert gw.worker_status("alpha") == "registered"
        with pytest.raises(MeshGatewayError, match="already registered"):
            gw.register_worker(
                MeshWorkerRegistration(
                    worker_id="alpha",
                    agent_name="alpha-agent",
                    room_id="!alpha:localhost",
                ),
            )
        # Enrollment must NOT have written a durable registry row.
        assert coord.registry.known_worker_ids() == ()


# ── Phase B human gate ────────────────────────────────────────────────────


class TestPhaseBHandshakeGate:
    def test_phase_b_constant_is_false(self):
        # This is the hard human gate: Phase B must never be on by default.
        assert PHASE_B_HANDSHAKE_ENABLED is False

    def test_default_coordinator_does_not_call_handshake(self, tmp_path):
        """By default no network handshake is performed (handshake stays None)."""
        called: list[str] = []

        def fake_handshake() -> None:
            called.append("handshake")

        coord = _coordinator(tmp_path, handshake=fake_handshake, handshake_enabled=False)
        coord.admit(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost")
        assert called == []  # no external call

    def test_enabling_handshake_without_approval_raises(self, tmp_path):
        """Setting handshake_enabled while the module gate is OFF must refuse, not call."""
        called: list[str] = []

        def fake_handshake() -> None:
            called.append("handshake")

        coord = _coordinator(tmp_path, handshake=fake_handshake, handshake_enabled=True)
        with pytest.raises(MeshEnrollmentError, match="not approved"):
            coord.admit(worker_id="alpha", agent_name="alpha-agent", room_id="!alpha:localhost")
        assert called == []  # the network call was refused, not performed
