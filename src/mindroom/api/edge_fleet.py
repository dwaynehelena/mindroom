"""Authenticated network boundary for OpenClaw and Hermes edge workers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from mindroom.edge_fleet import EdgeFleet, EdgeFleetError, EdgeJob, EdgeNode, JobLease, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Callable

_PREFIX = "/api/edge-fleet"
_ADMIN_PREFIX = "/api/edge-fleet-admin"


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


def create_edge_fleet_router(
    fleet: EdgeFleet,
    *,
    now: Callable[[], datetime] | None = None,
) -> APIRouter:
    """Build an isolated router around one lifecycle-owned fleet instance."""
    clock = now or (lambda: datetime.now(UTC))
    router = APIRouter(prefix=_PREFIX, tags=["edge-fleet"])

    @router.post("/enroll", response_model=NodeResponse)
    async def enroll(request: EnrollmentRequest) -> NodeResponse:
        try:
            node = await fleet.enroll(request.token, observed_at=clock())
        except EdgeFleetError as exc:
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
        body = request.model_dump(mode="json")
        await _authenticate(fleet, node_id, "/heartbeat", body, signed_at, nonce, signature, clock())
        try:
            node = await fleet.heartbeat(node_id, capabilities=request.capabilities, observed_at=clock())
        except EdgeFleetError as exc:
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
        body = request.model_dump(mode="json")
        await _authenticate(fleet, node_id, "/lease", body, signed_at, nonce, signature, clock())
        try:
            value = await fleet.acquire(node_id, observed_at=clock(), lease_seconds=request.lease_seconds)
        except EdgeFleetError as exc:
            raise _conflict() from exc
        if value is None:
            return None
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
        body = request.model_dump(mode="json")
        await _authenticate(fleet, node_id, "/complete", body, signed_at, nonce, signature, clock())
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
        except EdgeFleetError as exc:
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

    @router.post("/enrollments", response_model=EnrollmentIssueResponse)
    async def issue_enrollment(request: EnrollmentIssueRequest, response: Response) -> EnrollmentIssueResponse:
        expires_at = clock() + timedelta(seconds=request.expires_in_seconds)
        try:
            token = fleet.issue_enrollment(
                node_id=request.node_id,
                runtime=request.runtime,
                public_key=request.public_key,
                capabilities=request.capabilities,
                expires_at=expires_at,
            )
        except (EdgeFleetError, ValueError) as exc:
            raise _invalid_admin_request() from exc
        response.headers["Cache-Control"] = "no-store"
        return EnrollmentIssueResponse(token=token, expires_at=expires_at)

    @router.get("/nodes", response_model=tuple[NodeResponse, ...])
    async def healthy_nodes(max_age_seconds: int = 300) -> tuple[NodeResponse, ...]:
        if max_age_seconds < 1 or max_age_seconds > 3600:
            raise _invalid_admin_request()
        nodes = await fleet.healthy_nodes(observed_at=clock(), max_age=timedelta(seconds=max_age_seconds))
        return tuple(_node_response(node) for node in nodes)

    @router.post("/jobs", response_model=EdgeJobResponse, status_code=status.HTTP_201_CREATED)
    async def queue_job(request: QueueJobRequest) -> EdgeJobResponse:
        try:
            await fleet.queue_job(
                job_id=request.job_id,
                runtime=request.runtime,
                required_capabilities=request.required_capabilities,
                payload=request.payload,
            )
            return _job_response(await fleet.job(request.job_id))
        except EdgeFleetError as exc:
            raise _conflict() from exc

    @router.get("/jobs/{job_id}", response_model=EdgeJobResponse)
    async def get_job(job_id: str) -> EdgeJobResponse:
        try:
            return _job_response(await fleet.job(job_id))
        except EdgeFleetError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Edge job was not found") from exc

    return router


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
