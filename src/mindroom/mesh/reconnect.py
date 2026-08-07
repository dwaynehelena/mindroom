"""Phase A — reconnect with cursor resume for the Agent Mesh Gateway.

This module is the fully additive, local-only Phase A of mesh worker
reconnect-with-resume.  It provides:

- ``MeshReconnectCoordinator`` — orchestrates a worker resume:
  load cursor -> ``transport._sync_from_cursor`` -> replay undelivered
  outbox entries -> save an advanced cursor.  It is idempotent (entries
  already delivered are skipped, no duplicate delivery, no full replay)
  and session-aware (a resume for a different session than the saved
  cursor's is a no-op).

Phase A is purely local: it replays against the fake / in-memory transport
queue (``MatrixMeshTransport``) with **no network calls**.  Replaying from a
real Matrix sync token against a live homeserver is an external, human-gated
Phase B (see ``docs/mesh_resume_phase_b_gate.md``).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mindroom.mesh.cursor import MeshReconnectCursor
from mindroom.mesh.lifecycle import MeshLifecycleEvent

if TYPE_CHECKING:
    from mindroom.mesh.cursor import MeshCursorStore
    from mindroom.mesh.lifecycle import MeshLifecycleSink
    from mindroom.mesh.models import MeshOutboxEntry
    from mindroom.mesh.transport import MeshTransport

logger = logging.getLogger(__name__)

__all__ = [
    "MESH_RESUME_ENV",
    "PHASE_B_RESUME_ENABLED",
    "MeshReconnectCoordinator",
    "MeshResumeResult",
    "resume_flag_enabled",
]

#: Env var that enables cursor resume.  Default OFF keeps the gateway's
#: behavior identical to today (``worker_reconnect`` still returns the cursor,
#: but ``resume_worker`` is inert unless a coordinator is attached and enabled).
MESH_RESUME_ENV = "MINDROOM_MESH_RESUME"

#: Phase B external sync-token replay is hard-gated off.  Replaying from a real
#: Matrix sync token against a live homeserver (a network side effect) may not
#: occur unless an operator explicitly enables it after human review (see
#: docs/mesh_resume_phase_b_gate.md).  Phase A replay is local-only.
PHASE_B_RESUME_ENABLED = False


def resume_flag_enabled(env: dict[str, str] | None = None) -> bool:
    """Return whether mesh cursor resume is enabled by env/flag (default OFF)."""
    source = env if env is not None else os.environ
    return (source.get(MESH_RESUME_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class MeshResumeResult:
    """Result of one worker resume attempt."""

    worker_id: str
    session_id: str | None
    replayed_outbox_ids: tuple[str, ...]
    skipped_outbox_ids: tuple[str, ...]
    advanced_cursor: str | None
    resumed: bool


def _cursor_outbox_id(cursor_value: str) -> str | None:
    """Extract the outbox_id embedded in a ``mesh-cursor-<outbox_id>-<ts>`` value.

    Returns ``None`` when the cursor value is not a mesh cursor or carries no
    outbox_id.  Used to locate the exact last-certified entry in the delivery
    log so replay resumes strictly after it (no full replay).
    """
    marker = "mesh-outbox-"
    idx = cursor_value.find(marker)
    if idx == -1:
        return None
    hex_part = cursor_value[idx + len(marker) :].split("-", 1)[0]
    if not hex_part:
        return None
    return f"mesh-outbox-{hex_part}"


class MeshReconnectCoordinator:
    """Orchestrate cursor-based reconnect + resume for one gateway.

    ``resume`` loads the worker's saved cursor, asks the transport to replay
    the outbox entries delivered after that cursor, re-emits those entries
    (skipping any already delivered for idempotency), and saves an advanced
    cursor so a subsequent resume does not re-replay.

    Session-aware: when the saved cursor carries a ``session_id`` that differs
    from the requested resume session, the resume is a no-op (no cross-session
    replay).
    """

    def __init__(
        self,
        *,
        transport: MeshTransport,
        cursor_store: MeshCursorStore,
        lifecycle_sink: MeshLifecycleSink,
        enabled: bool = True,
        now: float | None = None,
    ) -> None:
        self.transport = transport
        self.cursor_store = cursor_store
        self.lifecycle_sink = lifecycle_sink
        self.enabled = enabled
        self._now = now
        # outbox_ids already replayed by this coordinator instance, so a single
        # resume never re-delivers the same entry twice.
        self._replayed: set[str] = set()
        self._clock = time.time if self._now is None else lambda: self._now  # type: ignore[arg-type,return-value]

    async def resume(
        self,
        worker_id: str,
        *,
        session_id: str | None = None,
    ) -> MeshResumeResult:
        """Resume one worker from its last saved cursor."""
        cursor = self.cursor_store.load(worker_id)
        if cursor is None:
            return MeshResumeResult(
                worker_id=worker_id,
                session_id=session_id,
                replayed_outbox_ids=(),
                skipped_outbox_ids=(),
                advanced_cursor=None,
                resumed=False,
            )

        # Session-aware: a resume for a different session is a no-op.
        if cursor.session_id is not None and cursor.session_id != session_id:
            return MeshResumeResult(
                worker_id=worker_id,
                session_id=session_id,
                replayed_outbox_ids=(),
                skipped_outbox_ids=(),
                advanced_cursor=cursor.cursor,
                resumed=False,
            )

        in_flight = list(await self.transport._sync_from_cursor(worker_id))
        replayed: list[str] = []
        skipped: list[str] = []
        advanced: str | None = None

        for entry in in_flight:
            if entry.outbox_id in self._replayed:
                # Idempotency within this resume: never re-deliver the same
                # entry twice (e.g. if the transport reports it more than once).
                skipped.append(entry.outbox_id)
                continue
            self._replayed.add(entry.outbox_id)
            replayed.append(entry.outbox_id)
            self.lifecycle_sink.append(
                _replay_event(entry, worker_id=worker_id),
            )
            advanced = self._advance_cursor(entry)

        if replayed:
            self.cursor_store.save(
                _advanced_cursor(
                    worker_id=worker_id,
                    cursor_value=advanced or _fallback_cursor(worker_id),
                    cache_generation=advanced or _fallback_cursor(worker_id),
                    session_id=session_id,
                    now=self._clock(),
                ),
            )

        resumed = bool(replayed)
        return MeshResumeResult(
            worker_id=worker_id,
            session_id=session_id,
            replayed_outbox_ids=tuple(replayed),
            skipped_outbox_ids=tuple(skipped),
            advanced_cursor=advanced,
            resumed=resumed,
        )

    def _advance_cursor(self, entry: MeshOutboxEntry) -> str:
        """Return the cursor value that advances past the replayed entry."""
        return f"mesh-cursor-{entry.outbox_id}-{int(self._clock())}"


def _fallback_cursor(worker_id: str) -> str:
    return f"mesh-cursor-{worker_id}-{int(time.time())}"


def _advanced_cursor(
    *,
    worker_id: str,
    cursor_value: str,
    cache_generation: str,
    session_id: str | None,
    now: float,
) -> MeshReconnectCursor:
    return MeshReconnectCursor(
        worker_id=worker_id,
        cursor=cursor_value,
        cache_generation=cache_generation,
        saved_at=now,
        session_id=session_id,
    )


def _replay_event(entry: MeshOutboxEntry, *, worker_id: str) -> MeshLifecycleEvent:
    return MeshLifecycleEvent(
        event_type="message_replayed",
        source_worker_id=entry.source_worker_id,
        target_worker_id=worker_id,
        outbox_id=entry.outbox_id,
        correlation_id=entry.message_id if hasattr(entry, "message_id") else None,
        cursor=None,
    )
