"""Tests for Phase B Unit 1 — inverted enrollment via the shared P9 edge-fleet EnrollmentAuthority.

Covers the re-architecture in which the mesh gateway's authority subclasses the
shared ``edge_fleet.EnrollmentAuthority`` (instead of a mesh-specific sibling):
- Shared authority reuse: ``MeshEnrollmentAuthority`` is an ``EnrollmentAuthority``
  subclass sharing the exact key contract + HMAC scheme.
- Inverted claim issuance: ``issue_edge_token`` produces a ``mindroom.edge-enrollment/1``
  claim that the shared P9 ``EnrollmentAuthority`` and ``EdgeFleet`` verify.
- Verification: the mesh authority verifies both shared edge-fleet claims
  (``node_id``) and Phase A mesh claims (``worker_id``/``agent_name``).
- Registry normalization: a shared edge-fleet claim is admitted/re-admitted
  with ``node_id`` normalized to the mesh ``worker_id``.
- Re-admission on restart: the same identity yields the same ``worker_id``.
- Duplicate/stale rejection via the shared authority.
"""

# ruff: noqa: ANN001, ANN201, D101, D102, RUF043

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mindroom.edge_fleet import EnrollmentAuthority, _b64
from mindroom.mesh import (
    MeshEnrollmentAuthority,
    MeshEnrollmentCoordinator,
    MeshEnrollmentError,
    MeshEnrollmentRegistry,
    MeshWorkerIdentity,
)

NOW = datetime(2026, 8, 7, tzinfo=UTC)


def _authority() -> MeshEnrollmentAuthority:
    return MeshEnrollmentAuthority(b"m" * 32)


def _registry(tmp_path) -> MeshEnrollmentRegistry:
    reg = MeshEnrollmentRegistry(Path(tmp_path) / "mesh_enrollment.db")
    reg.open()
    return reg


def _identity_path(tmp_path) -> Path:
    return Path(tmp_path) / "worker_identity.json"


def _coordinator(tmp_path, *, now=NOW) -> MeshEnrollmentCoordinator:
    return MeshEnrollmentCoordinator(
        authority=_authority(),
        registry=_registry(tmp_path),
        identity_path=_identity_path(tmp_path),
        enabled=True,
        now=lambda: now,
    )


# ── Shared authority reuse ────────────────────────────────────────────────


class TestSharedAuthorityReuse:
    def test_mesh_authority_is_edge_fleet_authority_subclass(self):
        auth = _authority()
        assert isinstance(auth, EnrollmentAuthority)

    def test_shared_key_contract_enforced(self):
        with pytest.raises(ValueError, match="32 bytes"):
            MeshEnrollmentAuthority(b"short")
        # Same 32-byte key accepted by both the shared and mesh authorities.
        key = b"k" * 32
        shared = EnrollmentAuthority(key)
        mesh = MeshEnrollmentAuthority(key)
        assert shared.verify(
            mesh.issue_edge(
                node_id="n1",
                runtime="openclaw",
                public_key=_b64(b"\x00" * 32),
                capabilities=("mesh.worker",),
                expires_at=NOW + timedelta(minutes=5),
            ),
            observed_at=NOW,
        )


# ── Inverted claim issuance / verification ────────────────────────────────


class TestInvertedClaimFlow:
    def test_issue_edge_shared_authority_roundtrip(self):
        auth = _authority()
        token = auth.issue_edge(
            node_id="worker-alpha",
            runtime="openclaw",
            public_key=_b64(b"\x01" * 32),
            capabilities=("mesh.worker",),
            expires_at=NOW + timedelta(minutes=5),
        )
        claim = auth.verify(token, observed_at=NOW)
        assert claim["schema"] == "mindroom.edge-enrollment/1"
        assert claim["node_id"] == "worker-alpha"
        assert claim["runtime"] == "openclaw"

    def test_edge_fleet_authority_verifies_mesh_issued_edge_claim(self):
        """A claim issued by the mesh authority verifies through the shared P9 authority."""
        mesh = _authority()
        shared = EnrollmentAuthority(b"m" * 32)
        token = mesh.issue_edge(
            node_id="worker-alpha",
            runtime="openclaw",
            public_key=_b64(b"\x01" * 32),
            capabilities=("mesh.worker",),
            expires_at=NOW + timedelta(minutes=5),
        )
        claim = shared.verify(token, observed_at=NOW)
        assert claim["node_id"] == "worker-alpha"

    def test_verify_accepts_mesh_claim_roundtrip(self):
        auth = _authority()
        token = auth.issue(
            worker_id="w2",
            agent_name="beta",
            runtime="openclaw",
            public_key=_b64(b"\x00" * 32),
            capabilities=("mesh.worker",),
            expires_at=NOW + timedelta(minutes=5),
        )
        claim = auth.verify(token, observed_at=NOW)
        assert claim["worker_id"] == "w2"
        assert claim["schema"] == "mindroom.mesh-enrollment/1"

    def test_expired_shared_edge_claim_rejected(self):
        auth = _authority()
        token = auth.issue_edge(
            node_id="worker-alpha",
            runtime="openclaw",
            public_key=_b64(b"\x01" * 32),
            capabilities=("mesh.worker",),
            expires_at=NOW,
        )
        with pytest.raises(MeshEnrollmentError, match="expired"):
            auth.verify(token, observed_at=NOW + timedelta(seconds=1))

    def test_tampered_shared_edge_claim_rejected(self):
        auth = _authority()
        token = auth.issue_edge(
            node_id="worker-alpha",
            runtime="openclaw",
            public_key=_b64(b"\x01" * 32),
            capabilities=("mesh.worker",),
            expires_at=NOW + timedelta(minutes=5),
        )
        with pytest.raises(MeshEnrollmentError, match="signature|malformed"):
            auth.verify(token[:-1] + ("A" if token[-1] != "A" else "B"), observed_at=NOW)


