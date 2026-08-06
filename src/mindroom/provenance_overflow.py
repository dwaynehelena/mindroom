"""Overflow SQLite store for Hermes-native memory records exceeding the native ceiling.

Tier 2 in the provenance memory fabric: stores full content that exceeds the
raised Hermes native ceiling (50,000 chars; the holographic SQLite store has no
inherent TEXT limit). The HermesMemoryHandler stores a compact reference
pointer in Hermes (Tier 1) and the full content here (Tier 2).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite


class ProvenanceOverflowError(RuntimeError):
    """The overflow store rejected or could not find a record."""


class ProvenanceOverflowStore:
    """SQLite-backed overflow store for large Hermes memory content.

    Schema
    ------
    provenance_records:
        id            TEXT PRIMARY KEY  — memory_id of the portable record
        content_hash  TEXT NOT NULL     — sha256 hex digest of content_json
        content_json  TEXT NOT NULL     — full JSON payload (unlimited size)
        created_at    TEXT NOT NULL     — ISO-8601 UTC timestamp
        updated_at    TEXT NOT NULL     — ISO-8601 UTC timestamp
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def path(self) -> str:
        """The on-disk path of this overflow store (for reference pointers)."""
        return self._path

    async def open(self) -> None:
        """Open the database and create the schema if needed."""
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS provenance_records (
              id            TEXT PRIMARY KEY,
              content_hash  TEXT NOT NULL,
              content_json  TEXT NOT NULL,
              created_at    TEXT NOT NULL,
              updated_at    TEXT NOT NULL
            );
            """,
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def store(self, memory_id: str, payload: dict[str, Any]) -> str:
        """Store a full payload in the overflow store.

        Returns the sha256 content digest for use in the reference pointer.
        """
        content_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        content_hash = hashlib.sha256(content_json.encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        await self._required_db().execute(
            "INSERT OR REPLACE INTO provenance_records(id, content_hash, content_json, created_at, updated_at) "
            "VALUES (?, ?, ?, "
            "  COALESCE((SELECT created_at FROM provenance_records WHERE id=?), ?), "
            "  ?"
            ")",
            (memory_id, content_hash, content_json, memory_id, now, now),
        )
        await self._required_db().commit()
        return content_hash

    async def fetch(self, memory_id: str) -> dict[str, Any] | None:
        """Retrieve the full payload for a memory_id, or None if not found."""
        row = await (
            await self._required_db().execute(
                "SELECT content_json FROM provenance_records WHERE id=?",
                (memory_id,),
            )
        ).fetchone()
        if row is None:
            return None
        return json.loads(str(row[0]))

    async def delete(self, memory_id: str) -> bool:
        """Remove an overflow record. Returns True if a row was deleted."""
        cursor = await self._required_db().execute(
            "DELETE FROM provenance_records WHERE id=?",
            (memory_id,),
        )
        await self._required_db().commit()
        return cursor.rowcount > 0

    async def has(self, memory_id: str) -> bool:
        """Check if a memory_id exists in the overflow store."""
        row = await (
            await self._required_db().execute(
                "SELECT 1 FROM provenance_records WHERE id=?",
                (memory_id,),
            )
        ).fetchone()
        return row is not None

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "overflow store is not open"
            raise ProvenanceOverflowError(message)
        return self._db