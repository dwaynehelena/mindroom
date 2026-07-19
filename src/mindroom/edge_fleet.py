"""Secure enrollment, leases, offline queues and result attestation for edge workers."""

# Transaction bodies validate before their shared rollback handler.
# ruff: noqa: TRY301

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast

import aiosqlite
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

if TYPE_CHECKING:
    from pathlib import Path

WorkerRuntime = Literal["openclaw", "hermes"]


class EdgeFleetError(RuntimeError):
    """An enrollment, lease, capability, health, or attestation invariant failed."""


@dataclass(frozen=True, slots=True)
class EdgeNode:
    """One enrolled public-key identity and its advertised capabilities."""

    node_id: str
    runtime: WorkerRuntime
    public_key: str
    capabilities: tuple[str, ...]
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class JobLease:
    """One bounded exclusive job lease issued to an eligible node."""

    job_id: str
    lease_id: str
    node_id: str
    payload: dict[str, object]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class EdgeJob:
    """Content and attestation state for one coordinator-owned job."""

    job_id: str
    runtime: WorkerRuntime
    required_capabilities: tuple[str, ...]
    payload: dict[str, object]
    status: Literal["queued", "leased", "completed"]
    node_id: str | None
    result: dict[str, object] | None
    result_signature: str | None


class EnrollmentAuthority:
    """Issue and verify short-lived coordinator-authenticated enrollment tokens."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            message = "edge enrollment key must contain at least 32 bytes"
            raise ValueError(message)
        self._key = key

    def issue(
        self,
        *,
        node_id: str,
        runtime: WorkerRuntime,
        public_key: str,
        capabilities: tuple[str, ...],
        expires_at: datetime,
    ) -> str:
        """Issue one signed enrollment claim; replay prevention is persisted by the fleet."""
        claim = {
            "capabilities": list(dict.fromkeys(capabilities)),
            "expires_at": _utc(expires_at).isoformat(),
            "node_id": node_id,
            "nonce": secrets.token_hex(16),
            "public_key": public_key,
            "runtime": runtime,
            "schema": "mindroom.edge-enrollment/1",
        }
        payload = _json(claim)
        signature = hmac.new(self._key, payload, hashlib.sha256).digest()
        return f"{_b64(payload)}.{_b64(signature)}"

    def verify(self, token: str, *, observed_at: datetime) -> dict[str, object]:
        """Verify signature, schema, expiry and strict claim shape."""
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
            payload = _unb64(encoded_payload)
            signature = _unb64(encoded_signature)
            claim = json.loads(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            message = "edge enrollment token is malformed"
            raise EdgeFleetError(message) from exc
        expected = hmac.new(self._key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            message = "edge enrollment signature is invalid"
            raise EdgeFleetError(message)
        if not isinstance(claim, dict) or set(claim) != {
            "capabilities",
            "expires_at",
            "node_id",
            "nonce",
            "public_key",
            "runtime",
            "schema",
        }:
            message = "edge enrollment claim shape is invalid"
            raise EdgeFleetError(message)
        if claim["schema"] != "mindroom.edge-enrollment/1" or claim["runtime"] not in {"openclaw", "hermes"}:
            message = "edge enrollment schema or runtime is invalid"
            raise EdgeFleetError(message)
        if _utc(observed_at) > datetime.fromisoformat(str(claim["expires_at"])):
            message = "edge enrollment token has expired"
            raise EdgeFleetError(message)
        _public_key(str(claim["public_key"]))
        capabilities = claim["capabilities"]
        if not isinstance(capabilities, list) or any(not isinstance(value, str) or not value for value in capabilities):
            message = "edge enrollment capabilities are invalid"
            raise EdgeFleetError(message)
        return claim


class EdgeFleet:
    """Persistent node inventory, job queue, leases and result attestations."""

    def __init__(self, path: Path, authority: EnrollmentAuthority) -> None:
        self._path = path
        self._authority = authority
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the fleet database."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS edge_enrollment_nonce (
              nonce TEXT PRIMARY KEY,
              used_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS edge_node (
              node_id TEXT PRIMARY KEY,
              runtime TEXT NOT NULL CHECK(runtime IN ('openclaw','hermes')),
              public_key TEXT NOT NULL,
              capabilities_json TEXT NOT NULL,
              last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS edge_request_nonce (
              node_id TEXT NOT NULL,
              nonce TEXT NOT NULL,
              used_at TEXT NOT NULL,
              PRIMARY KEY(node_id,nonce)
            );
            CREATE TABLE IF NOT EXISTS edge_job (
              job_id TEXT PRIMARY KEY,
              runtime TEXT NOT NULL CHECK(runtime IN ('openclaw','hermes')),
              required_capabilities_json TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('queued','leased','completed')),
              node_id TEXT,
              lease_id TEXT,
              lease_expires_at TEXT,
              result_json TEXT,
              result_signature TEXT
            );
            """,
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the fleet database."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    def issue_enrollment(
        self,
        *,
        node_id: str,
        runtime: WorkerRuntime,
        public_key: str,
        capabilities: tuple[str, ...],
        expires_at: datetime,
    ) -> str:
        """Issue one bounded enrollment token through the configured authority."""
        if not node_id or not capabilities or any(not value for value in capabilities):
            message = "edge enrollment identity and capabilities are required"
            raise EdgeFleetError(message)
        _public_key(public_key)
        return self._authority.issue(
            node_id=node_id,
            runtime=runtime,
            public_key=public_key,
            capabilities=capabilities,
            expires_at=expires_at,
        )

    async def enroll(self, token: str, *, observed_at: datetime) -> EdgeNode:
        """Consume one enrollment token and persist its exact node identity."""
        observed = _utc(observed_at)
        claim = self._authority.verify(token, observed_at=observed)
        node = EdgeNode(
            node_id=str(claim["node_id"]),
            runtime=cast("WorkerRuntime", claim["runtime"]),
            public_key=str(claim["public_key"]),
            capabilities=tuple(cast("list[str]", claim["capabilities"])),
            last_seen_at=observed,
        )
        if not node.node_id:
            message = "edge node identity is blank"
            raise EdgeFleetError(message)
        async with self._lock:
            db = self._required_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                nonce = str(claim["nonce"])
                cursor = await db.execute(
                    "INSERT OR IGNORE INTO edge_enrollment_nonce VALUES(?,?)",
                    (nonce, observed.isoformat()),
                )
                if cursor.rowcount != 1:
                    message = "edge enrollment token was already consumed"
                    raise EdgeFleetError(message)
                existing = await (
                    await db.execute(
                        "SELECT runtime,public_key,capabilities_json FROM edge_node WHERE node_id=?",
                        (node.node_id,),
                    )
                ).fetchone()
                identity = (node.runtime, node.public_key, _json_text(list(node.capabilities)))
                if existing is None:
                    await db.execute(
                        "INSERT INTO edge_node VALUES(?,?,?,?,?)",
                        (node.node_id, *identity, observed.isoformat()),
                    )
                elif tuple(existing) != identity:
                    message = "edge node identity equivocation denied"
                    raise EdgeFleetError(message)
                else:
                    await db.execute(
                        "UPDATE edge_node SET last_seen_at=? WHERE node_id=?",
                        (observed.isoformat(), node.node_id),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return node

    async def heartbeat(
        self,
        node_id: str,
        *,
        capabilities: tuple[str, ...],
        observed_at: datetime,
    ) -> EdgeNode:
        """Refresh health and capability discovery for an enrolled identity."""
        observed = _utc(observed_at)
        row = await (
            await self._required_db().execute(
                "SELECT runtime,public_key FROM edge_node WHERE node_id=?",
                (node_id,),
            )
        ).fetchone()
        if row is None:
            message = "edge node is not enrolled"
            raise EdgeFleetError(message)
        normalized = tuple(dict.fromkeys(capabilities))
        await self._required_db().execute(
            "UPDATE edge_node SET capabilities_json=?,last_seen_at=? WHERE node_id=?",
            (_json_text(list(normalized)), observed.isoformat(), node_id),
        )
        await self._required_db().commit()
        return EdgeNode(node_id, row[0], row[1], normalized, observed)

    async def authenticate_request(
        self,
        *,
        node_id: str,
        method: str,
        path: str,
        body: dict[str, object],
        timestamp: datetime,
        nonce: str,
        signature: str,
        observed_at: datetime,
        max_clock_skew: timedelta = timedelta(minutes=5),
    ) -> None:
        """Authenticate one node API request and consume its nonce exactly once."""
        observed = _utc(observed_at)
        signed_at = _utc(timestamp)
        if max_clock_skew <= timedelta(0) or max_clock_skew > timedelta(hours=1):
            message = "edge request clock-skew policy is invalid"
            raise EdgeFleetError(message)
        if abs(observed - signed_at) > max_clock_skew:
            message = "edge request timestamp is outside the accepted clock skew"
            raise EdgeFleetError(message)
        if (
            not node_id
            or not nonce
            or len(nonce.encode()) > 256
            or method.upper() not in {"POST", "PUT"}
            or not path.startswith("/api/edge-fleet/")
        ):
            message = "edge request identity, method, path, or nonce is invalid"
            raise EdgeFleetError(message)
        row = await (
            await self._required_db().execute("SELECT public_key FROM edge_node WHERE node_id=?", (node_id,))
        ).fetchone()
        if row is None:
            message = "edge node is not enrolled"
            raise EdgeFleetError(message)
        payload = node_request_attestation_payload(
            node_id=node_id,
            method=method,
            path=path,
            body=body,
            timestamp=signed_at,
            nonce=nonce,
        )
        try:
            _public_key(str(row[0])).verify(_unb64(signature), payload)
        except (InvalidSignature, ValueError) as exc:
            message = "edge request attestation is invalid"
            raise EdgeFleetError(message) from exc
        cursor = await self._required_db().execute(
            "INSERT OR IGNORE INTO edge_request_nonce VALUES(?,?,?)",
            (node_id, nonce, observed.isoformat()),
        )
        if cursor.rowcount != 1:
            message = "edge request nonce was already consumed"
            raise EdgeFleetError(message)
        await self._required_db().commit()

    async def healthy_nodes(self, *, observed_at: datetime, max_age: timedelta) -> tuple[EdgeNode, ...]:
        """Return nodes with fresh heartbeats."""
        cutoff = _utc(observed_at) - max_age
        rows = await (
            await self._required_db().execute(
                "SELECT node_id,runtime,public_key,capabilities_json,last_seen_at FROM edge_node "
                "WHERE last_seen_at>=? ORDER BY node_id",
                (cutoff.isoformat(),),
            )
        ).fetchall()
        return tuple(_node(tuple(row)) for row in rows)

    async def job(self, job_id: str) -> EdgeJob:
        """Return one exact coordinator job and its attested result state."""
        row = await (
            await self._required_db().execute(
                "SELECT job_id,runtime,required_capabilities_json,payload_json,status,node_id,"
                "result_json,result_signature FROM edge_job WHERE job_id=?",
                (job_id,),
            )
        ).fetchone()
        if row is None:
            message = "edge job not found"
            raise EdgeFleetError(message)
        return EdgeJob(
            job_id=str(row[0]),
            runtime=cast("WorkerRuntime", row[1]),
            required_capabilities=tuple(json.loads(str(row[2]))),
            payload=cast("dict[str, object]", json.loads(str(row[3]))),
            status=cast('Literal["queued", "leased", "completed"]', row[4]),
            node_id=str(row[5]) if row[5] is not None else None,
            result=cast("dict[str, object]", json.loads(str(row[6]))) if row[6] is not None else None,
            result_signature=str(row[7]) if row[7] is not None else None,
        )

    async def queue_job(
        self,
        *,
        job_id: str,
        runtime: WorkerRuntime,
        required_capabilities: tuple[str, ...],
        payload: dict[str, object],
    ) -> None:
        """Persist a job even when every eligible node is offline."""
        normalized_capabilities = tuple(dict.fromkeys(required_capabilities))
        try:
            payload_json = _json_text(payload)
        except (TypeError, ValueError) as exc:
            message = "edge job payload must be a JSON object"
            raise EdgeFleetError(message) from exc
        if (
            not job_id
            or len(job_id.encode()) > 256
            or not isinstance(payload, dict)
            or len(payload_json.encode()) > 1_048_576
            or len(normalized_capabilities) > 256
            or any(not value or len(value.encode()) > 256 for value in normalized_capabilities)
        ):
            message = "edge job identity, capabilities, or payload is invalid"
            raise EdgeFleetError(message)
        values = (
            job_id,
            runtime,
            _json_text(list(normalized_capabilities)),
            payload_json,
        )
        cursor = await self._required_db().execute(
            "INSERT OR IGNORE INTO edge_job VALUES(?,?,?,?,'queued',NULL,NULL,NULL,NULL,NULL)",
            values,
        )
        if cursor.rowcount != 1:
            row = await (
                await self._required_db().execute(
                    "SELECT job_id,runtime,required_capabilities_json,payload_json FROM edge_job WHERE job_id=?",
                    (job_id,),
                )
            ).fetchone()
            if tuple(row or ()) != values:
                message = "edge job identity equivocation denied"
                raise EdgeFleetError(message)
        await self._required_db().commit()

    async def acquire(
        self,
        node_id: str,
        *,
        observed_at: datetime,
        lease_seconds: int = 60,
    ) -> JobLease | None:
        """Requeue expired work and lease the oldest compatible job exclusively."""
        observed = _utc(observed_at)
        if lease_seconds < 1 or lease_seconds > 3600:
            message = "edge lease duration must be between 1 and 3600 seconds"
            raise EdgeFleetError(message)
        async with self._lock:
            db = self._required_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    "UPDATE edge_job SET status='queued',node_id=NULL,lease_id=NULL,lease_expires_at=NULL "
                    "WHERE status='leased' AND lease_expires_at<?",
                    (observed.isoformat(),),
                )
                node_row = await (
                    await db.execute(
                        "SELECT runtime,capabilities_json FROM edge_node WHERE node_id=?",
                        (node_id,),
                    )
                ).fetchone()
                if node_row is None:
                    message = "edge node is not enrolled"
                    raise EdgeFleetError(message)
                capabilities = set(json.loads(node_row[1]))
                rows = await (
                    await db.execute(
                        "SELECT job_id,required_capabilities_json,payload_json FROM edge_job "
                        "WHERE status='queued' AND runtime=? ORDER BY rowid",
                        (node_row[0],),
                    )
                ).fetchall()
                selected = next((row for row in rows if set(json.loads(row[1])) <= capabilities), None)
                if selected is None:
                    await db.commit()
                    return None
                lease_id = secrets.token_hex(16)
                expires = observed + timedelta(seconds=lease_seconds)
                cursor = await db.execute(
                    "UPDATE edge_job SET status='leased',node_id=?,lease_id=?,lease_expires_at=? "
                    "WHERE job_id=? AND status='queued'",
                    (node_id, lease_id, expires.isoformat(), selected[0]),
                )
                if cursor.rowcount != 1:
                    message = "edge job lease raced with another worker"
                    raise EdgeFleetError(message)
                await db.commit()
                return JobLease(selected[0], lease_id, node_id, json.loads(selected[2]), expires)
            except BaseException:
                await db.rollback()
                raise

    async def complete(
        self,
        lease: JobLease,
        *,
        result: dict[str, object],
        signature: str,
        observed_at: datetime,
    ) -> None:
        """Verify the node's Ed25519 result attestation before completing the lease."""
        observed = _utc(observed_at)
        row = await (
            await self._required_db().execute(
                "SELECT public_key FROM edge_node WHERE node_id=?",
                (lease.node_id,),
            )
        ).fetchone()
        if row is None or observed > lease.expires_at:
            message = "edge result identity or lease expiry is invalid"
            raise EdgeFleetError(message)
        result_json = _json(result)
        attestation = _result_attestation(lease.job_id, lease.lease_id, result_json)
        try:
            _public_key(row[0]).verify(_unb64(signature), attestation)
        except (InvalidSignature, ValueError) as exc:
            message = "edge result attestation is invalid"
            raise EdgeFleetError(message) from exc
        cursor = await self._required_db().execute(
            "UPDATE edge_job SET status='completed',result_json=?,result_signature=? "
            "WHERE job_id=? AND status='leased' AND node_id=? AND lease_id=? AND lease_expires_at>=?",
            (result_json.decode(), signature, lease.job_id, lease.node_id, lease.lease_id, observed.isoformat()),
        )
        if cursor.rowcount != 1:
            message = "edge result does not match an active lease"
            raise EdgeFleetError(message)
        await self._required_db().commit()

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "edge fleet is not open"
            raise RuntimeError(message)
        return self._db