# ── Inverted admission through the registry ───────────────────────────────


class TestInvertedAdmission:
    def test_inverted_edge_claim_first_admission_enrolled(self, tmp_path):
        """A shared edge-fleet claim is admitted with node_id -> worker_id."""
        coord = _coordinator(tmp_path)
        identity = MeshWorkerIdentity.generate(
            coord.identity_path,
            worker_id="worker-alpha",
            agent_name="alpha-agent",
        )
        token = coord.issue_edge_token(
            node_id="worker-alpha",
            public_key=identity.public_key,
            expires_at=NOW + timedelta(minutes=5),
        )
        result = coord.admit(
            worker_id="worker-alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=token,
        )
        assert result.status == "enrolled"
        assert result.worker_id == "worker-alpha"
        assert coord.registry.worker("worker-alpha") is not None

    def test_inverted_claim_re_admission_reconnected(self, tmp_path):
        """Re-admitting the same identity via inverted claim -> reconnected, no duplicate."""
        coord = _coordinator(tmp_path)
        coord.admit(worker_id="worker-alpha", agent_name="alpha-agent", room_id="!alpha:localhost")
        identity = MeshWorkerIdentity.load(coord.identity_path)
        token = coord.issue_edge_token(
            node_id="worker-alpha",
            public_key=identity.public_key,
            expires_at=NOW + timedelta(minutes=5),
        )
        result = coord.admit(
            worker_id="worker-alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=token,
        )
        assert result.status == "reconnected"
        assert coord.registry.known_worker_ids() == ("worker-alpha",)

    def test_inverted_claim_restart_re_admission_same_worker_id(self, tmp_path):
        """On restart the same identity file yields the same worker_id (no duplicate)."""
        coord1 = _coordinator(tmp_path)
        coord1.admit(worker_id="stable-inv", agent_name="alpha", room_id="!alpha:localhost")

        coord2 = _coordinator(tmp_path)
        identity = MeshWorkerIdentity.load(coord2.identity_path)
        token = coord2.issue_edge_token(
            node_id="stable-inv",
            public_key=identity.public_key,
            expires_at=NOW + timedelta(minutes=5),
        )
        result = coord2.admit(
            worker_id="stable-inv",
            agent_name="alpha",
            room_id="!alpha:localhost",
            token=token,
        )
        assert result.status == "reconnected"
        assert result.worker_id == "stable-inv"
        assert coord2.registry.known_worker_ids() == ("stable-inv",)

    def test_inverted_claim_duplicate_token_rejected(self, tmp_path):
        """Replaying the same shared edge-fleet token must be rejected as stale."""
        coord = _coordinator(tmp_path)
        identity = MeshWorkerIdentity.generate(
            coord.identity_path,
            worker_id="worker-alpha",
            agent_name="alpha-agent",
        )
        token = coord.issue_edge_token(
            node_id="worker-alpha",
            public_key=identity.public_key,
            expires_at=NOW + timedelta(minutes=5),
        )
        coord.admit(
            worker_id="worker-alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=token,
        )
        result = coord.admit(
            worker_id="worker-alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=token,
        )
        assert result.status == "rejected"
        assert "already consumed" in (result.reason or "")

    def test_inverted_claim_stale_expired_rejected(self, tmp_path):
        coord = _coordinator(tmp_path)
        identity = MeshWorkerIdentity.generate(
            coord.identity_path,
            worker_id="worker-alpha",
            agent_name="alpha-agent",
        )
        stale = coord.issue_edge_token(
            node_id="worker-alpha",
            public_key=identity.public_key,
            expires_at=NOW - timedelta(seconds=5),
        )
        result = coord.admit(
            worker_id="worker-alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=stale,
        )
        assert result.status == "rejected"
        assert "expired" in (result.reason or "")

    def test_inverted_claim_equivocation_denied(self, tmp_path):
        """Same node_id with a different public key is denied as equivocation."""
        coord = _coordinator(tmp_path)
        coord.admit(worker_id="worker-alpha", agent_name="alpha-agent", room_id="!alpha:localhost")
        token = coord.issue_edge_token(
            node_id="worker-alpha",
            public_key=_b64(b"\x09" * 32),
            expires_at=NOW + timedelta(minutes=5),
        )
        result = coord.admit(
            worker_id="worker-alpha",
            agent_name="alpha-agent",
            room_id="!alpha:localhost",
            token=token,
        )
        assert result.status == "rejected"
        assert "equivocation" in (result.reason or "")
