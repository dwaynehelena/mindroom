"""Consent-aware portable memory records and cross-runtime propagation ledger."""

# Transaction bodies validate before a shared rollback handler.
# ruff: noqa: TRY301

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

import aiosqlite

if TYPE_CHECKING:
    from pathlib import Path

RuntimeTarget = Literal["mindroom", "openclaw", "hermes"]
MemoryStatus = Literal["active", "superseded", "deleted"]
PropagationHandler = Callable[["PropagationAction", str], Awaitable[str]]


class ProvenanceMemoryError(RuntimeError):
    """A consent, scope, lifecycle, or propagation invariant failed."""


@dataclass(frozen=True, slots=True)
class MemoryCitation:
    """One portable source reference without embedding private source content."""

    source: str
    source_event_id: str | None = None
    content_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ConsentGrant:
    """Purpose-bound consent from one canonical actor."""

    actor_id: str
    purpose: str
    granted_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PortableMemory:
    """One scoped, cited and consent-bound portable memory record."""

    memory_id: str
    owner_id: str
    scope: str
    content: str
    purpose: str
    created_at: datetime
    expires_at: datetime | None
    citations: tuple[MemoryCitation, ...]
    consent: ConsentGrant
    status: MemoryStatus
    supersedes: str | None = None


@dataclass(frozen=True, slots=True)
class PropagationAction:
    """One idempotent export/delete operation awaiting a runtime adapter."""

    action_id: str
    memory_id: str
    target: RuntimeTarget
    operation: Literal["upsert", "delete"]
    payload: dict[str, object] | None


