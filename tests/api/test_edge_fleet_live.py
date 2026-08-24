"""U2 live-fleet slice tests for C3–C6 (issue-enrollment, enqueue, node path, health)."""

# ruff: noqa: ANN001, ANN201, ANN202, D103

from __future__ import annotations

import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from mindroom.api.edge_fleet import create_edge_fleet_admin_router, create_edge_fleet_router
from mindroom.api.main import (
    EDGE_FLEET_STATUS_ERROR,
    EDGE_FLEET_STATUS_UNMOUNTED,
    edge_fleet_activation_status,
)
from mindroom.edge_fleet import (
    EdgeFleet,
    EnrollmentAuthority,
    node_request_attestation_payload,
    result_attestation_payload,
)

NOW = datetime(2026, 8, 20, tzinfo=UTC)


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
    node_id: str,
    path: str,
    body: dict[str, object],
    nonce: str,
) -> dict[str, str]:
    payload = node_request_attestation_payload(
        node_id=node_id,
        method="POST",
        path=path,
        body=body,
        timestamp=NOW,
        nonce=nonce,
    )
    return {
        "X-Edge-Node-ID": node_id,
        "X-Edge-Timestamp": NOW.isoformat(),
        "X-Edge-Nonce": nonce,
        "X-Edge-Signature": _sign(private, payload),
    }


