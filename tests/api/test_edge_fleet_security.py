"""Security-specific tests for the Edge Fleet API.

Covers:
- Replay attack with captured nonce
- Tampered signature verification
- Clock skew boundary (±1 min, ±5 min, ±1 hour)
- Payload size boundary (1MB + 1 byte)
- Concurrent enrollment race
- Concurrent lease acquisition race
- Rate limit enforcement
"""

# ruff: noqa: ANN001, ANN202, D103

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

from mindroom.api.edge_fleet import create_edge_fleet_admin_router, create_edge_fleet_router
from mindroom.edge_fleet import (
    EdgeFleet,
    EdgeFleetError,
    EnrollmentAuthority,
    node_request_attestation_payload,
)

NOW = datetime.now(UTC)
AUTHORITY = EnrollmentAuthority(b"e" * 32)
pytestmark = pytest.mark.asyncio


def _keys() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return private, public


def _sign(private: Ed25519PrivateKey, payload: bytes) -> str:
    return base64.urlsafe_b64encode(private.sign(payload)).decode().rstrip("=")


def _headers(
    private: Ed25519PrivateKey,
    *,
    path: str,
    body: dict[str, object],
    nonce: str,
    timestamp: datetime | None = None,
) -> dict[str, str]:
    ts = timestamp or NOW
    payload = node_request_attestation_payload(
        node_id="test-node",
        method="POST",
        path=path,
        body=body,
        timestamp=ts,
        nonce=nonce,
    )
    return {
        "X-Edge-Node-ID": "test-node",
        "X-Edge-Timestamp": ts.isoformat(),
        "X-Edge-Nonce": nonce,
        "X-Edge-Signature": _sign(private, payload),
    }


@pytest_asyncio.fixture
async def fleet(tmp_path: Path) -> EdgeFleet:
    allowlist = frozenset(
        {
            "test-node",
            "test-node-2",
            *(f"rate-node-{i}" for i in range(7)),
        }
    )
    value = EdgeFleet(tmp_path / "fleet.db", AUTHORITY, node_allowlist=allowlist)
    await value.open()
    yield value
    await value.close()


@pytest_asyncio.fixture
async def api(fleet: EdgeFleet) -> tuple[httpx.AsyncClient, EdgeFleet, Ed25519PrivateKey, str]:
    private, public = _keys()
    token = fleet.issue_enrollment(
        node_id="test-node",
        runtime="openclaw",
        public_key=public,
        capabilities=("notify",),
        expires_at=NOW + timedelta(minutes=5),
    )
    app = FastAPI()
    app.include_router(create_edge_fleet_router(fleet, now=lambda: NOW))
    app.include_router(
        create_edge_fleet_admin_router(fleet, now=lambda: NOW),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, fleet, private, token


# ---------------------------------------------------------------------------
# Replay attack tests
# ---------------------------------------------------------------------------

async def test_replay_enrollment_token_is_denied(api) -> None:
    client, _fleet, _private, token = api
    response = await client.post("/api/edge-fleet/enroll", json={"token": token})
    assert response.status_code == 200
    replay = await client.post("/api/edge-fleet/enroll", json={"token": token})
    assert replay.status_code == 401


async def test_replay_authenticated_request_nonce_is_denied(api) -> None:
    client, fleet, private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})
    await fleet.queue_job(
        job_id="job-1",
        runtime="openclaw",
        required_capabilities=("notify",),
        payload={"message": "hello"},
    )
    body = {"lease_seconds": 60}
    headers = _headers(private, path="/api/edge-fleet/lease", body=body, nonce="replay-nonce")
    response = await client.post("/api/edge-fleet/lease", json=body, headers=headers)
    assert response.status_code == 200
    replay = await client.post("/api/edge-fleet/lease", json=body, headers=headers)
    assert replay.status_code == 401


# ---------------------------------------------------------------------------
# Tampered signature tests
# ---------------------------------------------------------------------------

