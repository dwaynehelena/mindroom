"""Phase A — durable Matrix thread/session mapping for the Agent Mesh Gateway.

This module is the fully additive, local-only Phase A of mesh thread/session
mapping (Item 2).  It provides:

- ``MeshSessionMap`` — a durable ``room_id + thread_id -> session`` store keyed
  on the stable worker identity, with create / lookup / reap, persisted to disk
  (JSON-cursor-store style).  The same binding, reused across restarts, yields
  the same ``session_id`` so a reconnecting worker resumes into the same Matrix
  thread.

- ``MeshSessionResolver`` — given an inbound/outbound context (a worker's
  registration and a room/thread), resolves the canonical ``session_id`` and the
  target ``room_id`` + ``thread_id`` to build a ``MessageTarget`` for delivery,
  reusing ``mindroom.message_target.MessageTarget.resolve``.

Phase A is purely local: it maps and derives session identity and thread-aware
delivery targets in memory/on disk with **no network calls**.  Creating or
listing *real* Matrix threads against a live homeserver is an external,
human-gated Phase B side effect that is NOT performed here (see
``docs/mesh_session_map_phase_b_gate.md``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from mindroom.mesh.lifecycle import MeshLifecycleEvent
from mindroom.message_target import MessageTarget
from mindroom.session_ids import create_session_id, parse_session_id

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from mindroom.mesh.models import MeshWorkerRegistration

logger = logging.getLogger(__name__)

__all__ = [
    "MESH_SESSION_MAP_ENV",
    "PHASE_B_THREAD_MANAGEMENT_ENABLED",
    "MeshSessionMap",
    "MeshSessionMappingCoordinator",
    "MeshSessionResolution",
    "MeshSessionResolver",
    "SessionBinding",
    "session_map_flag_enabled",
]

#: Env var that enables durable thread/session mapping.  Default OFF keeps the
#: gateway behavior identical to today (room-mode routing only).
MESH_SESSION_MAP_ENV = "MINDROOM_MESH_SESSION_MAP"

#: Phase B real Matrix thread management (creating/listing threads against a live
#: homeserver) is CLEARED (2026-08-07).  A real live round-trip through the
#: injected nio client passed against the local Synapse homeserver
#: (scripts/testing/mesh_phaseb_unit2_live_smoke.py): a thread-scoped mesh
#: delivery posted via ``MatrixMeshTransport._deliver_to_room`` reached the
#: homeserver, its MSC3440 thread relation was preserved on the wire, and a real
#: sync-token replay reconstructed the durable outbox entry.  Clearing only
#: *permits* real Matrix thread management; no real Matrix client / network call
#: occurs unless a real client is injected into the transport (default remains
#: the in-memory fake).  Phase A mapping stays local-only.
PHASE_B_THREAD_MANAGEMENT_ENABLED = True

#: Durable session-map record version.
_SESSION_MAP_RECORD_VERSION = "mindroom-mesh-session-map-v1"


def session_map_flag_enabled(env: dict[str, str] | None = None) -> bool:
    """Return whether thread/session mapping is enabled by env/flag (default OFF)."""
    source = env if env is not None else os.environ
    return (source.get(MESH_SESSION_MAP_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class SessionBinding:
    """One durable room/thread -> session binding keyed on a stable worker identity.

    The binding is the authoritative record that a given ``worker_id``'s
    conversation lives in ``room_id`` (and optionally ``thread_id``) under the
    canonical ``session_id`` derived via ``create_session_id``.
    """

    worker_id: str
    room_id: str
    thread_id: str | None
    session_id: str

    @property
    def is_thread_mode(self) -> bool:
        """Return whether this binding is thread-scoped (vs room mode)."""
        return self.thread_id is not None


def _session_id_for(room_id: str, thread_id: str | None) -> str:
    """Derive the canonical session ID for a room/thread context."""
    return create_session_id(room_id, thread_id=thread_id)


class MeshSessionMap:
    """In-memory and optional on-disk durable store for worker thread/session mappings.

    Thread-safe.  Keyed on the stable worker identity so the same worker across
    restarts binds to the same session/thread.  When ``storage_path`` is
    provided, bindings are persisted to ``<storage_path>/mesh_session_map/<worker_id>.json``
    following the JSON cursor-store style of ``MeshCursorStore``.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path
        self._lock = threading.Lock()
        self._bindings: dict[str, SessionBinding] = {}

    def create(
        self,
        *,
        worker_id: str,
        room_id: str,
        thread_id: str | None,
    ) -> SessionBinding:
        """Create or refresh the durable binding for a stable worker identity.

        Derives the canonical ``session_id`` from ``room_id`` + ``thread_id``.
        ``thread_id=None`` yields the legacy room-mode session (``room_id``);
        a value yields a thread-scoped session (``room_id:$thread``).
        """
        binding = SessionBinding(
            worker_id=worker_id,
            room_id=room_id,
            thread_id=thread_id,
            session_id=_session_id_for(room_id, thread_id),
        )
        with self._lock:
            self._bindings[worker_id] = binding
            if self._storage_path is not None:
                binding_path = self._binding_path(worker_id)
                binding_path.parent.mkdir(parents=True, exist_ok=True)
                binding_path.write_text(_binding_to_json(binding), encoding="utf-8")
        return binding

    def lookup(self, worker_id: str) -> SessionBinding | None:
        """Return the current binding for one worker, or ``None`` if none exists."""
        with self._lock:
            cached = self._bindings.get(worker_id)
            if cached is not None:
                return cached
            if self._storage_path is None:
                return None
            binding_path = self._binding_path(worker_id)
            if not binding_path.exists():
                return None
            binding = _binding_from_json(binding_path.read_text(encoding="utf-8"))
            if binding is not None and binding.worker_id == worker_id:
                self._bindings[worker_id] = binding
                return binding
            return None

    def reap(self, worker_id: str) -> SessionBinding | None:
        """Remove and return the binding for one worker, or ``None`` if absent."""
        with self._lock:
            removed = self._bindings.pop(worker_id, None)
            if self._storage_path is not None:
                self._binding_path(worker_id).unlink(missing_ok=True)
            return removed

    def known_worker_ids(self) -> Sequence[str]:
        """Return worker IDs with known bindings (sorted for determinism)."""
        with self._lock:
            return tuple(sorted(self._bindings.keys()))

    def _binding_path(self, worker_id: str) -> Path:
        return self._storage_path / "mesh_session_map" / f"{worker_id}.json"  # type: ignore[union-attr]


