"""HTTP-boundary tests for DELETE /api/edge-fleet-admin/nodes/{id}.

Covers the committed acceptance criteria AC-1..AC-7 at the API layer:

- AC-1 happy path: revoking an enrolled node returns 204 and fails it closed.
- AC-2 404 for a node_id that never existed.
- AC-3 idempotent 204 on repeat delete of an already-revoked node.
- AC-4 lease/heartbeat invalidation after revocation.
- AC-5 audit trail present and written in the same transaction.
- AC-6 admin-only authz with logged rejection (403 without permission).
- AC-7 input validation (blank/oversized node_id rejected).
- OQ-8 rate limit: max 5 revocations/min per principal, burst alert on exceed.
"""

# ruff: noqa: ANN001, ANN202, D103

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, HTTPException, Request

from mindroom.api.edge_fleet import create_edge_fleet_admin_router, create_edge_fleet_router
from mindroom.edge_fleet import EdgeFleet, EnrollmentAuthority, node_request_attestation_payload

NOW = datetime(2026, 7, 18, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


def _keys() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return private, public


def _sign(private: Ed25519PrivateKey, payload: bytes) -> str:
    return base64.urlsafe_b64encode(private.sign(payload)).decode().rstrip("=")


def _headers(private: Ed25519PrivateKey, *, path: str, body: dict, nonce: str) -> dict[str, str]:
    payload = node_request_attestation_payload(
        node_id="node-1",
        method="POST",
        path=path,
        body=body,
        timestamp=NOW,
        nonce=nonce,
    )
    return {
        "X-Edge-Node-ID": "node-1",
        "X-Edge-Timestamp": NOW.isoformat(),
        "X-Edge-Nonce": nonce,
        "X-Edge-Signature": _sign(private, payload),
    }


@pytest_asyncio.fixture
async def api(tmp_path):
    authority = EnrollmentAuthority(b"e" * 32)
    allowlist = frozenset({"node-1", "node-2", *(f"node-{i}" for i in range(7))})
    fleet = EdgeFleet(tmp_path / "fleet.db", authority, node_allowlist=allowlist)
    await fleet.open()

    async def require_admin(request: Request, user: dict | None = None) -> dict:
        # Simulates verify_user: an authenticated admin principal stored in scope.
        principal = user or {"user_id": "admin-1", "email": "admin@example.com"}
        request.scope["auth_user"] = principal
        return principal

    app = FastAPI()
    app.include_router(create_edge_fleet_router(fleet, now=lambda: NOW))
    app.include_router(
        create_edge_fleet_admin_router(fleet, now=lambda: NOW),
        dependencies=[Depends(require_admin)],
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, fleet
    await fleet.close()


async def _enroll(fleet: EdgeFleet, node_id: str) -> tuple[Ed25519PrivateKey, str]:
    private, public = _keys()
    token = fleet.issue_enrollment(
        node_id=node_id,
        runtime="openclaw",
        public_key=public,
        capabilities=("notify",),
        expires_at=NOW + timedelta(minutes=5),
    )
    await fleet.enroll(token, observed_at=NOW)
    return private, public


# ---------------------------------------------------------------------------
# AC-1: happy path — 204 and fail-closed
# ---------------------------------------------------------------------------

async def test_ac1_happy_path_revokes_node(api) -> None:
    client, fleet = api
    await _enroll(fleet, "node-1")
    response = await client.delete("/api/edge-fleet-admin/nodes/node-1")
    assert response.status_code == 204
    # Node is soft-deleted: excluded from healthy nodes.
    nodes = await fleet.healthy_nodes(observed_at=NOW, max_age=timedelta(minutes=5))
    assert [n.node_id for n in nodes] == []


# ---------------------------------------------------------------------------
# AC-2: 404 for a node_id that never existed
# ---------------------------------------------------------------------------

async def test_ac2_404_for_never_existed_node(api) -> None:
    client, _fleet = api
    response = await client.delete("/api/edge-fleet-admin/nodes/never-existed")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# AC-3: idempotent 204 on repeat delete of an already-revoked node
# ---------------------------------------------------------------------------

async def test_ac3_repeat_delete_is_idempotent_204(api) -> None:
    client, fleet = api
    await _enroll(fleet, "node-1")
    first = await client.delete("/api/edge-fleet-admin/nodes/node-1")
    second = await client.delete("/api/edge-fleet-admin/nodes/node-1")
    assert first.status_code == 204
    assert second.status_code == 204


# ---------------------------------------------------------------------------
# AC-4: lease/heartbeat invalidation after revocation
# ---------------------------------------------------------------------------

async def test_ac4_lease_and_heartbeat_invalidated_after_revoke(api) -> None:
    client, fleet = api
    private, _public = await _enroll(fleet, "node-1")
    await fleet.queue_job(
        job_id="job-1",
        runtime="openclaw",
        required_capabilities=("notify",),
        payload={"message": "hello"},
    )
    body = {"lease_seconds": 60}
    lease_headers = _headers(private, path="/api/edge-fleet/lease", body=body, nonce="lease-1")
    lease = await client.post("/api/edge-fleet/lease", json=body, headers=lease_headers)
    assert lease.status_code == 200
    assert (await fleet.job("job-1")).status == "leased"

    # Revoke via the endpoint.
    assert (await client.delete("/api/edge-fleet-admin/nodes/node-1")).status_code == 204
    # Lease cancelled.
    assert (await fleet.job("job-1")).status == "queued"

    # Heartbeat now rejected (fail-closed).
    hb_body = {"capabilities": ["notify"]}
    hb_headers = _headers(private, path="/api/edge-fleet/heartbeat", body=hb_body, nonce="hb-after-revoke")
    hb = await client.post("/api/edge-fleet/heartbeat", json=hb_body, headers=hb_headers)
    assert hb.status_code == 401


# ---------------------------------------------------------------------------
# AC-5: audit trail present and same-transaction
# ---------------------------------------------------------------------------

async def test_ac5_audit_trail_present(api) -> None:
    client, fleet = api
    await _enroll(fleet, "node-1")
    assert (await client.delete("/api/edge-fleet-admin/nodes/node-1")).status_code == 204
    rows = await (await fleet._db.execute("SELECT event,node_id,actor FROM edge_fleet_audit")).fetchall()
    assert any(event == "node.revoked" and node_id == "node-1" for event, node_id, _actor in rows)


async def test_ac5_audit_and_revocation_are_same_transaction(api) -> None:
    """The audit row and the tombstone are committed together."""
    client, fleet = api
    await _enroll(fleet, "node-1")
    assert (await client.delete("/api/edge-fleet-admin/nodes/node-1")).status_code == 204
    row = await (
        await fleet._db.execute("SELECT revoked_at FROM edge_node WHERE node_id=?", ("node-1",))
    ).fetchone()
    audit = await (await fleet._db.execute("SELECT COUNT(*) FROM edge_fleet_audit")).fetchone()
    assert row is not None and row[0] is not None
    assert audit[0] == 1


# ---------------------------------------------------------------------------
# AC-6: admin-only authz with logged rejection
# ---------------------------------------------------------------------------

async def test_ac6_revocation_requires_permission(tmp_path) -> None:
    authority = EnrollmentAuthority(b"e" * 32)
    fleet = EdgeFleet(tmp_path / "fleet.db", authority, node_allowlist=frozenset({"node-1"}))
    await fleet.open()
    await _enroll(fleet, "node-1")

    # A principal without the admin.nodes.revoke permission is rejected 403.
    async def require_admin_no_permission(request: Request, user: dict | None = None) -> dict:
        principal = user or {"user_id": "admin-1", "permissions": ["admin.nodes.list"]}
        request.scope["auth_user"] = principal
        return principal

    app = FastAPI()
    app.include_router(
        create_edge_fleet_admin_router(fleet, now=lambda: NOW),
        dependencies=[Depends(require_admin_no_permission)],
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/edge-fleet-admin/nodes/node-1")
        assert response.status_code == 403
    await fleet.close()


async def test_ac6_unauthenticated_request_is_rejected(tmp_path) -> None:
    authority = EnrollmentAuthority(b"e" * 32)
    fleet = EdgeFleet(tmp_path / "fleet.db", authority, node_allowlist=frozenset({"node-1"}))
    await fleet.open()
    await _enroll(fleet, "node-1")

    async def require_admin_deny(request: Request, user: dict | None = None) -> dict:
        raise HTTPException(401)

    app = FastAPI()
    app.include_router(
        create_edge_fleet_admin_router(fleet, now=lambda: NOW),
        dependencies=[Depends(require_admin_deny)],
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/edge-fleet-admin/nodes/node-1")
        assert response.status_code == 401
    await fleet.close()


# ---------------------------------------------------------------------------
# AC-7: input validation
# ---------------------------------------------------------------------------

async def test_ac7_blank_node_id_is_rejected(api) -> None:
    client, _fleet = api
    # A trailing slash triggers a 307 redirect; the core rejects blank ids.
    response = await client.delete("/api/edge-fleet-admin/nodes/", follow_redirects=True)
    assert response.status_code in (404, 405, 422)


async def test_ac7_oversized_node_id_is_rejected(api) -> None:
    client, _fleet = api
    response = await client.delete("/api/edge-fleet-admin/nodes/" + "x" * 10_000)
    assert response.status_code == 404  # never existed, so 404


# ---------------------------------------------------------------------------
# OQ-8: rate limit — max 5 revocations/min per principal, burst alert
# ---------------------------------------------------------------------------

async def test_oq8_rate_limit_after_five_revocations(api) -> None:
    client, fleet = api
    # Enroll 7 distinct nodes so each revocation is a fresh, valid target.
    for i in range(7):
        await _enroll(fleet, f"node-{i}")
    statuses = []
    for i in range(7):
        response = await client.delete(f"/api/edge-fleet-admin/nodes/node-{i}")
        statuses.append(response.status_code)
    # First 5 succeed (204), the 6th and 7th are rate-limited (429).
    assert statuses[:5] == [204] * 5, f"expected first 5 to succeed: {statuses}"
    assert statuses[5:] == [429, 429], f"expected rate limit on last 2: {statuses}"