class ProvenanceMemoryStore:
    """Transactional memory lifecycle and per-runtime propagation outbox."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the database and create the version-one schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS portable_memory (
              memory_id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              scope TEXT NOT NULL,
              content TEXT NOT NULL,
              purpose TEXT NOT NULL,
              created_at TEXT NOT NULL,
              expires_at TEXT,
              citations_json TEXT NOT NULL,
              consent_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('active','superseded','deleted')),
              supersedes TEXT
            );
            CREATE INDEX IF NOT EXISTS portable_memory_owner_scope
              ON portable_memory(owner_id, scope, status);
            CREATE TABLE IF NOT EXISTS memory_propagation (
              action_id TEXT PRIMARY KEY,
              memory_id TEXT NOT NULL,
              target TEXT NOT NULL CHECK(target IN ('mindroom','openclaw','hermes')),
              operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
              payload_json TEXT,
              status TEXT NOT NULL CHECK(status IN ('pending','executing','delivered','failed','uncertain')),
              failure TEXT,
              receipt TEXT,
              UNIQUE(memory_id,target,operation)
            );
            """,
        )
        await self._migrate_propagation_schema()
        await self._db.commit()

    async def close(self) -> None:
        """Close the store."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def remember(
        self,
        *,
        memory_id: str,
        owner_id: str,
        scope: str,
        content: str,
        purpose: str,
        citations: tuple[MemoryCitation, ...],
        consent: ConsentGrant,
        created_at: datetime,
        expires_at: datetime | None = None,
        contradicts: str | None = None,
        targets: tuple[RuntimeTarget, ...] = ("mindroom", "openclaw", "hermes"),
    ) -> PortableMemory:
        """Store a consent-bound memory and enqueue idempotent cross-runtime upserts."""
        created = _utc(created_at)
        expiry = _utc(expires_at) if expires_at is not None else None
        _validate_consent(consent, owner_id=owner_id, purpose=purpose, observed_at=created)
        if not all((memory_id, owner_id, scope, content, purpose)) or (expiry is not None and expiry <= created):
            message = "memory identity, scope, content, purpose, or TTL is invalid"
            raise ProvenanceMemoryError(message)
        record = PortableMemory(
            memory_id=memory_id,
            owner_id=owner_id,
            scope=scope,
            content=content,
            purpose=purpose,
            created_at=created,
            expires_at=expiry,
            citations=citations,
            consent=consent,
            status="active",
            supersedes=contradicts,
        )
        async with self._lock:
            db = self._required_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                if contradicts is not None:
                    prior = await self._load_row(contradicts)
                    if prior.owner_id != owner_id or prior.scope != scope or prior.status != "active":
                        message = "contradiction target must be active in the same owner scope"
                        raise ProvenanceMemoryError(message)
                    await db.execute(
                        "UPDATE portable_memory SET status='superseded' WHERE memory_id=? AND status='active'",
                        (contradicts,),
                    )
                values = _record_values(record)
                existing = await (
                    await db.execute("SELECT * FROM portable_memory WHERE memory_id=?", (memory_id,))
                ).fetchone()
                if existing is None:
                    await db.execute("INSERT INTO portable_memory VALUES(?,?,?,?,?,?,?,?,?,?,?)", values)
                elif tuple(existing) != values:
                    message = "memory identity equivocation denied"
                    raise ProvenanceMemoryError(message)
                for target in tuple(dict.fromkeys(targets)):
                    await self._enqueue(record, target=target, operation="upsert")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return record

    async def export(
        self,
        *,
        owner_id: str,
        scope: str,
        actor_id: str,
        purpose: str,
        observed_at: datetime,
    ) -> tuple[PortableMemory, ...]:
        """Return only active, unexpired records covered by actor and purpose consent."""
        observed = _utc(observed_at)
        rows = await (
            await self._required_db().execute(
                "SELECT * FROM portable_memory WHERE owner_id=? AND scope=? AND status='active' ORDER BY created_at",
                (owner_id, scope),
            )
        ).fetchall()
        exported: list[PortableMemory] = []
        for row in rows:
            record = _row_to_record(row)
            if record.expires_at is not None and observed > record.expires_at:
                continue
            try:
                _validate_consent(record.consent, owner_id=actor_id, purpose=purpose, observed_at=observed)
            except ProvenanceMemoryError:
                continue
            exported.append(record)
        return tuple(exported)

    async def delete(
        self,
        memory_id: str,
        *,
        actor_id: str,
        observed_at: datetime,
        targets: tuple[RuntimeTarget, ...] = ("mindroom", "openclaw", "hermes"),
    ) -> None:
        """Tombstone an authorized memory and enqueue deletion for every runtime."""
        observed = _utc(observed_at)
        async with self._lock:
            db = self._required_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                record = await self._load_row(memory_id)
                _validate_consent(record.consent, owner_id=actor_id, purpose=record.purpose, observed_at=observed)
                if record.status != "deleted":
                    await db.execute("UPDATE portable_memory SET status='deleted' WHERE memory_id=?", (memory_id,))
                deleted = replace(record, status="deleted")
                for target in tuple(dict.fromkeys(targets)):
                    await self._enqueue(deleted, target=target, operation="delete")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def pending_actions(self) -> tuple[PropagationAction, ...]:
        """Return pending propagation work without exposing local database metadata."""
        rows = await (
            await self._required_db().execute(
                "SELECT action_id,memory_id,target,operation,payload_json FROM memory_propagation "
                "WHERE status='pending' ORDER BY action_id",
            )
        ).fetchall()
        return tuple(
            PropagationAction(row[0], row[1], row[2], row[3], json.loads(row[4]) if row[4] else None) for row in rows
        )

    async def mark_delivered(self, action_id: str) -> None:
        """Mark one adapter-confirmed propagation action delivered."""
        cursor = await self._required_db().execute(
            "UPDATE memory_propagation SET status='delivered',failure=NULL "
            "WHERE action_id=? AND status IN ('pending','executing')",
            (action_id,),
        )
        if cursor.rowcount != 1:
            message = "propagation action is not pending"
            raise ProvenanceMemoryError(message)
        await self._required_db().commit()

    async def claim(self, action_id: str) -> None:
        """Claim one pending propagation action for a single adapter attempt."""
        cursor = await self._required_db().execute(
            "UPDATE memory_propagation SET status='executing' WHERE action_id=? AND status='pending'",
            (action_id,),
        )
        if cursor.rowcount != 1:
            message = "propagation action is not claimable"
            raise ProvenanceMemoryError(message)
        await self._required_db().commit()

    async def settle(self, action_id: str, *, receipt: str) -> None:
        """Settle one claimed action with an adapter receipt."""
        if not receipt:
            message = "propagation receipt is required"
            raise ProvenanceMemoryError(message)
        cursor = await self._required_db().execute(
            "UPDATE memory_propagation SET status='delivered',failure=NULL,receipt=? "
            "WHERE action_id=? AND status='executing'",
            (receipt, action_id),
        )
        if cursor.rowcount != 1:
            message = "propagation action is not executing"
            raise ProvenanceMemoryError(message)
        await self._required_db().commit()

    async def fail(self, action_id: str, failure_class: str) -> None:
        """Persist a sanitized failure class for one known failed attempt."""
        cursor = await self._required_db().execute(
            "UPDATE memory_propagation SET status='failed',failure=? "
            "WHERE action_id=? AND status='executing'",
            (failure_class[:256], action_id),
        )
        if cursor.rowcount != 1:
            message = "propagation action is not executing"
            raise ProvenanceMemoryError(message)
        await self._required_db().commit()

    async def recover_uncertain(self) -> int:
        """Quarantine adapter attempts interrupted before a durable receipt."""
        cursor = await self._required_db().execute(
            "UPDATE memory_propagation SET status='uncertain' WHERE status='executing'",
        )
        await self._required_db().commit()
        return cursor.rowcount

    async def propagation_status(self, action_id: str) -> tuple[str, str | None, str | None]:
        """Return status, sanitized failure class, and receipt."""
        row = await (
            await self._required_db().execute(
                "SELECT status,failure,receipt FROM memory_propagation WHERE action_id=?",
                (action_id,),
            )
        ).fetchone()
        if row is None:
            message = "propagation action not found"
            raise ProvenanceMemoryError(message)
        return str(row[0]), str(row[1]) if row[1] else None, str(row[2]) if row[2] else None

    async def _enqueue(
        self,
        record: PortableMemory,
        *,
        target: RuntimeTarget,
        operation: Literal["upsert", "delete"],
    ) -> None:
        payload = _portable_payload(record) if operation == "upsert" else None
        action_id = hashlib.sha256(f"{record.memory_id}\x1f{target}\x1f{operation}".encode()).hexdigest()
        await self._required_db().execute(
            "INSERT OR IGNORE INTO memory_propagation VALUES(?,?,?,?,?,'pending',NULL,NULL)",
            (action_id, record.memory_id, target, operation, _json(payload) if payload is not None else None),
        )

    async def _load_row(self, memory_id: str) -> PortableMemory:
        row = await (
            await self._required_db().execute("SELECT * FROM portable_memory WHERE memory_id=?", (memory_id,))
        ).fetchone()
        if row is None:
            message = "memory record not found"
            raise ProvenanceMemoryError(message)
        return _row_to_record(row)

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "provenance memory store is not open"
            raise RuntimeError(message)
        return self._db

    async def _migrate_propagation_schema(self) -> None:
        """Upgrade the original propagation lifecycle without losing outbox rows."""
        row = await (
            await self._required_db().execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_propagation'",
            )
        ).fetchone()
        schema = str(row[0]) if row else ""
        if "'executing'" in schema and "receipt" in schema:
            return
        await self._required_db().executescript(
            """
            ALTER TABLE memory_propagation RENAME TO memory_propagation_v1;
            CREATE TABLE memory_propagation (
              action_id TEXT PRIMARY KEY,
              memory_id TEXT NOT NULL,
              target TEXT NOT NULL CHECK(target IN ('mindroom','openclaw','hermes')),
              operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
              payload_json TEXT,
              status TEXT NOT NULL CHECK(status IN ('pending','executing','delivered','failed','uncertain')),
              failure TEXT,
              receipt TEXT,
              UNIQUE(memory_id,target,operation)
            );
            INSERT INTO memory_propagation(action_id,memory_id,target,operation,payload_json,status,failure,receipt)
              SELECT action_id,memory_id,target,operation,payload_json,status,failure,NULL
              FROM memory_propagation_v1;
            DROP TABLE memory_propagation_v1;
            """,
        )


class MemoryPropagator:
    """Deliver pending memory upserts/deletes once to all explicit runtimes."""

    def __init__(
        self,
        store: ProvenanceMemoryStore,
        handlers: Mapping[RuntimeTarget, PropagationHandler],
    ) -> None:
        if set(handlers) != {"mindroom", "openclaw", "hermes"}:
            message = "memory propagation requires MindRoom, OpenClaw, and Hermes handlers"
            raise ValueError(message)
        self._store = store
        self._handlers = dict(handlers)

    async def drain(self) -> dict[str, str]:
        """Attempt each pending action independently and return content-free outcomes."""
        outcomes: dict[str, str] = {}
        for action in await self._store.pending_actions():
            try:
                await self._store.claim(action.action_id)
            except ProvenanceMemoryError:
                outcomes[action.action_id] = "skipped"
                continue
            try:
                receipt = await self._handlers[action.target](action, action.action_id)
                await self._store.settle(action.action_id, receipt=receipt)
            except Exception as exc:
                await self._store.fail(
                    action.action_id,
                    f"{type(exc).__module__}.{type(exc).__qualname__}",
                )
                outcomes[action.action_id] = "failed"
            else:
                outcomes[action.action_id] = "delivered"
        return outcomes


def _validate_consent(grant: ConsentGrant, *, owner_id: str, purpose: str, observed_at: datetime) -> None:
    granted = _utc(grant.granted_at)
    expiry = _utc(grant.expires_at) if grant.expires_at is not None else None
    if grant.actor_id != owner_id or grant.purpose != purpose or observed_at < granted:
        message = "memory consent does not cover actor, purpose, or observation time"
        raise ProvenanceMemoryError(message)
    if expiry is not None and observed_at > expiry:
        message = "memory consent has expired"
        raise ProvenanceMemoryError(message)


def _portable_payload(record: PortableMemory) -> dict[str, object]:
    return {
        "schema": "mindroom.provenance-memory/1",
        "memory_id": record.memory_id,
        "owner_id": record.owner_id,
        "scope": record.scope,
        "content": record.content,
        "purpose": record.purpose,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "citations": [asdict(citation) for citation in record.citations],
        "supersedes": record.supersedes,
    }


def _record_values(record: PortableMemory) -> tuple[object, ...]:
    return (
        record.memory_id,
        record.owner_id,
        record.scope,
        record.content,
        record.purpose,
        record.created_at.isoformat(),
        record.expires_at.isoformat() if record.expires_at else None,
        _json([asdict(citation) for citation in record.citations]),
        _json(
            {
                "actor_id": record.consent.actor_id,
                "purpose": record.consent.purpose,
                "granted_at": record.consent.granted_at.isoformat(),
                "expires_at": record.consent.expires_at.isoformat() if record.consent.expires_at else None,
            },
        ),
        record.status,
        record.supersedes,
    )


def _row_to_record(row: Sequence[object]) -> PortableMemory:
    citations = tuple(MemoryCitation(**value) for value in json.loads(str(row[7])))
    consent_value = json.loads(str(row[8]))
    consent = ConsentGrant(
        actor_id=consent_value["actor_id"],
        purpose=consent_value["purpose"],
        granted_at=datetime.fromisoformat(consent_value["granted_at"]),
        expires_at=datetime.fromisoformat(consent_value["expires_at"]) if consent_value["expires_at"] else None,
    )
    return PortableMemory(
        memory_id=str(row[0]),
        owner_id=str(row[1]),
        scope=str(row[2]),
        content=str(row[3]),
        purpose=str(row[4]),
        created_at=datetime.fromisoformat(str(row[5])),
        expires_at=datetime.fromisoformat(str(row[6])) if row[6] else None,
        citations=citations,
        consent=consent,
        status=cast("MemoryStatus", str(row[9])),
        supersedes=str(row[10]) if row[10] else None,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "memory timestamps must include a timezone"
        raise ProvenanceMemoryError(message)
    return value.astimezone(UTC)