def result_attestation_payload(job_id: str, lease_id: str, result: dict[str, object]) -> bytes:
    """Return the exact bytes a node must sign for one leased result."""
    return _result_attestation(job_id, lease_id, _json(result))


def node_request_attestation_payload(
    *,
    node_id: str,
    method: str,
    path: str,
    body: dict[str, object],
    timestamp: datetime,
    nonce: str,
) -> bytes:
    """Return the exact canonical bytes a node signs for one API request."""
    return _json(
        {
            "body_digest": hashlib.sha256(_json(body)).hexdigest(),
            "method": method.upper(),
            "node_id": node_id,
            "nonce": nonce,
            "path": path,
            "schema": "mindroom.edge-request/1",
            "timestamp": _utc(timestamp).isoformat(),
        },
    )


def _result_attestation(job_id: str, lease_id: str, result_json: bytes) -> bytes:
    return _json(
        {
            "job_id": job_id,
            "lease_id": lease_id,
            "result_digest": hashlib.sha256(result_json).hexdigest(),
            "schema": "mindroom.edge-result/1",
        },
    )


def _node(row: tuple[object, ...]) -> EdgeNode:
    return EdgeNode(
        str(row[0]),
        cast("WorkerRuntime", row[1]),
        str(row[2]),
        tuple(json.loads(str(row[3]))),
        datetime.fromisoformat(str(row[4])),
    )


def _public_key(value: str) -> Ed25519PublicKey:
    try:
        raw = _unb64(value)
        if len(raw) != 32:
            message = "Ed25519 public keys must contain 32 bytes"
            raise ValueError(message)
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        message = "edge Ed25519 public key is invalid"
        raise EdgeFleetError(message) from exc


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


def _json_text(value: object) -> str:
    return _json(value).decode()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    if not value or "=" in value:
        message = "edge Base64URL value is non-canonical"
        raise ValueError(message)
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _b64(decoded) != value:
        message = "edge Base64URL value is non-canonical"
        raise ValueError(message)
    return decoded


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "edge fleet timestamps must include a timezone"
        raise EdgeFleetError(message)
    return value.astimezone(UTC)
