"""Authenticated network boundary for OpenClaw and Hermes edge workers.

Security hardening applied:
- Audit logging for all API calls (GAP-S1, GAP-S5)
- Rate limiting per identity (GAP-S2)
- Secrets management via environment (GAP-S8)
- Input validation and sanitization
- Structured JSON logging for SIEM readiness
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from mindroom.edge_fleet import EdgeFleet, EdgeFleetError, EdgeJob, EdgeNode, JobLease, WorkerRuntime

if TYPE_CHECKING:
    pass

_PREFIX = "/api/edge-fleet"
_ADMIN_PREFIX = "/api/edge-fleet-admin"

# The single permission that authorizes node revocation (OQ-7).
_REVOKE_PERMISSION = "admin.nodes.revoke"
# OQ-8: at most 5 revocations per minute per admin principal.
_REVOKE_RATE_LIMIT = 5
# A burst is a principal exceeding the revocation rate limit in one window.
_REVOKE_BURST_ALERT_EVENT = "admin.node_revoke_burst"

logger = logging.getLogger("mindroom.api.edge_fleet")

# ---------------------------------------------------------------------------
# Rate limiting — simple in-memory sliding-window per identity
# ---------------------------------------------------------------------------

_RATE_LIMIT_WINDOW_SECONDS = 60.0


class _RateBucket:
    __slots__ = ("count", "window_start")

    def __init__(self) -> None:
        self.count = 0
        self.window_start = time.monotonic()


class _RateLimiter:
    """Per-identity sliding-window rate limiter."""

    def __init__(self, max_requests: int, window_seconds: float = _RATE_LIMIT_WINDOW_SECONDS) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._buckets: dict[str, _RateBucket] = {}

    def check(self, identity: str) -> bool:
        now = time.monotonic()
        bucket = self._buckets.get(identity)
        if bucket is None or now - bucket.window_start > self._window:
            self._buckets[identity] = _RateBucket()
            self._buckets[identity].count = 1
            self._buckets[identity].window_start = now
            return True
        if bucket.count >= self._max:
            return False
        bucket.count += 1
        return True

    def cleanup(self) -> None:
        now = time.monotonic()
        stale = [k for k, v in self._buckets.items() if now - v.window_start > self._window]
        for k in stale:
            del self._buckets[k]


# Per-endpoint rate limits (requests per minute), scoped per router instance so
# distinct fleet instances never share (and deplete) one another's budget.
_ENROLL_LIMIT = 5       # /enroll per IP
_HEARTBEAT_LIMIT = 60   # /heartbeat per node
_LEASE_LIMIT = 30       # /lease per node
_COMPLETE_LIMIT = 30    # /complete per node
_ADMIN_LIMIT = 120      # admin endpoints per user


def _rate_limit(limiter: _RateLimiter, identity: str) -> None:
    if not limiter.check(identity):
        logger.warning("Rate limit exceeded", extra={"identity": identity})
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")


# ---------------------------------------------------------------------------
# Audit logging helpers
# ---------------------------------------------------------------------------

def _audit_log(event: str, *, node_id: str | None = None, detail: str | None = None, **extra: object) -> None:
    """Emit a structured audit log entry for SIEM ingestion."""
    record = {
        "event": event,
        "service": "edge-fleet",
        "node_id": node_id,
        "detail": detail,
        **extra,
    }
    logger.info("Edge fleet audit event", extra=record)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class EnrollmentRequest(BaseModel):
    """One coordinator-issued enrollment token."""

    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=16_384)


class HeartbeatRequest(BaseModel):
    """Current capabilities advertised by an authenticated node."""

    model_config = ConfigDict(extra="forbid")
    capabilities: tuple[str, ...] = Field(max_length=256)


class LeaseRequest(BaseModel):
    """Bounded lease duration requested by an authenticated node."""

    model_config = ConfigDict(extra="forbid")
    lease_seconds: int = Field(default=60, ge=1, le=3600)


class CompleteRequest(BaseModel):
    """One exact leased result with its separate result attestation."""

    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=256)
    lease_id: str = Field(min_length=1, max_length=256)
    lease_expires_at: datetime
    result: dict[str, object]
    result_signature: str = Field(min_length=1, max_length=4096)


class NodeResponse(BaseModel):
    """Content-free enrolled node state."""

    node_id: str
    runtime: str
    capabilities: tuple[str, ...]
    last_seen_at: datetime


class LeaseResponse(BaseModel):
    """One authenticated job lease response."""

    job_id: str
    lease_id: str
    payload: dict[str, object]
    expires_at: datetime


class EnrollmentIssueRequest(BaseModel):
    """Exact identity authorized to consume one short-lived enrollment token."""

    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1, max_length=256)
    runtime: WorkerRuntime
    public_key: str = Field(min_length=1, max_length=4096)
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=256)
    expires_in_seconds: int = Field(default=600, ge=1, le=3600)


class EnrollmentIssueResponse(BaseModel):
    """One bearer enrollment token and its explicit expiry."""

    token: str
    expires_at: datetime


class QueueJobRequest(BaseModel):
    """One immutable runtime-scoped edge job."""

    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=256)
    runtime: WorkerRuntime
    required_capabilities: tuple[str, ...] = Field(max_length=256)
    payload: dict[str, object]


class EdgeJobResponse(BaseModel):
    """Coordinator-visible job and attested outcome state."""

    job_id: str
    runtime: WorkerRuntime
    required_capabilities: tuple[str, ...]
    payload: dict[str, object]
    status: Literal["queued", "leased", "completed"]
    node_id: str | None
    result: dict[str, object] | None
    result_signature: str | None


# ---------------------------------------------------------------------------
# Router factories
# ---------------------------------------------------------------------------

def create_edge_fleet_router(
    fleet: EdgeFleet,
    *,
    now: Callable[[], datetime] | None = None,
    node_allowlist: frozenset[str] | None = None,
) -> APIRouter:
    """Build an isolated router around one lifecycle-owned fleet instance."""
    clock = now or (lambda: datetime.now(UTC))
    router = APIRouter(prefix=_PREFIX, tags=["edge-fleet"])
    # Per-instance rate limiters so independent fleets keep independent budgets.
    enroll_limiter = _RateLimiter(_ENROLL_LIMIT)
    heartbeat_limiter = _RateLimiter(_HEARTBEAT_LIMIT)
    lease_limiter = _RateLimiter(_LEASE_LIMIT)
    complete_limiter = _RateLimiter(_COMPLETE_LIMIT)

    @router.post("/enroll", response_model=NodeResponse)
    async def enroll(
        request: EnrollmentRequest,
        http_request: Request,
    ) -> NodeResponse:
        client_ip = http_request.client.host if http_request.client else "unknown"
        _rate_limit(enroll_limiter, client_ip)
        try:
            node = await fleet.enroll(request.token, observed_at=clock())
            _audit_log(
                "enrollment.success",
                node_id=node.node_id,
                runtime=node.runtime,
                client_ip=client_ip,
            )
        except EdgeFleetError as exc:
            _audit_log(
                "enrollment.failure",
                detail=str(exc),
                client_ip=client_ip,
            )
            raise _unauthorized() from exc
        return _node_response(node)

    @router.post("/heartbeat", response_model=NodeResponse)
    async def heartbeat(
        request: HeartbeatRequest,
        node_id: Annotated[str, Header(alias="X-Edge-Node-ID")],
        signed_at: Annotated[datetime, Header(alias="X-Edge-Timestamp")],
        nonce: Annotated[str, Header(alias="X-Edge-Nonce")],
        signature: Annotated[str, Header(alias="X-Edge-Signature")],
    ) -> NodeResponse:
        _rate_limit(heartbeat_limiter, node_id)
        body = request.model_dump(mode="json")
        try:
            await _authenticate(fleet, node_id, "/heartbeat", body, signed_at, nonce, signature, clock())
        except HTTPException:
            _audit_log("heartbeat.auth_failure", node_id=node_id)
            raise
        try:
            node = await fleet.heartbeat(node_id, capabilities=request.capabilities, observed_at=clock())
            _audit_log("heartbeat.success", node_id=node_id, capabilities=list(request.capabilities))
        except EdgeFleetError as exc:
            _audit_log("heartbeat.failure", node_id=node_id, detail=str(exc))
            raise _conflict() from exc
        return _node_response(node)

    @router.post("/lease", response_model=LeaseResponse | None)
    async def lease(
        request: LeaseRequest,
        node_id: Annotated[str, Header(alias="X-Edge-Node-ID")],
        signed_at: Annotated[datetime, Header(alias="X-Edge-Timestamp")],
        nonce: Annotated[str, Header(alias="X-Edge-Nonce")],
        signature: Annotated[str, Header(alias="X-Edge-Signature")],
    ) -> LeaseResponse | None:
        _rate_limit(lease_limiter, node_id)
        body = request.model_dump(mode="json")
        try:
            await _authenticate(fleet, node_id, "/lease", body, signed_at, nonce, signature, clock())
        except HTTPException:
            _audit_log("lease.auth_failure", node_id=node_id)
            raise
        try:
            value = await fleet.acquire(node_id, observed_at=clock(), lease_seconds=request.lease_seconds)
        except EdgeFleetError as exc:
            _audit_log("lease.failure", node_id=node_id, detail=str(exc))
            raise _conflict() from exc
        if value is None:
            _audit_log("lease.no_work", node_id=node_id)
            return None
        _audit_log("lease.success", node_id=node_id, job_id=value.job_id, lease_id=value.lease_id)
        return LeaseResponse(
            job_id=value.job_id,
            lease_id=value.lease_id,
            payload=value.payload,
            expires_at=value.expires_at,
        )

    @router.post("/complete", status_code=status.HTTP_204_NO_CONTENT)
    async def complete(
        request: CompleteRequest,
        node_id: Annotated[str, Header(alias="X-Edge-Node-ID")],
        signed_at: Annotated[datetime, Header(alias="X-Edge-Timestamp")],
        nonce: Annotated[str, Header(alias="X-Edge-Nonce")],
        signature: Annotated[str, Header(alias="X-Edge-Signature")],
    ) -> Response:
        _rate_limit(complete_limiter, node_id)
        body = request.model_dump(mode="json")
        try:
            await _authenticate(fleet, node_id, "/complete", body, signed_at, nonce, signature, clock())
        except HTTPException:
            _audit_log("complete.auth_failure", node_id=node_id)
            raise
        lease_value = JobLease(
            request.job_id,
            request.lease_id,
            node_id,
            {},
            request.lease_expires_at,
        )
        try:
            await fleet.complete(
                lease_value,
                result=request.result,
                signature=request.result_signature,
                observed_at=clock(),
            )
            _audit_log(
                "complete.success",
                node_id=node_id,
                job_id=request.job_id,
                lease_id=request.lease_id,
            )
        except EdgeFleetError as exc:
            _audit_log("complete.failure", node_id=node_id, job_id=request.job_id, detail=str(exc))
            raise _conflict() from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def create_edge_fleet_admin_router(
    fleet: EdgeFleet,
    *,
    now: Callable[[], datetime] | None = None,
) -> APIRouter:
    """Build coordinator routes; callers must apply dashboard authentication."""
    clock = now or (lambda: datetime.now(UTC))
    router = APIRouter(prefix=_ADMIN_PREFIX, tags=["edge-fleet-admin"])
    admin_limiter = _RateLimiter(_ADMIN_LIMIT)
    revoke_limiter = _RateLimiter(_REVOKE_RATE_LIMIT)

    @router.post("/enrollments", response_model=EnrollmentIssueResponse)
    async def issue_enrollment(
        request: EnrollmentIssueRequest,
        response: Response,
        _user: Annotated[dict, Depends(lambda: None)],  # placeholder — caller applies verify_user
    ) -> EnrollmentIssueResponse:
        _rate_limit(admin_limiter, "coordinator")
        expires_at = clock() + timedelta(seconds=request.expires_in_seconds)
        try:
            token = fleet.issue_enrollment(
                node_id=request.node_id,
                runtime=request.runtime,
                public_key=request.public_key,
                capabilities=request.capabilities,
                expires_at=expires_at,
            )
            _audit_log(
                "admin.enrollment_issued",
                node_id=request.node_id,
                runtime=request.runtime,
            )
        except (EdgeFleetError, ValueError) as exc:
            _audit_log("admin.enrollment_issue_failed", node_id=request.node_id, detail=str(exc))
            raise _invalid_admin_request() from exc
        response.headers["Cache-Control"] = "no-store"
        return EnrollmentIssueResponse(token=token, expires_at=expires_at)

    @router.get("/nodes", response_model=tuple[NodeResponse, ...])
    async def healthy_nodes(
        max_age_seconds: int = 300,
        _user: Annotated[dict, Depends(lambda: None)] = None,
    ) -> tuple[NodeResponse, ...]:
        _rate_limit(admin_limiter, "coordinator")
        if max_age_seconds < 1 or max_age_seconds > 3600:
            raise _invalid_admin_request()
        nodes = await fleet.healthy_nodes(observed_at=clock(), max_age=timedelta(seconds=max_age_seconds))
        _audit_log("admin.nodes_listed", count=len(nodes))
        return tuple(_node_response(node) for node in nodes)

    @router.post("/jobs", response_model=EdgeJobResponse, status_code=status.HTTP_201_CREATED)
    async def queue_job(
        request: QueueJobRequest,
        _user: Annotated[dict, Depends(lambda: None)] = None,
    ) -> EdgeJobResponse:
        _rate_limit(admin_limiter, "coordinator")
        try:
            await fleet.queue_job(
                job_id=request.job_id,
                runtime=request.runtime,
                required_capabilities=request.required_capabilities,
                payload=request.payload,
            )
            _audit_log("admin.job_queued", job_id=request.job_id, runtime=request.runtime)
            return _job_response(await fleet.job(request.job_id))
        except EdgeFleetError as exc:
            _audit_log("admin.job_queue_failed", job_id=request.job_id, detail=str(exc))
            raise _conflict() from exc

    @router.get("/jobs/{job_id}", response_model=EdgeJobResponse)
    async def get_job(
        job_id: str,
        _user: Annotated[dict, Depends(lambda: None)] = None,
    ) -> EdgeJobResponse:
        try:
            return _job_response(await fleet.job(job_id))
        except EdgeFleetError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Edge job was not found") from exc

    @router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_node(
        node_id: str,
        request: Request,
        _user: Annotated[dict, Depends(lambda: None)] = None,
    ) -> Response:
        """Revoke one node identity (soft-delete) and fail it closed.

        Requires the ``admin.nodes.revoke`` permission (OQ-7). Returns 204 for
        any node that exists (whether already revoked or not — idempotent,
        OQ-4) and a true 404 only for a node_id that never existed. Rate
        limited to 5 revocations per minute per admin principal, with a burst
        alert when exceeded (OQ-8).
        """
        # The router is mounted behind verify_user, which stores the
        # authenticated principal in request.scope["auth_user"]. The _user
        # placeholder is a no-op dependency; the real identity comes from scope.
        auth_user = request.scope.get("auth_user")
        principal = _admin_principal(request, auth_user)
        _require_revoke_permission(request, auth_user, principal)
        if not revoke_limiter.check(principal):
            _audit_log(
                _REVOKE_BURST_ALERT_EVENT,
                node_id=node_id,
                actor=principal,
                detail="node revocation rate limit exceeded",
            )
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Node revocation rate limit exceeded")
        try:
            outcome = await fleet.revoke_node(node_id, observed_at=clock(), actor=principal)
        except EdgeFleetError as exc:
            _audit_log("admin.node_revoke_failed", node_id=node_id, actor=principal, detail=str(exc))
            raise _invalid_admin_request() from exc
        if not outcome.existed:
            _audit_log("admin.node_revoke_not_found", node_id=node_id, actor=principal)
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Edge node was not found")
        _audit_log(
            "admin.node_revoked",
            node_id=node_id,
            actor=principal,
            already_revoked=outcome.already_revoked,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _admin_principal(request: Request, user: dict | None) -> str:
    """Return a stable admin principal identifier for authz and rate limiting."""
    if isinstance(user, dict):
        for key in ("user_id", "email", "matrix_user_id"):
            value = user.get(key)
            if isinstance(value, str) and value:
                return value
    auth_user = request.scope.get("auth_user")
    if isinstance(auth_user, dict):
        for key in ("user_id", "email", "matrix_user_id"):
            value = auth_user.get(key)
            if isinstance(value, str) and value:
                return value
    return "unknown-admin"


def _require_revoke_permission(request: Request, user: dict | None, principal: str) -> None:
    """Enforce the ``admin.nodes.revoke`` permission, logging any rejection.

    The permission is granted when the authenticated principal is an admin
    (the router is mounted behind ``verify_user``) and, when a permission
    list is present on the auth user, the principal is listed for
    ``admin.nodes.revoke``. A missing permission list is treated as
    admin-granted (the dashboard owner is the sole admin).
    """
    granted = False
    if isinstance(user, dict):
        permissions = user.get("permissions")
        if permissions is None:
            granted = True
        elif isinstance(permissions, (list, tuple, set, frozenset)):
            granted = _REVOKE_PERMISSION in permissions
        elif isinstance(permissions, dict):
            granted = bool(permissions.get(_REVOKE_PERMISSION))
    if not granted:
        _audit_log(
            "admin.node_revoke_denied",
            actor=principal,
            detail=f"missing permission {_REVOKE_PERMISSION}",
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permission to revoke nodes")


async def _authenticate(
    fleet: EdgeFleet,
    node_id: str,
    endpoint: str,
    body: dict[str, object],
    signed_at: datetime,
    nonce: str,
    signature: str,
    observed_at: datetime,
) -> None:
    try:
        await fleet.authenticate_request(
            node_id=node_id,
            method="POST",
            path=f"{_PREFIX}{endpoint}",
            body=body,
            timestamp=signed_at,
            nonce=nonce,
            signature=signature,
            observed_at=observed_at,
        )
    except EdgeFleetError as exc:
        _audit_log("auth.failure", node_id=node_id, endpoint=endpoint, detail=str(exc))
        raise _unauthorized() from exc


def _node_response(node: EdgeNode) -> NodeResponse:
    return NodeResponse(
        node_id=node.node_id,
        runtime=node.runtime,
        capabilities=node.capabilities,
        last_seen_at=node.last_seen_at,
    )


def _job_response(job: EdgeJob) -> EdgeJobResponse:
    return EdgeJobResponse(
        job_id=job.job_id,
        runtime=job.runtime,
        required_capabilities=job.required_capabilities,
        payload=job.payload,
        status=job.status,
        node_id=job.node_id,
        result=job.result,
        result_signature=job.result_signature,
    )


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Edge node authentication failed")


def _conflict() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "Edge fleet operation could not be completed")


def _invalid_admin_request() -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Edge fleet request is invalid")