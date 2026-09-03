"""Tamper-evident, redacted event recorder for cross-runtime execution evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import aiosqlite

from mindroom.constants import tracking_dir
from mindroom.redaction import redact_sensitive_data

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

    from mindroom.constants import RuntimePaths

EventKind = Literal[
    "message",
    "model_call",
    "tool_call",
    "handoff",
    "approval",
    "memory_write",
    "runtime_state",
]


class FlightRecorderError(RuntimeError):
    """A recorder integrity or replay-safety invariant failed."""


@dataclass(frozen=True, slots=True)
class FlightRecord:
    """One content-redacted, hash-chained execution event."""

    sequence: int
    run_id: str
    kind: EventKind
    occurred_at: datetime
    payload: JsonValue
    side_effect: bool
    duration_ms: int | None
    cost_microunits: int | None
    previous_hash: str
    record_hash: str


class FlightRecorder:
    """Serialize append-only trace writes and permit replay of pure events only."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the recorder database and create its immutable event schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS flight_record (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              side_effect INTEGER NOT NULL CHECK(side_effect IN (0,1)),
              duration_ms INTEGER,
              cost_microunits INTEGER,
              previous_hash TEXT NOT NULL,
              record_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS flight_record_run_sequence
              ON flight_record(run_id, sequence);
            CREATE TRIGGER IF NOT EXISTS flight_record_no_update
              BEFORE UPDATE ON flight_record BEGIN SELECT RAISE(ABORT, 'flight records are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS flight_record_no_delete
              BEFORE DELETE ON flight_record BEGIN SELECT RAISE(ABORT, 'flight records are immutable'); END;
            """,
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the recorder."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def append(
        self,
        *,
        run_id: str,
        kind: EventKind,
        payload: JsonValue,
        side_effect: bool,
        occurred_at: datetime | None = None,
        duration_ms: int | None = None,
        cost_microunits: int | None = None,
    ) -> FlightRecord:
        """Redact and append one record to the global integrity chain."""
        if (
            not run_id
            or (duration_ms is not None and duration_ms < 0)
            or (cost_microunits is not None and cost_microunits < 0)
        ):
            message = "invalid flight record identity, duration, or cost"
            raise FlightRecorderError(message)
        timestamp = _utc(occurred_at or datetime.now(UTC))
        safe_payload = redact_sensitive_data(payload)
        payload_json = _canonical_json(safe_payload)
        async with self._lock:
            db = self._required_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                latest = await (
                    await db.execute("SELECT sequence,record_hash FROM flight_record ORDER BY sequence DESC LIMIT 1")
                ).fetchone()
                sequence = int(latest[0]) + 1 if latest else 1
                previous_hash = str(latest[1]) if latest else "0" * 64
                digest_input = {
                    "cost_microunits": cost_microunits,
                    "duration_ms": duration_ms,
                    "kind": kind,
                    "occurred_at": timestamp.isoformat(),
                    "payload": safe_payload,
                    "previous_hash": previous_hash,
                    "run_id": run_id,
                    "sequence": sequence,
                    "side_effect": side_effect,
                }
                record_hash = hashlib.sha256(_canonical_json(digest_input).encode("utf-8")).hexdigest()
                await db.execute(
                    "INSERT INTO flight_record VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        sequence,
                        run_id,
                        kind,
                        timestamp.isoformat(),
                        payload_json,
                        int(side_effect),
                        duration_ms,
                        cost_microunits,
                        previous_hash,
                        record_hash,
                    ),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return FlightRecord(
            sequence,
            run_id,
            kind,
            timestamp,
            json.loads(payload_json),
            side_effect,
            duration_ms,
            cost_microunits,
            previous_hash,
            record_hash,
        )

    async def replayable(self, run_id: str) -> tuple[FlightRecord, ...]:
        """Return verified pure events; reject a run containing any side effect."""
        rows = await (
            await self._required_db().execute(
                "SELECT sequence,run_id,kind,occurred_at,payload_json,side_effect,duration_ms,cost_microunits,"
                "previous_hash,record_hash FROM flight_record WHERE run_id=? ORDER BY sequence",
                (run_id,),
            )
        ).fetchall()
        records = tuple(_row_to_record(row) for row in rows)
        if any(record.side_effect for record in records):
            message = "side-effecting runs are not replayable"
            raise FlightRecorderError(message)
        await self.verify_chain()
        return records

    async def records(self, run_id: str) -> tuple[FlightRecord, ...]:
        """Return verified records for audit without claiming replay safety."""
        rows = await (
            await self._required_db().execute(
                "SELECT sequence,run_id,kind,occurred_at,payload_json,side_effect,duration_ms,cost_microunits,"
                "previous_hash,record_hash FROM flight_record WHERE run_id=? ORDER BY sequence",
                (run_id,),
            )
        ).fetchall()
        await self.verify_chain()
        return tuple(_row_to_record(row) for row in rows)

    async def verify_chain(self) -> None:
        """Verify every stored record against the global append-only hash chain."""
        rows = await (
            await self._required_db().execute(
                "SELECT sequence,run_id,kind,occurred_at,payload_json,side_effect,duration_ms,cost_microunits,"
                "previous_hash,record_hash FROM flight_record ORDER BY sequence",
            )
        ).fetchall()
        previous_hash = "0" * 64
        for row in rows:
            record = _row_to_record(row)
            digest_input = {
                "cost_microunits": record.cost_microunits,
                "duration_ms": record.duration_ms,
                "kind": record.kind,
                "occurred_at": record.occurred_at.isoformat(),
                "payload": record.payload,
                "previous_hash": previous_hash,
                "run_id": record.run_id,
                "sequence": record.sequence,
                "side_effect": record.side_effect,
            }
            expected = hashlib.sha256(_canonical_json(digest_input).encode("utf-8")).hexdigest()
            if record.previous_hash != previous_hash or record.record_hash != expected:
                message = f"flight record integrity failed at sequence {record.sequence}"
                raise FlightRecorderError(message)
            previous_hash = record.record_hash

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "flight recorder is not open"
            raise RuntimeError(message)
        return self._db


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "flight record timestamps must include a timezone"
        raise FlightRecorderError(message)
    return value.astimezone(UTC)


def _row_to_record(row: tuple[object, ...]) -> FlightRecord:
    return FlightRecord(
        sequence=int(row[0]),
        run_id=str(row[1]),
        kind=str(row[2]),  # type: ignore[arg-type]
        occurred_at=datetime.fromisoformat(str(row[3])),
        payload=json.loads(str(row[4])),
        side_effect=bool(row[5]),
        duration_ms=int(row[6]) if row[6] is not None else None,
        cost_microunits=int(row[7]) if row[7] is not None else None,
        previous_hash=str(row[8]),
        record_hash=str(row[9]),
    )


async def record_flight_event(
    runtime_paths: RuntimePaths,
    *,
    run_id: str,
    kind: EventKind,
    payload: JsonValue,
    side_effect: bool,
    duration_ms: int | None = None,
    cost_microunits: int | None = None,
) -> FlightRecord:
    """Append through a short-lived connection so runtime call sites own no lifecycle state."""
    recorder = FlightRecorder(tracking_dir(runtime_paths) / "flight_recorder.db")
    await recorder.open()
    try:
        return await recorder.append(
            run_id=run_id,
            kind=kind,
            payload=payload,
            side_effect=side_effect,
            duration_ms=duration_ms,
            cost_microunits=cost_microunits,
        )
    finally:
        await recorder.close()


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        message = "flight record payload is not finite JSON"
        raise FlightRecorderError(message) from exc
