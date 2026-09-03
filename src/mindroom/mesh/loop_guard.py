"""Loop prevention for the Agent Mesh Gateway (pure logic, no external deps).

``MeshLoopGuard`` protects the mesh from message loops caused by cycles in
worker routing (e.g. A->B->A->B->...).  It provides two independent guards:

1. **Per-message hop counting / TTL** — each message carries a ``hop_count``
   and a ``trace`` of the workers it has traversed.  A message whose hop count
   exceeds the configured ``max_hops``, or whose age exceeds ``ttl_seconds``,
   is dropped.

2. **Per-(source, target) recent-message dedup** — a sliding window of recent
   ``(source_worker_id, target_worker_id)`` edges.  If the same directed edge
   is seen too frequently within a short window it is treated as a duplicate
   echo and dropped.

The guard is purely additive: when disabled (the default), ``check`` returns a
benign result and the gateway behaves exactly as it does today.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field

from mindroom.mesh.models import MeshMessage

__all__ = [
    "MeshLoopError",
    "MeshLoopGuard",
    "MeshLoopVerdict",
]

# Env var that enables mesh loop prevention.  Default OFF keeps the gateway's
# route_message behavior identical to today.
MESH_LOOPGUARD_ENV = "MINDROOM_MESH_LOOPGUARD"

# Number of distinct recent edges to remember for the dedup sliding window.
_DEDUP_WINDOW_SIZE = 64
# Maximum repeats of the same directed edge within the window before it is
# flagged as a duplicate echo.
_DEDUP_MAX_REPEATS = 1


class MeshLoopError(RuntimeError):
    """Raised when a mesh message is dropped by loop prevention."""

    def __init__(self, *, reason: str, drop_kind: str, worker_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.drop_kind = drop_kind
        self.worker_id = worker_id


@dataclass(frozen=True, slots=True)
class MeshLoopVerdict:
    """Result of consulting the loop guard for one message."""

    allowed: bool
    drop_kind: str | None = None
    reason: str | None = None

    @property
    def dropped(self) -> bool:
        """Return whether the message should be dropped."""
        return not self.allowed

    @classmethod
    def allow(cls) -> MeshLoopVerdict:
        """Return an allow verdict."""
        return cls(allowed=True)

    @classmethod
    def drop(cls, *, drop_kind: str, reason: str) -> MeshLoopVerdict:
        """Return a drop verdict with a drop kind and reason."""
        return cls(allowed=False, drop_kind=drop_kind, reason=reason)


@dataclass
class MeshLoopGuard:
    """Per-message hop/TTL and per-(source,target) dedup guard for the mesh.

    Pure in-memory logic with no external dependencies.  When ``enabled`` is
    False (the default) ``check`` always allows the message through, so the
    gateway's default path is unchanged.
    """

    enabled: bool = False
    max_hops: int = 8
    ttl_seconds: int = 300
    now: float | None = field(default=None, repr=False)  # injectable clock for tests
    _recent_edges: deque[tuple[str, str, str]] = field(default_factory=deque, repr=False)

    def _clock(self) -> float:
        return self.now if self.now is not None else time.time()

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        max_hops: int = 8,
        ttl_seconds: int = 300,
    ) -> MeshLoopGuard:
        """Build a guard enabled when ``MINDROOM_MESH_LOOPGUARD`` is truthy.

        Default is OFF: when the env var is absent, empty, or set to a
        falsy value, the returned guard is disabled and the gateway behaves
        exactly as today.
        """
        source = env if env is not None else os.environ
        raw = (source.get(MESH_LOOPGUARD_ENV) or "").strip().lower()
        enabled = raw in ("1", "true", "yes", "on", "gateway_only", "enabled")
        return cls(
            enabled=enabled,
            max_hops=max_hops,
            ttl_seconds=ttl_seconds,
        )

    def check(self, message: MeshMessage) -> MeshLoopVerdict:
        """Evaluate one message against the loop guard.

        When disabled, always allows.  Otherwise applies hop-count, TTL, and
        duplicate-edge checks in order.  A dropped message is recorded in the
        recent-edge window so repeated echoes keep being detected.
        """
        if not self.enabled:
            return MeshLoopVerdict.allow()

        # 1. Hop-count exhaustion.
        if message.hop_count >= self.max_hops:
            return MeshLoopVerdict.drop(
                drop_kind="loop",
                reason=f"hop count {message.hop_count} reached max_hops {self.max_hops}",
            )

        # 2. TTL expiry.
        if message.created_at + self.ttl_seconds < self._clock():
            return MeshLoopVerdict.drop(
                drop_kind="loop",
                reason="message TTL expired",
            )

        # 3. Duplicate-edge detection.  A message is a duplicate echo when the
        # same logical message (correlation_id) traverses the same directed
        # edge more than once within the recent window.  Distinct messages
        # (different correlation_id) between the same pair are preserved.
        edge_key = (message.source_worker_id, message.target_worker_id, message.correlation_id)
        repeats = sum(1 for seen in self._recent_edges if seen == edge_key)
        # Record the edge regardless so a future echo is also caught.
        self._record_edge(edge_key)
        if repeats >= _DEDUP_MAX_REPEATS:
            return MeshLoopVerdict.drop(
                drop_kind="duplicate",
                reason=f"duplicate {message.source_worker_id}->{message.target_worker_id} echo detected in recent window",
            )

        return MeshLoopVerdict.allow()

    def _record_edge(self, edge: tuple[str, str, str]) -> None:
        self._recent_edges.append(edge)
        while len(self._recent_edges) > _DEDUP_WINDOW_SIZE:
            self._recent_edges.popleft()

    def reset(self) -> None:
        """Clear the recent-edge window (e.g. after a cycle is broken)."""
        self._recent_edges.clear()