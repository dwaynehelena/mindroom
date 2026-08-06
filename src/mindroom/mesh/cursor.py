"""Reconnect cursors for mesh transport resumption.

Builds on the existing sync-token checkpoint pattern from
``mindroom.matrix.sync_tokens`` / ``sync_certification``: the gateway persists a
cursor after each batch of messages is durably delivered, so a disconnected
worker can resume from the last certified point rather than replaying the full
timeline.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "MeshCursorStore",
    "MeshReconnectCursor",
]

_CURSOR_RECORD_VERSION = "mindroom-mesh-cursor-v1"  # noqa: S105


@dataclass(frozen=True, slots=True)
class MeshReconnectCursor:
    """A resumable delivery checkpoint for one worker."""

    worker_id: str
    cursor: str
    cache_generation: str
    saved_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        """Return the durable JSON record for this cursor."""
        payload = {
            "version": _CURSOR_RECORD_VERSION,
            "worker_id": self.worker_id,
            "cursor": self.cursor,
            "cache_generation": self.cache_generation,
            "saved_at": self.saved_at,
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> MeshReconnectCursor | None:
        """Parse a durable JSON cursor record, returning ``None`` on mismatch."""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("version") != _CURSOR_RECORD_VERSION:
            return None
        worker_id = payload.get("worker_id")
        cursor = payload.get("cursor")
        cache_generation = payload.get("cache_generation")
        saved_at = payload.get("saved_at", time.time())
        if not isinstance(worker_id, str) or not isinstance(cursor, str) or not isinstance(cache_generation, str):
            return None
        return cls(
            worker_id=worker_id,
            cursor=cursor,
            cache_generation=cache_generation,
            saved_at=float(saved_at),
        )


class MeshCursorStore:
    """In-memory and optional on-disk cursor store for mesh workers.

    Thread-safe.  When ``storage_path`` is provided, cursors are persisted
    to ``<storage_path>/mesh_cursors/<worker_id>.cursor`` following the same
    pattern as ``sync_tokens``.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path
        self._lock = threading.Lock()
        self._cursors: dict[str, MeshReconnectCursor] = {}

    def save(self, cursor: MeshReconnectCursor) -> None:
        """Persist one cursor checkpoint for a worker."""
        with self._lock:
            self._cursors[cursor.worker_id] = cursor
            if self._storage_path is not None:
                cursor_path = self._cursor_path(cursor.worker_id)
                cursor_path.parent.mkdir(parents=True, exist_ok=True)
                cursor_path.write_text(cursor.to_json(), encoding="utf-8")

    def load(self, worker_id: str) -> MeshReconnectCursor | None:
        """Load the last saved cursor for a worker, or ``None`` if none exists."""
        with self._lock:
            cached = self._cursors.get(worker_id)
            if cached is not None:
                return cached
            if self._storage_path is None:
                return None
            cursor_path = self._cursor_path(worker_id)
            if not cursor_path.exists():
                return None
            cursor = MeshReconnectCursor.from_json(cursor_path.read_text(encoding="utf-8"))
            if cursor is not None:
                self._cursors[worker_id] = cursor
            return cursor

    def clear(self, worker_id: str) -> None:
        """Remove one persisted cursor when a worker deregisters."""
        with self._lock:
            self._cursors.pop(worker_id, None)
            if self._storage_path is not None:
                self._cursor_path(worker_id).unlink(missing_ok=True)

    def known_worker_ids(self) -> Sequence[str]:
        """Return worker IDs with known cursors."""
        with self._lock:
            return tuple(self._cursors.keys())

    def _cursor_path(self, worker_id: str) -> Path:
        return self._storage_path / "mesh_cursors" / f"{worker_id}.cursor"  # type: ignore[union-attr]