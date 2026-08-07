"""Phase A — stable worker identity + enrollment for the Agent Mesh Gateway.

This module is the fully additive, local-only Phase A of mesh worker
enrollment.  It provides:

- ``MeshWorkerIdentity`` — a stable per-worker identity (``worker_id``,
  ``agent_name``, ``public_key``, ``runtime``, ``capabilities``) persisted in
  a mode-0600 JSON file, mirroring the shape of
  ``mindroom.edge_node.EdgeNodeIdentity`` (schema ``mindroom.mesh-worker/1``).
  The same identity file, reused across restarts, yields the same
  ``worker_id`` so a worker is re-admitted rather than duplicated.

- ``MeshEnrollmentAuthority`` — issues/verifies short-lived HMAC-signed
  enrollment claims.  It reuses the HMAC-SHA256 scheme and helpers from
  ``mindroom.edge_fleet.EnrollmentAuthority`` (thin mesh wrapper).

- ``MeshEnrollmentRegistry`` — a durable worker inventory (``worker_id <-> room``
  binding, capabilities, ``last_seen``) in a synchronous SQLite store mirroring
  the ``mindroom.edge_fleet.EdgeFleet`` table pattern.

- ``MeshEnrollmentCoordinator`` — orchestrates the identity, authority and
  registry to admit/re-admit workers, and gates the external OpenClaw
  handshake behind an explicit human approval (Phase B).

PHASE B GATE
------------
The real OpenClaw gateway enrollment *handshake* is an **external side effect**
(a network round-trip to the OpenClaw gateway authority).  It is NOT performed
here.  ``MeshEnrollmentCoordinator`` exposes ``handshake`` and
``handshake_enabled``; ``handshake`` defaults to ``None`` and
``handshake_enabled`` defaults to ``False``, so no network call is ever made
unless an operator explicitly flips both.  See
``docs/mesh_enrollment_phase_b_gate.md`` for the approval checklist that must
be satisfied before enabling Phase B.
"""

# Ruff: TRY301 — identity parsing validates inside one shared sanitized-error boundary.
# ruff: noqa: TRY301

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mindroom.edge_fleet import _b64, _json, _unb64, _utc

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = [
    "PHASE_B_HANDSHAKE_ENABLED",
    "MeshEnrollmentAuthority",
    "MeshEnrollmentCoordinator",
    "MeshEnrollmentError",
    "MeshEnrollmentRegistry",
    "MeshEnrollmentResult",
    "MeshWorkerIdentity",
    "MeshWorkerRuntime",
]

MeshWorkerRuntime = Literal["openclaw"]

#: Persistent identity file schema (mesh sibling of ``mindroom.edge-node/1``).
MESH_WORKER_SCHEMA = "mindroom.mesh-worker/1"
#: Short-lived enrollment claim schema (mesh sibling of ``mindroom.edge-enrollment/1``).
MESH_ENROLLMENT_SCHEMA = "mindroom.mesh-enrollment/1"

#: Enrollment flag.  When OFF (default) enrollment is fully inert and the
#: gateway keeps its static registration behavior.  Only append behind this
#: flag; never change the default-path behavior.
MESH_ENROLLMENT_ENV = "MINDROOM_MESH_ENROLLMENT"

#: Phase B handshake is hard-gated off.  No real OpenClaw gateway handshake
#: (a network call) may occur unless an operator explicitly enables it after
#: human review (see docs/mesh_enrollment_phase_b_gate.md).
PHASE_B_HANDSHAKE_ENABLED = False


class MeshEnrollmentError(RuntimeError):
    """An identity, enrollment, registry, or admission invariant failed."""