async def test_tampered_signature_is_denied(api) -> None:
    client, fleet, private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})
    body = {"capabilities": ["notify"]}
    headers = _headers(private, path="/api/edge-fleet/heartbeat", body=body, nonce="tamper-test")
    headers["X-Edge-Signature"] = headers["X-Edge-Signature"][:-1] + (
        "A" if headers["X-Edge-Signature"][-1] != "A" else "B"
    )
    response = await client.post("/api/edge-fleet/heartbeat", json=body, headers=headers)
    assert response.status_code == 401


async def test_wrong_private_key_signature_is_denied(api) -> None:
    client, fleet, _private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})
    wrong_private, _wrong_public = _keys()
    body = {"capabilities": ["notify"]}
    headers = _headers(wrong_private, path="/api/edge-fleet/heartbeat", body=body, nonce="wrong-key")
    response = await client.post("/api/edge-fleet/heartbeat", json=body, headers=headers)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Clock skew boundary tests
# ---------------------------------------------------------------------------

async def test_clock_skew_within_one_minute_is_accepted(api) -> None:
    client, fleet, private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})
    body = {"capabilities": ["notify"]}
    skewed = NOW - timedelta(minutes=1)
    headers = _headers(private, path="/api/edge-fleet/heartbeat", body=body, nonce="skew-1m", timestamp=skewed)
    response = await client.post("/api/edge-fleet/heartbeat", json=body, headers=headers)
    assert response.status_code == 200


async def test_clock_skew_at_five_minutes_is_accepted(api) -> None:
    client, fleet, private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})
    body = {"capabilities": ["notify"]}
    skewed = NOW - timedelta(minutes=5)
    headers = _headers(private, path="/api/edge-fleet/heartbeat", body=body, nonce="skew-5m", timestamp=skewed)
    response = await client.post("/api/edge-fleet/heartbeat", json=body, headers=headers)
    assert response.status_code == 200


async def test_clock_skew_beyond_five_minutes_is_rejected(api) -> None:
    client, fleet, private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})
    body = {"capabilities": ["notify"]}
    skewed = NOW - timedelta(minutes=6)
    headers = _headers(private, path="/api/edge-fleet/heartbeat", body=body, nonce="skew-6m", timestamp=skewed)
    response = await client.post("/api/edge-fleet/heartbeat", json=body, headers=headers)
    assert response.status_code == 401


async def test_clock_skew_one_hour_future_is_rejected(api) -> None:
    client, fleet, private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})
    body = {"capabilities": ["notify"]}
    skewed = NOW + timedelta(hours=1)
    headers = _headers(private, path="/api/edge-fleet/heartbeat", body=body, nonce="skew-1h", timestamp=skewed)
    response = await client.post("/api/edge-fleet/heartbeat", json=body, headers=headers)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Payload size boundary tests
# ---------------------------------------------------------------------------

async def test_oversized_job_payload_is_rejected(api) -> None:
    _client, fleet, _private, _token = api
    with pytest.raises(EdgeFleetError, match="payload is invalid"):
        await fleet.queue_job(
            job_id="oversized",
            runtime="openclaw",
            required_capabilities=(),
            payload={"value": "x" * 1_048_577},  # 1MB + 1 byte
        )


async def test_maximum_job_payload_is_accepted(api) -> None:
    _client, fleet, _private, _token = api
    # Serialized JSON payload ({'value': <n>}) is bounded at 1MB. The {"value":""}
    # envelope adds 12 bytes, so a 1_048_564-char string serializes to exactly 1MB.
    await fleet.queue_job(
        job_id="max-size",
        runtime="openclaw",
        required_capabilities=(),
        payload={"value": "x" * 1_048_564},  # serializes to exactly 1MB
    )
    job = await fleet.job("max-size")
    assert job.status == "queued"


# ---------------------------------------------------------------------------
# Concurrent enrollment race test
# ---------------------------------------------------------------------------

async def test_concurrent_enrollment_race(tmp_path: Path) -> None:
    """Two concurrent enrollments with the same token — only one should succeed."""
    authority = EnrollmentAuthority(b"e" * 32)
    fleet = EdgeFleet(tmp_path / "race.db", authority, node_allowlist=frozenset({"race-node"}))
    await fleet.open()
    try:
        private, public = _keys()
        token = authority.issue(
            node_id="race-node",
            runtime="hermes",
            public_key=public,
            capabilities=("research",),
            expires_at=NOW + timedelta(minutes=5),
        )

        async def try_enroll() -> bool:
            try:
                await fleet.enroll(token, observed_at=NOW)
                return True
            except EdgeFleetError:
                return False

        results = await asyncio.gather(try_enroll(), try_enroll(), try_enroll())
        success_count = sum(1 for r in results if r)
        assert success_count == 1, f"Expected exactly 1 success, got {success_count}"
    finally:
        await fleet.close()


