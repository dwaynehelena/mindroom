"""Transactional SQLite session mapping and invocation lifecycle ledger."""
# ruff: noqa: C901, EM101, EM102, TC001, TC003, TRY003, TRY300, TRY301

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from mindroom.message_target import MessageTarget

from .models import ConversationScope, RuntimeIdentity

_SCHEMA_VERSION = 5
_ROOM_SCOPE = ""
_STATUSES = frozenset(
    {"reserved", "invoking", "response_ready", "delivering", "delivered", "delivery_failed", "failed"},
)


class SourceEventConflictError(RuntimeError):
    """Raised when one source event ID is reused for another runtime/scope."""


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    """Everything needed to retry or reconcile one native Matrix delivery."""

    source_event_id: str
    response_text: str
    transaction_id: str
    target: MessageTarget
    status: str
    delivery_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeStateEvent:
    """Content-free reconnectable lifecycle progress for one invocation."""

    source_event_id: str
    sequence: int
    phase: str
    recorded_at: str


class RuntimeBridgeStore:
    """Durable sessions and at-most-once invocation lifecycle facts."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open and migrate the store without overwriting unknown schema versions."""
        async with self._lock:
            if self._db is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(self._path)
            try:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA synchronous=FULL")
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute("PRAGMA busy_timeout=5000")
                cursor = await db.execute("PRAGMA user_version")
                row = await cursor.fetchone()
                await cursor.close()
                version = int(row[0]) if row else 0
                integrity = await (await db.execute("PRAGMA integrity_check")).fetchone()
                if integrity != ("ok",):
                    raise RuntimeError(f"runtime bridge database integrity check failed: {integrity!r}")
                if version > _SCHEMA_VERSION:
                    raise RuntimeError(
                        f"runtime bridge database schema {version} is newer than supported {_SCHEMA_VERSION}",
                    )
                await db.execute("BEGIN IMMEDIATE")
                if version == 0:
                    objects = await (await db.execute(
                        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'",
                    )).fetchall()
                    if objects:
                        raise RuntimeError("unversioned non-empty runtime bridge database is rejected")
                    await _create_schema_v5(db)
                elif version == 1:
                    await _migrate_v1_to_v2(db)
                    await _migrate_v2_to_v3(db)
                    await _migrate_v3_to_v4(db)
                    await _migrate_v4_to_v5(db)
                elif version == 2:
                    await _migrate_v2_to_v3(db)
                    await _migrate_v3_to_v4(db)
                    await _migrate_v4_to_v5(db)
                elif version == 3:
                    await _migrate_v3_to_v4(db)
                    await _migrate_v4_to_v5(db)
                elif version == 4:
                    await _migrate_v4_to_v5(db)
                await db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                await db.commit()
            except BaseException:
                with suppress(Exception):
                    await db.rollback()
                with suppress(Exception):
                    await db.close()
                raise
            self._db = db

    async def close(self) -> None:
        """Close the store; safe to call repeatedly."""
        async with self._lock:
            if self._db is None:
                return
            db, self._db = self._db, None
            await db.close()

    async def reserve_source_event(
        self,
        *,
        identity: RuntimeIdentity,
        scope: ConversationScope,
        source_event_id: str,
        request_digest: str | None = None,
        max_sessions: int | None = None,
    ) -> tuple[str, bool]:
        """Atomically reserve one event; any prior lifecycle state prevents replay."""
        if not source_event_id.strip():
            raise ValueError("source_event_id must not be blank")
        thread_id = scope.thread_id or _ROOM_SCOPE
        session_id = _session_id(identity, scope)
        async with self._lock:
            db = self._require_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                existing_scope = await (await db.execute(
                    "SELECT session_id FROM sessions WHERE runtime_key=? AND room_id=? AND thread_id=?",
                    (identity.key, scope.room_id, thread_id),
                )).fetchone()
                if existing_scope is None and max_sessions is not None:
                    if max_sessions < 1:
                        raise ValueError("max_sessions must be positive")
                    count = await (await db.execute("SELECT COUNT(*) FROM sessions")).fetchone()
                    needed = max(0, int(count[0]) - max_sessions + 1)
                    if needed:
                        cursor = await db.execute("""DELETE FROM sessions WHERE session_id IN (
                            SELECT s.session_id FROM sessions s WHERE NOT EXISTS (
                            SELECT 1 FROM source_events e WHERE e.session_id=s.session_id
                            AND e.status IN ('reserved','invoking','response_ready','delivering'))
                            ORDER BY s.last_used_at,s.created_at LIMIT ?)""", (needed,))
                        if cursor.rowcount < needed:
                            raise RuntimeError("max_sessions reached and all eviction candidates are unsettled")
                await db.execute(
                    """INSERT INTO sessions (runtime_key, room_id, thread_id, session_id)
                    VALUES (?, ?, ?, ?) ON CONFLICT(runtime_key, room_id, thread_id)
                    DO UPDATE SET last_used_at = CURRENT_TIMESTAMP""",
                    (identity.key, scope.room_id, thread_id, session_id),
                )
                cursor = await db.execute(
                    """SELECT runtime_key, room_id, thread_id, session_id
                    FROM source_events WHERE source_event_id = ?""",
                    (source_event_id,),
                )
                existing = await cursor.fetchone()
                await cursor.close()
                if existing is not None:
                    expected = (identity.key, scope.room_id, thread_id, session_id)
                    if tuple(existing) != expected:
                        raise SourceEventConflictError("source event ID is already bound to another runtime scope")
                    await db.commit()
                    return session_id, False
                await db.execute(
                    """INSERT INTO source_events
                    (source_event_id, runtime_key, room_id, thread_id, session_id, status, request_digest)
                    VALUES (?, ?, ?, ?, ?, 'reserved', ?)""",
                    (source_event_id, identity.key, scope.room_id, thread_id, session_id, request_digest),
                )
                await db.commit()
                return session_id, True
            except BaseException:
                with suppress(Exception):
                    await db.rollback()
                raise

    async def mark_invoking(self, source_event_id: str, request_digest: str) -> None:
        """Durably record that external execution is about to begin."""
        await self._transition(
            source_event_id,
            from_status="reserved",
            to_status="invoking",
            assignments="request_digest = ?, invoking_at = CURRENT_TIMESTAMP",
            values=(request_digest,),
        )

    async def mark_response_ready(
        self,
        source_event_id: str,
        response_digest: str,
        response_text: str,
        delivery_key: str,
        target: MessageTarget | None = None,
    ) -> None:
        """Atomically persist a validated response and complete delivery target."""
        target_json = json.dumps(target.to_metadata(), separators=(",", ":"), sort_keys=True) if target else None
        await self._transition(
            source_event_id,
            from_status="invoking",
            to_status="response_ready",
            assignments=(
                "response_digest = ?, response_text = ?, delivery_key = ?, target_json = ?, "
                "response_ready_at = CURRENT_TIMESTAMP"
            ),
            values=(response_digest, response_text, delivery_key, target_json),
        )

    async def mark_accepted(self, source_event_id: str, response_digest: str) -> None:
        """Compatibility alias: persist an empty response as response-ready."""
        await self.mark_response_ready(source_event_id, response_digest, "", _delivery_key(source_event_id))

    async def mark_delivering(self, source_event_id: str) -> None:
        """Reserve the single stable delivery attempt."""
        await self._transition(
            source_event_id,
            from_status="response_ready",
            to_status="delivering",
            assignments="delivering_at = CURRENT_TIMESTAMP",
            values=(),
        )

    async def mark_delivered(self, source_event_id: str, event_id: str) -> None:
        """Persist the native Matrix event ID proving delivery."""
        await self._transition(
            source_event_id,
            from_status="delivering",
            to_status="delivered",
            assignments="delivery_event_id = ?, delivered_at = CURRENT_TIMESTAMP",
            values=(event_id,),
        )

    async def mark_delivery_failed(self, source_event_id: str, failure: str) -> None:
        """Persist a sanitized delivery failure; explicit retry may requeue it."""
        await self._transition(
            source_event_id,
            from_status="delivering",
            to_status="delivery_failed",
            assignments="sanitized_failure = ?, delivery_failed_at = CURRENT_TIMESTAMP",
            values=(failure[:128],),
        )

    async def recover_delivery_queue(self) -> tuple[DeliveryRecord, ...]:
        """Return response-ready and uncertain delivering rows with stable txn IDs.

        Re-sending ``delivering`` with the same Matrix transaction ID is safe and
        is the only automatic recovery performed; ``invoking`` is never replayed.
        Legacy rows lacking a persisted target remain quarantined.
        """
        async with self._lock:
            cursor = await self._require_db().execute(
                """SELECT source_event_id, response_text, delivery_key, target_json, status, delivery_event_id
                FROM source_events WHERE status IN ('response_ready','delivering')
                AND target_json IS NOT NULL ORDER BY reserved_at, source_event_id""",
            )
            rows = await cursor.fetchall()
            await cursor.close()
        records: list[DeliveryRecord] = []
        for row in rows:
            try:
                target = MessageTarget.from_metadata(json.loads(str(row[3])))
            except (TypeError, ValueError, json.JSONDecodeError):
                target = None
            if target is not None:
                records.append(
                    DeliveryRecord(
                        source_event_id=str(row[0]),
                        response_text=str(row[1]),
                        transaction_id=str(row[2]),
                        target=target,
                        status=str(row[4]),
                        delivery_event_id=str(row[5]) if row[5] is not None else None,
                    ),
                )
        return tuple(records)

    async def delivered_records(self) -> tuple[DeliveryRecord, ...]:
        """Return delivered rows used to repair a missed handled-turn write."""
        async with self._lock:
            cursor = await self._require_db().execute(
                """SELECT source_event_id,response_text,delivery_key,target_json,status,delivery_event_id
                FROM source_events WHERE status='delivered' AND target_json IS NOT NULL
                AND delivery_event_id IS NOT NULL ORDER BY reserved_at,source_event_id""",
            )
            rows = await cursor.fetchall()
            await cursor.close()
        records: list[DeliveryRecord] = []
        for row in rows:
            target = MessageTarget.from_metadata(json.loads(str(row[3])))
            if target is not None:
                records.append(DeliveryRecord(str(row[0]), str(row[1]), str(row[2]), target, str(row[4]), str(row[5])))
        return tuple(records)

    async def prune_sessions(self, *, max_sessions: int) -> int:
        """Bound inactive sessions without deleting unsettled invocation/outbox records."""
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute("SELECT COUNT(*) FROM sessions")
            row = await cursor.fetchone()
            await cursor.close()
            excess = max(0, int(row[0]) - max_sessions) if row else 0
            if not excess:
                return 0
            cursor = await db.execute(
                """DELETE FROM sessions WHERE session_id IN (
                SELECT s.session_id FROM sessions s
                WHERE NOT EXISTS (
                    SELECT 1 FROM source_events e WHERE e.session_id=s.session_id
                    AND e.status IN ('reserved','invoking','response_ready','delivering'))
                ORDER BY s.last_used_at, s.created_at LIMIT ?)""",
                (excess,),
            )
            deleted = cursor.rowcount
            await cursor.close()
            await db.commit()
            return deleted

    async def mark_failed(self, source_event_id: str, sanitized_failure: str) -> None:
        """Record a bounded sanitized failure without enabling replay."""
        failure = sanitized_failure[:128]
        await self._transition(
            source_event_id,
            from_status="invoking",
            to_status="failed",
            assignments="sanitized_failure = ?, failed_at = CURRENT_TIMESTAMP",
            values=(failure,),
        )

    async def lifecycle(self, source_event_id: str) -> tuple[str, str | None, str | None, str | None] | None:
        """Return status, request digest, response digest, and sanitized failure."""
        async with self._lock:
            cursor = await self._require_db().execute(
                "SELECT status, request_digest, response_digest, sanitized_failure FROM source_events WHERE source_event_id = ?",
                (source_event_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return tuple(row) if row is not None else None  # type: ignore[return-value]

    async def append_state(self, source_event_id: str, phase: str) -> RuntimeStateEvent:
        """Append one content-free monotonic lifecycle event."""
        if phase not in _STATUSES:
            raise ValueError("invalid runtime state phase")
        async with self._lock:
            db = self._require_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                exists = await (
                    await db.execute("SELECT 1 FROM source_events WHERE source_event_id=?", (source_event_id,))
                ).fetchone()
                if exists is None:
                    raise RuntimeError("runtime source event not found")
                row = await (
                    await db.execute(
                        "SELECT COALESCE(MAX(sequence),0)+1 FROM runtime_state_event WHERE source_event_id=?",
                        (source_event_id,),
                    )
                ).fetchone()
                sequence = int(row[0])
                await db.execute(
                    "INSERT INTO runtime_state_event(source_event_id,sequence,phase) VALUES(?,?,?)",
                    (source_event_id, sequence, phase),
                )
                recorded = await (
                    await db.execute(
                        "SELECT recorded_at FROM runtime_state_event WHERE source_event_id=? AND sequence=?",
                        (source_event_id, sequence),
                    )
                ).fetchone()
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return RuntimeStateEvent(source_event_id, sequence, phase, str(recorded[0]))

    async def states_since(self, source_event_id: str, *, after_sequence: int = 0) -> tuple[RuntimeStateEvent, ...]:
        """Replay lifecycle events after a reconnect cursor."""
        if after_sequence < 0:
            raise ValueError("state sequence cursor must not be negative")
        async with self._lock:
            rows = await (
                await self._require_db().execute(
                    "SELECT source_event_id,sequence,phase,recorded_at FROM runtime_state_event "
                    "WHERE source_event_id=? AND sequence>? ORDER BY sequence",
                    (source_event_id, after_sequence),
                )
            ).fetchall()
        return tuple(RuntimeStateEvent(str(row[0]), int(row[1]), str(row[2]), str(row[3])) for row in rows)

    async def _transition(
        self,
        source_event_id: str,
        *,
        from_status: str,
        to_status: str,
        assignments: str,
        values: tuple[object, ...],
    ) -> None:
        if from_status not in _STATUSES or to_status not in _STATUSES:
            raise ValueError("invalid invocation lifecycle status")
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                f"UPDATE source_events SET status = ?, {assignments} WHERE source_event_id = ? AND status = ?",  # noqa: S608
                (to_status, *values, source_event_id, from_status),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise RuntimeError(f"invalid runtime invocation transition from {from_status} to {to_status}")
            await cursor.close()
            await db.commit()

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("runtime bridge store is not open")
        return self._db


async def _create_schema_v5(db: aiosqlite.Connection) -> None:
    await db.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
            runtime_key TEXT NOT NULL, room_id TEXT NOT NULL, thread_id TEXT NOT NULL,
            session_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (runtime_key, room_id, thread_id))""",
    )
    await db.execute(
        """CREATE TABLE IF NOT EXISTS source_events (
            source_event_id TEXT PRIMARY KEY, runtime_key TEXT NOT NULL, room_id TEXT NOT NULL,
            thread_id TEXT NOT NULL, session_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN
                ('reserved','invoking','response_ready','delivering','delivered','delivery_failed','failed')),
            request_digest TEXT, response_digest TEXT, response_text TEXT, delivery_key TEXT UNIQUE,
            target_json TEXT, delivery_event_id TEXT, sanitized_failure TEXT,
            reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, invoking_at TEXT,
            response_ready_at TEXT, delivering_at TEXT, delivered_at TEXT, delivery_failed_at TEXT, failed_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id))""",
    )
    await _migrate_v4_to_v5(db)


async def _migrate_v1_to_v2(db: aiosqlite.Connection) -> None:
    statements = (
        "ALTER TABLE source_events ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'",
        "ALTER TABLE source_events ADD COLUMN request_digest TEXT",
        "ALTER TABLE source_events ADD COLUMN response_digest TEXT",
        "ALTER TABLE source_events ADD COLUMN sanitized_failure TEXT",
        "ALTER TABLE source_events ADD COLUMN reserved_at TEXT",
        "ALTER TABLE source_events ADD COLUMN invoking_at TEXT",
        "ALTER TABLE source_events ADD COLUMN failed_at TEXT",
        "UPDATE source_events SET reserved_at = accepted_at WHERE reserved_at IS NULL",
    )
    for statement in statements:
        await db.execute(statement)


async def _migrate_v2_to_v3(db: aiosqlite.Connection) -> None:
    """Rebuild the checked lifecycle table and preserve old accepted rows as delivered facts."""
    await db.execute("ALTER TABLE sessions ADD COLUMN last_used_at TEXT")
    await db.execute("UPDATE sessions SET last_used_at = created_at WHERE last_used_at IS NULL")
    await db.execute("ALTER TABLE source_events RENAME TO source_events_v2")
    await db.execute(
        """CREATE TABLE source_events (
            source_event_id TEXT PRIMARY KEY, runtime_key TEXT NOT NULL, room_id TEXT NOT NULL,
            thread_id TEXT NOT NULL, session_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN
                ('reserved','invoking','response_ready','delivering','delivered','delivery_failed','failed')),
            request_digest TEXT, response_digest TEXT, response_text TEXT, delivery_key TEXT UNIQUE,
            delivery_event_id TEXT, sanitized_failure TEXT,
            reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, invoking_at TEXT,
            response_ready_at TEXT, delivering_at TEXT, delivered_at TEXT, delivery_failed_at TEXT, failed_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id))""",
    )
    await db.execute(
        """INSERT INTO source_events (
            source_event_id,runtime_key,room_id,thread_id,session_id,status,request_digest,response_digest,
            response_text,delivery_key,sanitized_failure,reserved_at,invoking_at,response_ready_at,delivered_at,failed_at)
        SELECT source_event_id,runtime_key,room_id,thread_id,session_id,
            CASE status WHEN 'accepted' THEN 'delivered' ELSE status END,
            request_digest,response_digest,'',('mrb-delivery-' || lower(hex(randomblob(16)))),sanitized_failure,
            COALESCE(reserved_at,CURRENT_TIMESTAMP),invoking_at,accepted_at,accepted_at,failed_at
        FROM source_events_v2""",
    )
    await db.execute("DROP TABLE source_events_v2")


async def _migrate_v3_to_v4(db: aiosqlite.Connection) -> None:
    """Add persisted canonical Matrix targets; legacy unsettled rows stay quarantined."""
    await db.execute("ALTER TABLE source_events ADD COLUMN target_json TEXT")


async def _migrate_v4_to_v5(db: aiosqlite.Connection) -> None:
    """Add content-free, reconnectable lifecycle progress."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS runtime_state_event (
            source_event_id TEXT NOT NULL REFERENCES source_events(source_event_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL CHECK(sequence > 0),
            phase TEXT NOT NULL CHECK(phase IN
                ('reserved','invoking','response_ready','delivering','delivered','delivery_failed','failed')),
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(source_event_id,sequence))""",
    )


def _delivery_key(source_event_id: str) -> str:
    return f"mrb-delivery-{hashlib.sha256(source_event_id.encode()).hexdigest()}"


def _session_id(identity: RuntimeIdentity, scope: ConversationScope) -> str:
    canonical = "\x1f".join((identity.key, scope.room_id, scope.thread_id or _ROOM_SCOPE))
    return f"mrb1_{hashlib.sha256(canonical.encode()).hexdigest()}"


def validate_database(path: Path) -> None:
    """Raise if SQLite reports corruption; useful before rollback/export."""
    with sqlite3.connect(path) as db:
        result = db.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"runtime bridge database integrity check failed: {result!r}")