def enrollment_flag_enabled(env: dict[str, str] | None = None) -> bool:
    """Return whether mesh enrollment is enabled by env/flag (default OFF)."""
    source = env if env is not None else os.environ
    return (source.get(MESH_ENROLLMENT_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class MeshWorkerIdentity:
    """One stable private worker identity persisted in a mode-0600 JSON file.

    Mirrors ``EdgeNodeIdentity``: the private key is persisted and the public
    key is derived, so the same file across restarts yields the same
    ``worker_id`` and ``public_key``.
    """

    worker_id: str
    agent_name: str
    runtime: MeshWorkerRuntime
    capabilities: tuple[str, ...]
    private_key: Ed25519PrivateKey

    @classmethod
    def generate(
        cls,
        path: Path,
        *,
        worker_id: str,
        agent_name: str,
        runtime: MeshWorkerRuntime = "openclaw",
        capabilities: tuple[str, ...] = ("mesh.worker",),
    ) -> MeshWorkerIdentity:
        """Generate and exclusively persist a new mesh worker identity."""
        if (
            not worker_id
            or not agent_name
            or runtime != "openclaw"
            or any(not value for value in capabilities)
        ):
            message = "mesh worker identity, name, runtime, or capabilities are invalid"
            raise MeshEnrollmentError(message)
        if not path.is_absolute() or not path.parent.is_dir() or path.parent.is_symlink():
            message = "mesh worker identity path must use an existing absolute real directory"
            raise MeshEnrollmentError(message)
        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        payload = json.dumps(
            {
                "agent_name": agent_name,
                "capabilities": list(dict.fromkeys(capabilities)),
                "private_key": _b64(raw),
                "runtime": runtime,
                "schema": MESH_WORKER_SCHEMA,
                "worker_id": worker_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            message = "mesh worker identity file could not be created exclusively"
            raise MeshEnrollmentError(message) from exc
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return cls(worker_id, agent_name, runtime, tuple(dict.fromkeys(capabilities)), private_key)

    @classmethod
    def load(cls, path: Path) -> MeshWorkerIdentity:
        """Load an exact private identity only when filesystem permissions are safe."""
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                message = "mesh worker identity file must be regular and mode 0600"
                raise MeshEnrollmentError(message)
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict) or set(value) != {
                "agent_name",
                "capabilities",
                "private_key",
                "runtime",
                "schema",
                "worker_id",
            }:
                message = "mesh worker identity shape is invalid"
                raise MeshEnrollmentError(message)
            if (
                not isinstance(value["worker_id"], str)
                or not value["worker_id"]
                or not isinstance(value["agent_name"], str)
                or not value["agent_name"]
                or value["runtime"] != "openclaw"
                or not isinstance(value["capabilities"], list)
                or any(not isinstance(item, str) or not item for item in value["capabilities"])
            ):
                message = "mesh worker identity content is invalid"
                raise MeshEnrollmentError(message)
            raw = _unb64(value["private_key"])
            identity = cls(
                value["worker_id"],
                value["agent_name"],
                value["runtime"],
                tuple(value["capabilities"]),
                Ed25519PrivateKey.from_private_bytes(raw),
            )
        except MeshEnrollmentError:
            raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            message = "mesh worker identity could not be loaded"
            raise MeshEnrollmentError(message) from exc
        if value["schema"] != MESH_WORKER_SCHEMA or len(raw) != 32:
            message = "mesh worker identity content is invalid"
            raise MeshEnrollmentError(message)
        return identity

    @property
    def public_key(self) -> str:
        """Return the URL-safe raw Ed25519 public key for enrollment."""
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return _b64(raw)


class MeshEnrollmentAuthority:
    """Issue/verify short-lived HMAC-signed mesh enrollment claims.

    A thin mesh wrapper that reuses the same HMAC-SHA256 scheme and canonical
    JSON helpers as ``edge_fleet.EnrollmentAuthority`` but carries the mesh
    claim schema (including the worker ``agent_name``) so a reconnecting
    worker can be re-admitted with a stable ``worker_id``.  It deliberately
    does not subclass ``edge_fleet.EnrollmentAuthority`` because the mesh claim
    shape differs (``worker_id``/``agent_name`` vs ``node_id``), which would
    otherwise violate the base signature contract.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            message = "mesh enrollment key must contain at least 32 bytes"
            raise ValueError(message)
        self._key = key

    def issue(
        self,
        *,
        worker_id: str,
        agent_name: str,
        runtime: MeshWorkerRuntime,
        public_key: str,
        capabilities: tuple[str, ...],
        expires_at: datetime,
    ) -> str:
        """Issue one signed mesh enrollment claim; replay is persisted by the registry."""
        claim = {
            "agent_name": agent_name,
            "capabilities": list(dict.fromkeys(capabilities)),
            "expires_at": _utc(expires_at).isoformat(),
            "nonce": secrets.token_hex(16),
            "public_key": public_key,
            "runtime": runtime,
            "schema": MESH_ENROLLMENT_SCHEMA,
            "worker_id": worker_id,
        }
        payload = _json(claim)
        signature = hmac.new(self._key, payload, hashlib.sha256).digest()
        return f"{_b64(payload)}.{_b64(signature)}"

    def verify(self, token: str, *, observed_at: datetime) -> dict[str, object]:
        """Verify signature, schema, expiry and strict mesh claim shape."""
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
            payload = _unb64(encoded_payload)
            signature = _unb64(encoded_signature)
            claim = json.loads(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            message = "mesh enrollment token is malformed"
            raise MeshEnrollmentError(message) from exc
        expected = hmac.new(self._key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            message = "mesh enrollment signature is invalid"
            raise MeshEnrollmentError(message)
        if not isinstance(claim, dict) or set(claim) != {
            "agent_name",
            "capabilities",
            "expires_at",
            "nonce",
            "public_key",
            "runtime",
            "schema",
            "worker_id",
        }:
            message = "mesh enrollment claim shape is invalid"
            raise MeshEnrollmentError(message)
        if (
            claim["schema"] != MESH_ENROLLMENT_SCHEMA
            or claim["runtime"] != "openclaw"
            or not isinstance(claim["agent_name"], str)
            or not claim["agent_name"]
        ):
            message = "mesh enrollment schema or runtime is invalid"
            raise MeshEnrollmentError(message)
        if _utc(observed_at) > datetime.fromisoformat(str(claim["expires_at"])):
            message = "mesh enrollment token has expired"
            raise MeshEnrollmentError(message)
        capabilities = claim["capabilities"]
        if not isinstance(capabilities, list) or any(
            not isinstance(value, str) or not value for value in capabilities
        ):
            message = "mesh enrollment capabilities are invalid"
            raise MeshEnrollmentError(message)
        return claim


@dataclass(frozen=True, slots=True)
class MeshEnrolledWorker:
    """One enrolled mesh worker's durable inventory row."""

    worker_id: str
    agent_name: str
    public_key: str
    runtime: MeshWorkerRuntime
    capabilities: tuple[str, ...]
    room_id: str
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class MeshEnrollmentResult:
    """Result of one admission attempt."""

    status: Literal["enrolled", "reconnected", "rejected"]
    worker_id: str
    reason: str | None = None


class MeshEnrollmentRegistry:
    """Durable worker inventory (worker_id <-> room, capabilities, last_seen).

    Synchronous SQLite store mirroring the ``edge_fleet.EdgeFleet`` table
    pattern so it can be driven by the (synchronous) gateway admission path.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def open(self) -> None:
        """Open the registry database and create tables if needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS mesh_enrollment_nonce (
              nonce TEXT PRIMARY KEY,
              used_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mesh_worker (
              worker_id TEXT PRIMARY KEY,
              agent_name TEXT NOT NULL,
              public_key TEXT NOT NULL,
              runtime TEXT NOT NULL CHECK(runtime IN ('openclaw')),
              capabilities_json TEXT NOT NULL,
              room_id TEXT NOT NULL,
              last_seen_at TEXT NOT NULL
            );
            """,
        )
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        """Close the registry database."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def enroll(
        self,
        token: str,
        *,
        room_id: str,
        observed_at: datetime,
        authority: MeshEnrollmentAuthority,
    ) -> Literal["enrolled", "reconnected"]:
        """Consume one enrollment token and admit or re-admit a mesh worker.

        Returns ``"enrolled"`` on first admission and ``"reconnected"`` when a
        matching identity is re-admitted (no duplicate row).  Raises on token
        replay (stale/duplicate) or on identity equivocation (same worker_id
        with a different public key).
        """
        observed = _utc(observed_at)
        claim = authority.verify(token, observed_at=observed)
        worker_id = str(claim["worker_id"])
        public_key = str(claim["public_key"])
        agent_name = str(claim["agent_name"])
        runtime = cast("MeshWorkerRuntime", claim["runtime"])
        capabilities = tuple(cast("list[str]", claim["capabilities"]))
        if not worker_id or not agent_name:
            message = "mesh worker identity is blank"
            raise MeshEnrollmentError(message)

        with self._lock:
            conn = self._required_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                nonce = str(claim["nonce"])
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO mesh_enrollment_nonce VALUES(?,?)",
                    (nonce, observed.isoformat()),
                )
                if cursor.rowcount != 1:
                    message = "mesh enrollment token was already consumed"
                    raise MeshEnrollmentError(message)
                existing = (
                    conn.execute(
                        "SELECT agent_name,public_key,runtime,capabilities_json FROM mesh_worker "
                        "WHERE worker_id=?",
                        (worker_id,),
                    )
                ).fetchone()
                identity = (agent_name, public_key, runtime, _json_text(list(capabilities)))
                if existing is None:
                    conn.execute(
                        "INSERT INTO mesh_worker VALUES(?,?,?,?,?,?,?)",
                        (worker_id, *identity, room_id, observed.isoformat()),
                    )
                    status: Literal["enrolled", "reconnected"] = "enrolled"
                elif tuple(existing) != identity:
                    message = "mesh worker identity equivocation denied"
                    raise MeshEnrollmentError(message)
                else:
                    conn.execute(
                        "UPDATE mesh_worker SET room_id=?,last_seen_at=? WHERE worker_id=?",
                        (room_id, observed.isoformat(), worker_id),
                    )
                    status = "reconnected"
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return status

    def worker(self, worker_id: str) -> MeshEnrolledWorker | None:
        """Return one enrolled worker, or ``None`` if not present."""
        row = self._required_conn().execute(
            "SELECT worker_id,agent_name,public_key,runtime,capabilities_json,room_id,last_seen_at "
            "FROM mesh_worker WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        return _worker(tuple(row)) if row is not None else None

    def known_worker_ids(self) -> tuple[str, ...]:
        """Return all enrolled worker IDs (sorted for determinism)."""
        rows = self._required_conn().execute("SELECT worker_id FROM mesh_worker ORDER BY worker_id").fetchall()
        return tuple(str(row[0]) for row in rows)

    def _required_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            message = "mesh enrollment registry is not open"
            raise RuntimeError(message)
        return self._conn


class MeshEnrollmentCoordinator:
    """Orchestrate identity + authority + registry to admit/re-admit workers.

    Phase A is purely local: the identity file, the HMAC authority and the
    SQLite registry.  The external OpenClaw gateway handshake (Phase B) is a
    network side effect that stays hard-gated behind ``handshake_enabled``.
    """

    def __init__(
        self,
        *,
        authority: MeshEnrollmentAuthority,
        registry: MeshEnrollmentRegistry,
        identity_path: Path,
        enabled: bool = True,
        handshake: Callable[[], None] | None = None,
        handshake_enabled: bool = False,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._authority = authority
        self._registry = registry
        self._identity_path = identity_path
        self.enabled = enabled
        # Phase B: no network handshake by default.
        self.handshake = handshake
        self.handshake_enabled = handshake_enabled
        self._now = now if now is not None else _utc_now

    @property
    def identity_path(self) -> Path:
        """Return the persisted identity file path."""
        return self._identity_path

    @property
    def registry(self) -> MeshEnrollmentRegistry:
        """Return the durable enrollment registry."""
        return self._registry

    @property
    def authority(self) -> MeshEnrollmentAuthority:
        """Return the enrollment authority."""
        return self._authority

    def load_or_create_identity(
        self,
        *,
        worker_id: str,
        agent_name: str,
        capabilities: tuple[str, ...] = ("mesh.worker",),
    ) -> MeshWorkerIdentity:
        """Load the persisted identity or generate it once for a new worker."""
        if self._identity_path.exists():
            identity = MeshWorkerIdentity.load(self._identity_path)
            if identity.worker_id != worker_id or identity.agent_name != agent_name:
                message = "mesh worker identity does not match the requested registration"
                raise MeshEnrollmentError(message)
            return identity
        return MeshWorkerIdentity.generate(
            self._identity_path,
            worker_id=worker_id,
            agent_name=agent_name,
            capabilities=capabilities,
        )

    def issue_token(self, identity: MeshWorkerIdentity, *, expires_at: datetime) -> str:
        """Issue one short-lived enrollment claim for an identity."""
        return self._authority.issue(
            worker_id=identity.worker_id,
            agent_name=identity.agent_name,
            runtime=identity.runtime,
            public_key=identity.public_key,
            capabilities=identity.capabilities,
            expires_at=expires_at,
        )

    def admit(
        self,
        *,
        worker_id: str,
        agent_name: str,
        room_id: str,
        capabilities: tuple[str, ...] = ("mesh.worker",),
        token: str | None = None,
    ) -> MeshEnrollmentResult:
        """Admit or re-admit a worker using its stable local identity.

        - Fresh worker: issues + verifies a token, records enrollment -> ``enrolled``.
        - Re-admission (same identity file, same worker_id): re-admits -> ``reconnected``.
        - Stale/duplicate/equivocation: raises ``MeshEnrollmentError``.
        """
        self._assert_phase_b_gate()
        identity = self.load_or_create_identity(
            worker_id=worker_id,
            agent_name=agent_name,
            capabilities=capabilities,
        )
        observed = self._now()
        if token is None:
            token = self.issue_token(identity, expires_at=observed + timedelta(minutes=5))
        try:
            status = self._registry.enroll(
                token,
                room_id=room_id,
                observed_at=observed,
                authority=self._authority,
            )
        except MeshEnrollmentError as exc:
            return MeshEnrollmentResult(status="rejected", worker_id=worker_id, reason=str(exc))
        return MeshEnrollmentResult(status=status, worker_id=identity.worker_id)

    def _assert_phase_b_gate(self) -> None:
        """Ensure the external OpenClaw handshake is never invoked unless gated on."""
        if self.handshake_enabled and PHASE_B_HANDSHAKE_ENABLED and self.handshake is not None:
            self.handshake()  # pragma: no cover - never reached unless operator enables Phase B
            return
        if self.handshake_enabled and not PHASE_B_HANDSHAKE_ENABLED:
            message = "Phase B OpenClaw handshake is not approved; refusing external side effect"
            raise MeshEnrollmentError(message)


def _worker(row: tuple[object, ...]) -> MeshEnrolledWorker:
    return MeshEnrolledWorker(
        worker_id=str(row[0]),
        agent_name=str(row[1]),
        public_key=str(row[2]),
        runtime=cast("MeshWorkerRuntime", row[3]),
        capabilities=tuple(json.loads(str(row[4]))),
        room_id=str(row[5]),
        last_seen_at=datetime.fromisoformat(str(row[6])),
    )


def _json_text(value: object) -> str:
    return _json(value).decode()


def _utc_now() -> datetime:
    return datetime.now(UTC)