# ---------------------------------------------------------------------------
# Concurrent lease race test
# ---------------------------------------------------------------------------

async def test_concurrent_lease_race(api) -> None:
    """Two nodes racing for the same job — only one should get it."""
    client, fleet, private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})

    # Enroll a second node
    private2, public2 = _keys()
    token2 = fleet.issue_enrollment(
        node_id="test-node-2",
        runtime="openclaw",
        public_key=public2,
        capabilities=("notify",),
        expires_at=NOW + timedelta(minutes=5),
    )
    await fleet.enroll(token2, observed_at=NOW)

    await fleet.queue_job(
        job_id="race-job",
        runtime="openclaw",
        required_capabilities=("notify",),
        payload={"message": "race"},
    )

    async def try_lease(node_id: str, private_key: Ed25519PrivateKey) -> bool:
        body = {"lease_seconds": 60}
        headers = _headers(private_key, path="/api/edge-fleet/lease", body=body, nonce=f"race-{node_id}")
        response = await client.post("/api/edge-fleet/lease", json=body, headers=headers)
        return response.status_code == 200 and response.json() is not None

    results = await asyncio.gather(
        try_lease("test-node", private),
        try_lease("test-node-2", private2),
    )
    success_count = sum(1 for r in results if r)
    assert success_count == 1, f"Expected exactly 1 lease success, got {success_count}"


# ---------------------------------------------------------------------------
# Rate limit enforcement test
# ---------------------------------------------------------------------------

async def test_enroll_rate_limit_is_enforced(api) -> None:
    """Enroll endpoint should rate-limit after 5 requests per IP."""
    client, fleet, _private, _token = api
    responses = []
    for i in range(7):
        private, public = _keys()
        token = fleet.issue_enrollment(
            node_id=f"rate-node-{i}",
            runtime="openclaw",
            public_key=public,
            capabilities=("notify",),
            expires_at=NOW + timedelta(minutes=5),
        )
        response = await client.post("/api/edge-fleet/enroll", json={"token": token})
        responses.append(response.status_code)
    # First 5 should succeed (200), remaining should be rate-limited (429)
    assert responses[:5] == [200] * 5, f"Expected first 5 to succeed: {responses[:5]}"
    assert 429 in responses[5:], f"Expected rate limit in last 2: {responses[5:]}"


# ---------------------------------------------------------------------------
# Input validation tests
# ---------------------------------------------------------------------------

async def test_empty_node_id_is_rejected(api) -> None:
    _client, fleet, _private, _token = api
    with pytest.raises(EdgeFleetError, match="identity and capabilities are required"):
        fleet.issue_enrollment(
            node_id="",
            runtime="openclaw",
            public_key="x" * 32,
            capabilities=("notify",),
            expires_at=NOW + timedelta(minutes=5),
        )


async def test_invalid_runtime_is_rejected(api) -> None:
    _client, fleet, _private, _token = api
    with pytest.raises(EdgeFleetError, match="schema or runtime"):
        import hashlib
        import hmac
        import secrets

        private, public = _keys()
        # Build a claim with invalid runtime and sign it with the correct key
        claim = {
            "capabilities": ["notify"],
            "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
            "node_id": "bad-runtime",
            "nonce": secrets.token_hex(16),
            "public_key": public,
            "runtime": "invalid_runtime",
            "schema": "mindroom.edge-enrollment/1",
        }
        payload = json.dumps(claim, separators=(",", ":"), sort_keys=True).encode()
        signature = hmac.new(b"e" * 32, payload, hashlib.sha256).digest()
        token = (
            f"{base64.urlsafe_b64encode(payload).decode().rstrip('=')}."
            f"{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"
        )
        await fleet.enroll(token, observed_at=NOW)