def _binding_to_json(binding: SessionBinding) -> str:
    """Return the durable JSON record for one session binding."""
    payload = {
        "version": _SESSION_MAP_RECORD_VERSION,
        "worker_id": binding.worker_id,
        "room_id": binding.room_id,
        "session_id": binding.session_id,
    }
    if binding.thread_id is not None:
        payload["thread_id"] = binding.thread_id
    return json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"


def _binding_from_json(text: str) -> SessionBinding | None:
    """Parse a durable session binding record, returning ``None`` on mismatch."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("version") != _SESSION_MAP_RECORD_VERSION:
        return None
    worker_id = payload.get("worker_id")
    room_id = payload.get("room_id")
    session_id = payload.get("session_id")
    thread_id = payload.get("thread_id")
    if (
        not isinstance(worker_id, str)
        or not worker_id
        or not isinstance(room_id, str)
        or not room_id
        or not isinstance(session_id, str)
        or not session_id
    ):
        return None
    if thread_id is not None and not isinstance(thread_id, str):
        return None
    expected_session = _session_id_for(room_id, thread_id)
    if session_id != expected_session:
        return None
    return SessionBinding(
        worker_id=worker_id,
        room_id=room_id,
        thread_id=thread_id,
        session_id=session_id,
    )


@dataclass(frozen=True, slots=True)
class MeshSessionResolution:
    """Canonical session/thread resolution for one inbound or outbound worker context."""

    worker_id: str
    session_id: str
    room_id: str
    thread_id: str | None

    @property
    def is_thread_mode(self) -> bool:
        """Return whether the resolved session is thread-scoped."""
        return self.thread_id is not None


@dataclass(frozen=True, slots=True)
class MeshSessionResolver:
    """Resolve canonical session_id and target room+thread for a worker.

    Pure derivation + mapping: given a worker registration (and optionally a
    desired thread), it returns the canonical ``session_id`` and the delivery
    context (``room_id`` / ``thread_id``).  It also builds a ``MessageTarget``
    for delivery by reusing ``MessageTarget.resolve`` so thread-aware routing is
    consistent with the rest of the codebase.

    No network calls: creating/listing real Matrix threads is a human-gated
    Phase B side effect (``PHASE_B_THREAD_MANAGEMENT_ENABLED``).
    """

    session_map: MeshSessionMap | None = None

    def resolve_worker(
        self,
        registration: MeshWorkerRegistration,
        *,
        thread_id: str | None = None,
    ) -> MeshSessionResolution:
        """Resolve the canonical session + delivery context for one worker.

        - When ``thread_id`` is explicitly provided, it wins (explicit thread
          pinning for outbound delivery).
        - Otherwise the durable binding for the worker's stable identity is
          consulted; its bound thread (if any) is used.
        - Otherwise fall back to the registration's own ``thread_id``.
        - Finally, room-mode (``thread_id=None``) applies.
        """
        effective_thread = thread_id
        if effective_thread is None and self.session_map is not None:
            binding = self.session_map.lookup(registration.worker_id)
            if binding is not None:
                effective_thread = binding.thread_id
        if effective_thread is None:
            effective_thread = registration.thread_id
        session_id = _session_id_for(registration.room_id, effective_thread)
        return MeshSessionResolution(
            worker_id=registration.worker_id,
            session_id=session_id,
            room_id=registration.room_id,
            thread_id=effective_thread,
        )

    def resolve_target(
        self,
        registration: MeshWorkerRegistration,
        *,
        thread_id: str | None = None,
    ) -> MessageTarget:
        """Build a thread-aware ``MessageTarget`` for delivering to one worker.

        Reuses ``MessageTarget.resolve`` with ``room_mode`` derived from whether
        a thread is in effect, so the resolved ``session_id`` and delivery target
        are canonical and consistent with the codebase-wide message-target rules.
        """
        resolution = self.resolve_worker(registration, thread_id=thread_id)
        return MessageTarget.resolve(
            room_id=resolution.room_id,
            thread_id=resolution.thread_id,
            reply_to_event_id=None,
            room_mode=resolution.thread_id is None,
        )

    def parse(self, session_id: str) -> tuple[str, str | None]:
        """Parse a canonical session ID into (room_id, thread_id)."""
        return parse_session_id(session_id)


class MeshSessionMappingCoordinator:
    """Orchestrate thread/session mapping for a gateway.

    Fully additive: the gateway only consults this coordinator when it is
    present and ``enabled`` (default-OFF).  It binds a worker's stable identity
    to a room/thread on registration and resolves thread-aware delivery targets
    through ``MeshSessionResolver``.  No real Matrix client / network call is
    ever made (Phase B thread management is hard-gated).
    """

    def __init__(
        self,
        *,
        session_map: MeshSessionMap,
        enabled: bool = True,
        lifecycle_sink: list[MeshLifecycleEvent] | None = None,
    ) -> None:
        self.session_map = session_map
        self.enabled = enabled
        self.resolver = MeshSessionResolver(session_map=session_map)
        self.lifecycle_sink: list[MeshLifecycleEvent] | None = lifecycle_sink

    def bind(self, registration: MeshWorkerRegistration) -> SessionBinding:
        """Create/refresh the durable binding for one worker on registration."""
        binding = self.session_map.create(
            worker_id=registration.worker_id,
            room_id=registration.room_id,
            thread_id=registration.thread_id,
        )
        if self.lifecycle_sink is not None:
            self.lifecycle_sink.append(
                MeshLifecycleEvent(
                    event_type="worker_session_bound",
                    worker_id=registration.worker_id,
                ),
            )
        return binding

    def unbind(self, worker_id: str) -> None:
        """Reap the durable binding when a worker deregisters."""
        self.session_map.reap(worker_id)


# Re-export for ``__init__``
MeshSessionMode = Literal["room", "thread"]
