"""Tests for the authenticated edge-fleet HTTP boundary."""

# ruff: noqa: ANN001, ANN201, ANN202, D103

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, HTTPException

from mindroom.api.edge_fleet import create_edge_fleet_admin_router, create_edge_fleet_router
from mindroom.edge_fleet import EdgeFleet, EdgeFleetError, EnrollmentAuthority, node_request_attestation_payload

NOW = datetime(2026, 7, 18, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def api(tmp_path):
    authority = EnrollmentAuthority(b"e" * 32)
    fleet = EdgeFleet(tmp_path / "fleet.db", authority, node_allowlist=frozenset({"node-1"}))
    await fleet.open()
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    token = authority.issue(
        node_id="node-1",
        runtime="openclaw",
        public_key=public,
        capabilities=("notify",),
        expires_at=NOW + timedelta(minutes=5),
    )
    app = FastAPI()
    app.include_router(create_edge_fleet_router(fleet, now=lambda: NOW))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, fleet, private, token
    await fleet.close()


def _headers(private, *, path, body, nonce):
    signature = private.sign(
        node_request_attestation_payload(
            node_id="node-1",
            method="POST",
            path=path,
            body=body,
            timestamp=NOW,
            nonce=nonce,
        ),
    )
    return {
        "X-Edge-Node-ID": "node-1",
        "X-Edge-Timestamp": NOW.isoformat(),
        "X-Edge-Nonce": nonce,
        "X-Edge-Signature": base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }


async def test_enroll_and_authenticated_heartbeat(api) -> None:
    client, _fleet, private, token = api
    response = await client.post("/api/edge-fleet/enroll", json={"token": token})
    assert response.status_code == 200
    body = {"capabilities": ["notify", "camera"]}
    response = await client.post(
        "/api/edge-fleet/heartbeat",
        json=body,
        headers=_headers(private, path="/api/edge-fleet/heartbeat", body=body, nonce="heartbeat-1"),
    )
    assert response.status_code == 200
    assert response.json()["capabilities"] == ["notify", "camera"]


async def test_lease_requires_auth_and_replay_is_denied(api) -> None:
    client, fleet, private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})
    await fleet.queue_job(
        job_id="job-1",
        runtime="openclaw",
        required_capabilities=("notify",),
        payload={"message": "hello"},
    )
    body = {"lease_seconds": 60}
    assert (await client.post("/api/edge-fleet/lease", json=body)).status_code == 422
    headers = _headers(private, path="/api/edge-fleet/lease", body=body, nonce="lease-1")
    response = await client.post("/api/edge-fleet/lease", json=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["job_id"] == "job-1"
    replay = await client.post("/api/edge-fleet/lease", json=body, headers=headers)
    assert replay.status_code == 401
    assert "already consumed" not in replay.text


async def test_tampered_authenticated_body_is_denied(api) -> None:
    client, _fleet, private, token = api
    await client.post("/api/edge-fleet/enroll", json={"token": token})
    signed = {"capabilities": ["notify"]}
    headers = _headers(private, path="/api/edge-fleet/heartbeat", body=signed, nonce="heartbeat-2")
    response = await client.post(
        "/api/edge-fleet/heartbeat",
        json={"capabilities": ["camera"]},
        headers=headers,
    )
    assert response.status_code == 401


async def test_authenticated_coordinator_can_issue_enrollments_and_queue_jobs(tmp_path) -> None:
    authority = EnrollmentAuthority(b"e" * 32)
    fleet = EdgeFleet(tmp_path / "fleet.db", authority)
    await fleet.open()
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    async def require_admin(x_admin: str | None = None) -> None:
        if x_admin != "yes":
            raise HTTPException(401)

    app = FastAPI()
    app.include_router(
        create_edge_fleet_admin_router(fleet, now=lambda: NOW),
        dependencies=[Depends(require_admin)],
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        request = {
            "node_id": "node-2",
            "runtime": "hermes",
            "public_key": public,
            "capabilities": ["research"],
            "expires_in_seconds": 60,
        }
        assert (await client.post("/api/edge-fleet-admin/enrollments", json=request)).status_code == 401
        issued = await client.post("/api/edge-fleet-admin/enrollments?x_admin=yes", json=request)
        assert issued.status_code == 200
        assert issued.headers["Cache-Control"] == "no-store"
        assert authority.verify(issued.json()["token"], observed_at=NOW)["node_id"] == "node-2"

        queued = await client.post(
            "/api/edge-fleet-admin/jobs?x_admin=yes",
            json={
                "job_id": "job-admin-1",
                "runtime": "hermes",
                "required_capabilities": ["research"],
                "payload": {"query": "bounded"},
            },
        )
        assert queued.status_code == 201
        assert queued.json()["status"] == "queued"
        fetched = await client.get("/api/edge-fleet-admin/jobs/job-admin-1?x_admin=yes")
        assert fetched.json() == queued.json()
    await fleet.close()


async def test_coordinator_job_payload_is_bounded(api) -> None:
    _client, fleet, _private, _token = api
    with pytest.raises(EdgeFleetError, match="payload is invalid"):
        await fleet.queue_job(
            job_id="oversized",
            runtime="openclaw",
            required_capabilities=(),
            payload={"value": "x" * 1_048_576},
        )