@pytest_asyncio.fixture
async def live_api(tmp_path):
    authority = EnrollmentAuthority(b"e" * 32)
    allowlist = frozenset({"openclaw-node-1", "hermes-node-1", "node-2"})
    fleet = EdgeFleet(tmp_path / "fleet.db", authority, node_allowlist=allowlist)
    await fleet.open()

    async def require_admin() -> dict:
        return {"user_id": "operator-1"}

    async def connected_tailnet() -> None:
        return None

    app = FastAPI()
    app.include_router(create_edge_fleet_router(fleet, now=lambda: NOW, require_tailnet=connected_tailnet))
    app.include_router(
        create_edge_fleet_admin_router(fleet, now=lambda: NOW),
        dependencies=[Depends(require_admin)],
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, fleet, authority
    await fleet.close()


async def _finish_runtime(
    client: httpx.AsyncClient,
    *,
    node_id: str,
    runtime: str,
    capabilities: list[str],
    job_id: str,
) -> None:
    private, public = _keys()
    issued = await client.post(
        "/api/edge-fleet-admin/enrollments",
        json={
            "node_id": node_id,
            "runtime": runtime,
            "public_key": public,
            "capabilities": capabilities,
            "expires_in_seconds": 600,
        },
    )
    assert issued.status_code == 200, issued.text
    assert issued.headers["Cache-Control"] == "no-store"
    token = issued.json()["token"]
    assert issued.json()["expires_at"]

    enroll = await client.post("/api/edge-fleet/enroll", json={"token": token})
    assert enroll.status_code == 200
    assert enroll.json()["node_id"] == node_id
    assert enroll.json()["runtime"] == runtime

    heartbeat_body = {"capabilities": capabilities}
    heartbeat = await client.post(
        "/api/edge-fleet/heartbeat",
        json=heartbeat_body,
        headers=_headers(
            private,
            node_id=node_id,
            path="/api/edge-fleet/heartbeat",
            body=heartbeat_body,
            nonce=f"{node_id}-hb",
        ),
    )
    assert heartbeat.status_code == 200

    queued = await client.post(
        "/api/edge-fleet-admin/jobs",
        json={
            "job_id": job_id,
            "runtime": runtime,
            "required_capabilities": capabilities,
            "payload": {"task": runtime},
        },
    )
    assert queued.status_code == 201
    assert queued.json()["status"] == "queued"

    lease_body = {"lease_seconds": 60}
    lease = await client.post(
        "/api/edge-fleet/lease",
        json=lease_body,
        headers=_headers(
            private,
            node_id=node_id,
            path="/api/edge-fleet/lease",
            body=lease_body,
            nonce=f"{node_id}-lease",
        ),
    )
    assert lease.status_code == 200
    lease_json = lease.json()
    assert lease_json["job_id"] == job_id
    result = {"ok": True, "runtime": runtime}
    complete_body = {
        "job_id": job_id,
        "lease_id": lease_json["lease_id"],
        "lease_expires_at": lease_json["expires_at"],
        "result": result,
        "result_signature": _sign(
            private,
            result_attestation_payload(job_id, lease_json["lease_id"], result),
        ),
    }
    complete = await client.post(
        "/api/edge-fleet/complete",
        json=complete_body,
        headers=_headers(
            private,
            node_id=node_id,
            path="/api/edge-fleet/complete",
            body=complete_body,
            nonce=f"{node_id}-complete",
        ),
    )
    assert complete.status_code == 204
    inspected = await client.get(f"/api/edge-fleet-admin/jobs/{job_id}")
    assert inspected.status_code == 200
    assert inspected.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_c3_issue_enrollment_requires_auth_and_returns_no_store_token(tmp_path) -> None:
    authority = EnrollmentAuthority(b"e" * 32)
    fleet = EdgeFleet(tmp_path / "fleet.db", authority)
    await fleet.open()
    _private, public = _keys()

    async def require_admin(x_admin: str | None = None) -> None:
        if x_admin != "yes":
            raise HTTPException(401)

    app = FastAPI()
    app.include_router(
        create_edge_fleet_admin_router(fleet, now=lambda: NOW),
        dependencies=[Depends(require_admin)],
    )
    request = {
        "node_id": "node-2",
        "runtime": "openclaw",
        "public_key": public,
        "capabilities": ["notify"],
        "expires_in_seconds": 60,
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/edge-fleet-admin/enrollments", json=request)).status_code == 401
        issued = await client.post("/api/edge-fleet-admin/enrollments?x_admin=yes", json=request)
        assert issued.status_code == 200
        assert issued.headers["Cache-Control"] == "no-store"
        body = issued.json()
        assert body["token"]
        assert body["expires_at"]
        assert authority.verify(body["token"], observed_at=NOW)["runtime"] == "openclaw"
        invalid = await client.post(
            "/api/edge-fleet-admin/enrollments?x_admin=yes",
            json={**request, "runtime": "unknown"},
        )
        assert invalid.status_code == 422
    await fleet.close()


@pytest.mark.asyncio
async def test_c4_enqueue_compatible_job_and_inspect(live_api) -> None:
    client, fleet, _authority = live_api
    queued = await client.post(
        "/api/edge-fleet-admin/jobs",
        json={
            "job_id": "job-openclaw-1",
            "runtime": "openclaw",
            "required_capabilities": ["notify"],
            "payload": {"task": "ping"},
        },
    )
    assert queued.status_code == 201
    assert queued.json()["status"] == "queued"
    fetched = await client.get("/api/edge-fleet-admin/jobs/job-openclaw-1")
    assert fetched.status_code == 200
    assert fetched.json() == queued.json()
    missing = await client.get("/api/edge-fleet-admin/jobs/never-queued")
    assert missing.status_code == 404
    stored = await fleet.job("job-openclaw-1")
    assert stored.status == "queued"
    assert stored.runtime == "openclaw"


@pytest.mark.asyncio
async def test_c4_equivocation_is_409_and_invalid_payload_is_422(live_api) -> None:
    client, _fleet, _authority = live_api
    body = {
        "job_id": "job-conflict",
        "runtime": "hermes",
        "required_capabilities": ["research"],
        "payload": {"query": "one"},
    }
    assert (await client.post("/api/edge-fleet-admin/jobs", json=body)).status_code == 201
    conflict = await client.post(
        "/api/edge-fleet-admin/jobs",
        json={**body, "payload": {"query": "two"}},
    )
    assert conflict.status_code == 409
    oversized = await client.post(
        "/api/edge-fleet-admin/jobs",
        json={
            "job_id": "oversized",
            "runtime": "openclaw",
            "required_capabilities": [],
            "payload": {"value": "x" * 1_048_576},
        },
    )
    assert oversized.status_code == 422


@pytest.mark.asyncio
async def test_c5_openclaw_and_hermes_enroll_take_and_finish(live_api) -> None:
    client, _fleet, _authority = live_api
    await _finish_runtime(
        client,
        node_id="openclaw-node-1",
        runtime="openclaw",
        capabilities=["notify"],
        job_id="job-openclaw-finish",
    )
    await _finish_runtime(
        client,
        node_id="hermes-node-1",
        runtime="hermes",
        capabilities=["research"],
        job_id="job-hermes-finish",
    )


def test_c6_activation_status_sentences() -> None:
    assert edge_fleet_activation_status(enabled=False) == EDGE_FLEET_STATUS_UNMOUNTED
    assert edge_fleet_activation_status(enabled=True, healthy_nodes=0) == "Edge fleet is mounted with 0 healthy nodes."
    assert edge_fleet_activation_status(enabled=True, healthy_nodes=1) == "Edge fleet is mounted with 1 healthy node."
    assert edge_fleet_activation_status(enabled=True, error="boom") == EDGE_FLEET_STATUS_ERROR


def test_c6_health_reports_unmounted_fleet(monkeypatch) -> None:
    from mindroom.api import main as api_main
    from mindroom.matrix.health import reset_matrix_sync_health

    reset_matrix_sync_health()
    monkeypatch.setattr(api_main, "_edge_fleet_instance", None)
    response = TestClient(api_main.app).get("/api/health")
    assert response.status_code == 200
    fragment = response.json()["edge_fleet"]
    assert fragment == {"enabled": False, "status": EDGE_FLEET_STATUS_UNMOUNTED}


def test_c6_health_reports_mounted_healthy_nodes(monkeypatch) -> None:
    from mindroom.api import main as api_main
    from mindroom.matrix.health import reset_matrix_sync_health

    class _Node:
        pass

    reset_matrix_sync_health()
    fleet = AsyncMock()
    fleet.healthy_nodes = AsyncMock(return_value=(_Node(),))
    monkeypatch.setattr(api_main, "_edge_fleet_instance", fleet)
    client = TestClient(api_main.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    fragment = response.json()["edge_fleet"]
    assert fragment["enabled"] is True
    assert fragment["healthy_nodes"] == 1
    assert fragment["status"] == "Edge fleet is mounted with 1 healthy node."


def test_c6_health_reports_mounted_error(monkeypatch) -> None:
    from mindroom.api import main as api_main
    from mindroom.matrix.health import reset_matrix_sync_health

    reset_matrix_sync_health()
    fleet = AsyncMock()
    fleet.healthy_nodes = AsyncMock(side_effect=RuntimeError("edge fleet is not open"))
    monkeypatch.setattr(api_main, "_edge_fleet_instance", fleet)
    client = TestClient(api_main.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    fragment = response.json()["edge_fleet"]
    assert fragment["enabled"] is True
    assert fragment["error"] == "edge fleet is not open"
    assert fragment["status"] == EDGE_FLEET_STATUS_ERROR


def test_fr21_operator_start_command_documents_both_runtimes() -> None:
    from mindroom.edge_node import _build_parser

    parser = _build_parser()
    openclaw = parser.parse_args(
        [
            "--identity",
            "/tmp/openclaw.json",
            "--node-id",
            "openclaw-node-1",
            "--runtime",
            "openclaw",
            "--capability",
            "notify",
            "--token",
            "tok",
            "--once",
        ],
    )
    hermes = parser.parse_args(
        [
            "--identity",
            "/tmp/hermes.json",
            "--node-id",
            "hermes-node-1",
            "--runtime",
            "hermes",
            "--capability",
            "research",
            "--token",
            "tok",
            "--once",
        ],
    )
    assert openclaw.runtime == "openclaw"
    assert hermes.runtime == "hermes"
    assert openclaw.once is True
    assert parser.description is not None
    assert "MindRoom does not start or supervise this process" in parser.description
    assert {action.dest for action in parser._actions} >= {"runtime", "identity", "token", "once"}


def test_c6_docs_quote_health_sentence() -> None:
    from pathlib import Path

    docs = (Path(__file__).resolve().parents[2] / "docs" / "edge-fleet.md").read_text(encoding="utf-8")
    assert EDGE_FLEET_STATUS_UNMOUNTED in docs
    assert "Edge fleet is mounted with" in docs
    assert "Production activation awaiting explicit security approval from Dwayne, do not activate" not in docs
    assert "Security approval obtained — implementation ready" not in docs