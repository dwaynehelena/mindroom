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
  enrollment claims.  **Phase B Unit 1 (inverted enrollment):** this now
  subclasses the shared P9 edge-fleet ``edge_fleet.EnrollmentAuthority`` rather
  than being a standalone mesh sibling.  It reuses the exact same HMAC-SHA256
  scheme, canonical JSON helpers and 32-byte key contract as the edge fleet, so
  the gateway/authority issues the same ``mindroom.edge-enrollment/1`` claims
  that an OpenClaw ``EdgeNodeClient.enroll(token)`` proves possession of.  It
  additionally keeps a mesh-facing claim shape (``worker_id``/``agent_name``,
  schema ``mindroom.mesh-enrollment/1``) and ``verify`` accepts BOTH shapes so
  inverted enrollment is backward compatible with the Phase A mesh claim.

- ``MeshEnrollmentRegistry`` — a durable worker inventory (``worker_id <-> room``
  binding, capabilities, ``last_seen``) in a synchronous SQLite store mirroring
  the ``mindroom.edge_fleet.EdgeFleet`` table pattern.

- ``MeshEnrollmentCoordinator`` — orchestrates the identity, authority and
  registry to admit/re-admit workers, and gates the external OpenClaw
  handshake behind an explicit human approval (Phase B).

PHASE B GATE
------------
The real OpenClaw gateway enrollment *handshake* is an **external side effect**
(a network round-trip to the OpenClaw gateway authority).  **Phase B Unit 1
gate status: CLEARED** (2026-08-07) — a real live inverted-enrollment handshake
passed against the live P9 edge-node surface
(``POST /api/edge-fleet/enroll``, HTTP 200, node admitted) using the shared
``edge_fleet.EnrollmentAuthority`` that ``MeshEnrollmentAuthority`` subclasses.
See ``docs/mesh_enrollment_phase_b_gate.md`` and
``scripts/testing/mesh_phaseb_unit1_live_smoke.py``.

Clearing the gate only *permits* the external handshake.  No network call is
made unless an operator additionally binds a real ``handshake`` callable AND
sets ``handshake_enabled=True`` on the coordinator; with ``handshake=None``
(the default) the coordinator stays inert and no external side effect occurs.
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

from mindroom.edge_fleet import (
    EdgeFleetError,
    EnrollmentAuthority,
    WorkerRuntime,
    _b64,
    _json,
    _unb64,
    _utc,
)

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

#: Phase B handshake gate.  **Phase B Unit 1 gate: CLEARED** on 2026-08-07.
#: A real live inverted-enrollment handshake passed against the live P9
#: edge-node enrollment surface (``POST /api/edge-fleet/enroll``, HTTP 200,
#: node admitted) using the shared ``edge_fleet.EnrollmentAuthority`` that
#: ``MeshEnrollmentAuthority`` subclasses — see
#: ``docs/mesh_enrollment_phase_b_gate.md`` and
#: ``scripts/testing/mesh_phaseb_unit1_live_smoke.py``.
#:
#: NOTE: clearing the gate only *permits* the OpenClaw gateway handshake; no
#: network call is made unless an operator additionally binds a real
#: ``handshake`` callable AND sets ``handshake_enabled=True`` on the
#: coordinator.  With ``handshake=None`` (default) the coordinator stays inert.
PHASE_B_HANDSHAKE_ENABLED = True


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


