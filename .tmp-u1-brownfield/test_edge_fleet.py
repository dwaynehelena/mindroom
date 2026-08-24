"""Tests for secure edge enrollment, leases, offline queues and attestation."""

# ruff: noqa: ANN001, ANN201, ANN202, D103

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mindroom.edge_fleet import (
    EdgeFleet,
    EdgeFleetError,
    EnrollmentAuthority,
    node_request_attestation_payload,
    result_attestation_payload,
)

NOW = datetime(2026, 7, 18, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


def _keys():
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return private, public


@pytest_asyncio.fixture
async def fleet(tmp_path):
    authority = EnrollmentAuthority(b"e" * 32)
    value = EdgeFleet(tmp_path / "fleet.db", authority, node_allowlist=frozenset({"node-1"}))
    await value.open()
    yield value, authority
    await value.close()


async def _enroll(fleet, authority, *, node_id="node-1", runtime="openclaw", capabilities=("notify",)):
    private, public = _keys()
    token = authority.issue(
        node_id=node_id,
        runtime=runtime,
        public_key=public,
        capabilities=capabilities,
        expires_at=NOW + timedelta(minutes=5),
    )
    node = await fleet.enroll(token, observed_at=NOW)
    return node, private, token


async def test_one_time_enrollment_capability_discovery_and_health(fleet) -> None:
    value, authority = fleet
    node, _private, token = await _enroll(value, authority)
    assert node.capabilities == ("notify",)
    with pytest.raises(EdgeFleetError, match="already consumed"):
        await value.enroll(token, observed_at=NOW)
    refreshed = await value.heartbeat(node.node_id, capabilities=("notify", "camera"), observed_at=NOW)
    assert refreshed.capabilities == ("notify", "camera")
    assert await value.healthy_nodes(observed_at=NOW + timedelta(seconds=30), max_age=timedelta(seconds=60))
    assert await value.healthy_nodes(observed_at=NOW + timedelta(seconds=61), max_age=timedelta(seconds=60)) == ()


async def test_offline_job_queue_capability_lease_and_recovery(fleet) -> None:
    value, authority = fleet
    await value.queue_job(
        job_id="job-1",
        runtime="openclaw",
        required_capabilities=("notify",),
        payload={"message": "hello"},
    )
    node, _private, _token = await _enroll(value, authority, capabilities=("camera",))
    assert await value.acquire(node.node_id, observed_at=NOW) is None
    await value.heartbeat(node.node_id, capabilities=("notify",), observed_at=NOW)
    first = await value.acquire(node.node_id, observed_at=NOW, lease_seconds=10)
    assert first is not None
    assert await value.acquire(node.node_id, observed_at=NOW) is None
    recovered = await value.acquire(node.node_id, observed_at=NOW + timedelta(seconds=11))
    assert recovered is not None
    assert recovered.job_id == first.job_id
    assert recovered.lease_id != first.lease_id


async def test_result_requires_exact_node_attestation_and_active_lease(fleet) -> None:
    value, authority = fleet
    node, private, _token = await _enroll(value, authority)
    await value.queue_job(
        job_id="job-1",
        runtime="openclaw",
        required_capabilities=("notify",),
        payload={"message": "hello"},
    )
    lease = await value.acquire(node.node_id, observed_at=NOW)
    assert lease is not None
    result = {"delivered": True}
    signature = base64.urlsafe_b64encode(
        private.sign(result_attestation_payload(lease.job_id, lease.lease_id, result)),
    ).decode().rstrip("=")
    with pytest.raises(EdgeFleetError, match="attestation"):
        await value.complete(lease, result={"delivered": False}, signature=signature, observed_at=NOW)
    await value.complete(lease, result=result, signature=signature, observed_at=NOW)
    with pytest.raises(EdgeFleetError, match="active lease"):
        await value.complete(lease, result=result, signature=signature, observed_at=NOW)


async def test_expired_or_tampered_enrollment_is_rejected(fleet) -> None:
    value, authority = fleet
    _private, public = _keys()
    token = authority.issue(
        node_id="node-1",
        runtime="hermes",
        public_key=public,
        capabilities=("research",),
        expires_at=NOW,
    )
    with pytest.raises(EdgeFleetError, match="expired"):
        await value.enroll(token, observed_at=NOW + timedelta(seconds=1))
    with pytest.raises(EdgeFleetError, match=r"malformed|signature"):
        await value.enroll(token[:-1] + ("A" if token[-1] != "A" else "B"), observed_at=NOW)


async def test_node_requests_require_exact_fresh_nonreplayed_attestation(fleet) -> None:
    value, authority = fleet
    node, private, _token = await _enroll(value, authority)
    body = {"capabilities": ["notify"]}
    path = "/api/edge-fleet/heartbeat"
    signature = base64.urlsafe_b64encode(
        private.sign(
            node_request_attestation_payload(
                node_id=node.node_id,
                method="POST",
                path=path,
                body=body,
                timestamp=NOW,
                nonce="nonce-1",
            ),
        ),
    ).decode().rstrip("=")
    await value.authenticate_request(
        node_id=node.node_id,
        method="POST",
        path=path,
        body=body,
        timestamp=NOW,
        nonce="nonce-1",
        signature=signature,
        observed_at=NOW,
    )
    with pytest.raises(EdgeFleetError, match="already consumed"):
        await value.authenticate_request(
            node_id=node.node_id,
            method="POST",
            path=path,
            body=body,
            timestamp=NOW,
            nonce="nonce-1",
            signature=signature,
            observed_at=NOW,
        )
    with pytest.raises(EdgeFleetError, match="attestation"):
        await value.authenticate_request(
            node_id=node.node_id,
            method="POST",
            path=path,
            body={"capabilities": ["camera"]},
            timestamp=NOW,
            nonce="nonce-2",
            signature=signature,
            observed_at=NOW,
        )


async def test_node_request_timestamp_must_be_fresh(fleet) -> None:
    value, authority = fleet
    node, private, _token = await _enroll(value, authority)
    body = {}
    signature = base64.urlsafe_b64encode(
        private.sign(
            node_request_attestation_payload(
                node_id=node.node_id,
                method="POST",
                path="/api/edge-fleet/lease",
                body=body,
                timestamp=NOW,
                nonce="nonce-old",
            ),
        ),
    ).decode().rstrip("=")
    with pytest.raises(EdgeFleetError, match="clock skew"):
        await value.authenticate_request(
            node_id=node.node_id,
            method="POST",
            path="/api/edge-fleet/lease",
            body=body,
            timestamp=NOW,
            nonce="nonce-old",
            signature=signature,
            observed_at=NOW + timedelta(minutes=6),
        )