class MeshEnrollmentAuthority(EnrollmentAuthority):
    """Issue/verify short-lived HMAC-signed mesh enrollment claims.

    **Phase B Unit 1 — inverted enrollment.**  This now subclasses the shared
    P9 edge-fleet ``edge_fleet.EnrollmentAuthority`` instead of being a
    standalone mesh sibling.  It inherits the exact 32-byte key contract,
    HMAC-SHA256 signing scheme and canonical JSON helpers, so a mesh worker's
    claim can be issued through the same authority that an OpenClaw
    ``EdgeNodeClient.enroll(token)`` presents to the P9 edge-fleet surface.

    It deliberately keeps two claim faces:
    - ``issue``/``verify`` for the mesh-facing claim (``worker_id``,
      ``agent_name``, schema ``mindroom.mesh-enrollment/1``) so Phase A
      admission/re-admission behavior is unchanged; and
    - ``issue_edge``/``verify`` accepting the shared edge-fleet claim
      (``node_id``, schema ``mindroom.edge-enrollment/1``) so inverted
      enrollment through the shared P9 authority verifies transparently.
    """

    def __init__(self, key: bytes) -> None:
        super().__init__(key)  # reuses the shared edge-fleet 32-byte key contract

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

    def issue_edge(
        self,
        *,
        node_id: str,
        runtime: WorkerRuntime,
        public_key: str,
        capabilities: tuple[str, ...],
        expires_at: datetime,
    ) -> str:
        """Issue a shared edge-fleet enrollment claim (``mindroom.edge-enrollment/1``).

        This is the inverted path: the gateway/authority issues a claim through
        the exact same shared ``EnrollmentAuthority`` code, so an OpenClaw
        ``EdgeNodeClient`` can present it to the P9 edge-fleet endpoint.
        """
        return super().issue(
            node_id=node_id,
            runtime=runtime,
            public_key=public_key,
            capabilities=capabilities,
            expires_at=expires_at,
        )

    def verify(self, token: str, *, observed_at: datetime) -> dict[str, object]:
        """Verify signature, schema, expiry and strict claim shape.

        Accepts both the mesh claim shape (``worker_id``/``agent_name``,
        schema ``mindroom.mesh-enrollment/1``) and the shared edge-fleet claim
        shape (``node_id``, schema ``mindroom.edge-enrollment/1``).  The claim
        schema is read first and dispatched to the matching strict validator so
        genuine edge-fleet failures (expired / bad signature) surface with their
        exact reason rather than being masked by the mesh fallback.
        """
        schema = _claim_schema(token)
        if schema == "mindroom.edge-enrollment/1":
            # Inverted path — delegate to the shared P9 edge-fleet authority.
            # Translate its failure to the mesh error contract so the registry
            # produces a ``rejected`` admission result (never lets the shared
            # authority's exception escape the mesh surface).
            try:
                return super().verify(token, observed_at=observed_at)
            except EdgeFleetError as exc:
                raise MeshEnrollmentError(str(exc)) from exc
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
        # Phase B Unit 1 (inverted enrollment): the shared edge-fleet claim
        # carries ``node_id`` and does NOT authoritatively carry an
        # ``agent_name``; the Phase A mesh claim carries both
        # ``worker_id``/``agent_name``.  Normalize both to the mesh worker
        # identity so the durable re-admission registry is shape-agnostic.
        worker_id = str(claim.get("worker_id") or claim.get("node_id") or "")
        public_key = str(claim["public_key"])
        # A mesh claim carries an authoritative agent_name; an inverted edge
        # claim does not, so its agent_name is not part of the identity
        # equivalence check and defaults to the worker_id on first admission.
        has_authoritative_agent_name = bool(claim.get("agent_name"))
        agent_name = str(claim.get("agent_name") or worker_id)
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
                if existing is None:
                    conn.execute(
                        "INSERT INTO mesh_worker VALUES(?,?,?,?,?,?,?)",
                        (worker_id, agent_name, public_key, runtime, _json_text(list(capabilities)), room_id, observed.isoformat()),
                    )
                    status: Literal["enrolled", "reconnected"] = "enrolled"
                else:
                    # Identity equivalence check.  For an inverted edge claim the
                    # agent_name is NOT authoritative, so on re-admission we
                    # adopt the existing row's agent_name and only enforce the
                    # fields the claim truly binds (public_key, runtime,
                    # capabilities).
                    effective_agent_name = existing[0] if not has_authoritative_agent_name else agent_name
                    identity = (
                        effective_agent_name,
                        public_key,
                        runtime,
                        _json_text(list(capabilities)),
                    )
                    if (existing[0], existing[1], existing[2], existing[3]) != identity:
                        message = "mesh worker identity equivocation denied"
                        raise MeshEnrollmentError(message)
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

    def issue_edge_token(
        self,
        *,
        node_id: str,
        public_key: str,
        runtime: WorkerRuntime = "openclaw",
        capabilities: tuple[str, ...] = ("mesh.worker",),
        expires_at: datetime,
    ) -> str:
        """Issue one shared edge-fleet enrollment claim (inverted enrollment).

        Phase B Unit 1: the gateway/authority issues a ``mindroom.edge-enrollment/1``
        claim through the shared P9 ``EnrollmentAuthority``.  An OpenClaw worker
        presents this token to ``EdgeNodeClient.enroll(token)`` against the live
        P9 edge-fleet surface, and the mesh registry admits/re-admits the same
        identity on verification.
        """
        return self._authority.issue_edge(
            node_id=node_id,
            runtime=runtime,
            public_key=public_key,
            capabilities=capabilities,
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
        - Inverted enrollment: a caller-supplied ``token`` may be a shared
          edge-fleet claim (``mindroom.edge-enrollment/1``, ``node_id``) issued
          through ``issue_edge_token``; the registry normalizes ``node_id`` to
          the mesh ``worker_id`` and re-admits on verification.
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


def _claim_schema(token: str) -> str:
    """Decode the claim's ``schema`` field to route verification.

    Returns ``""`` for any token that cannot be decoded (so the caller treats
    it as a malformed mesh claim); never raises for a structurally invalid
    token.
    """
    try:
        encoded_payload, _signature = token.split(".", 1)
        claim = json.loads(_unb64(encoded_payload))
    except (ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(claim, dict):
        return ""
    return str(claim.get("schema") or "")


def _utc_now() -> datetime:
    return datetime.now(UTC)
