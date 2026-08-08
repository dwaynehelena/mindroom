"""Base contract for ops-autopilot signal collectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class CollectResult:
    """One collector's output."""

    source: str
    ok: bool
    data: object = None
    error: str | None = None
    collected_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def render(self) -> str:
        """Human-readable one-line status for this source."""
        status = "OK" if self.ok else "ERROR"
        head = f"• {self.source}: {status}"
        if not self.ok:
            head += f" — {self.error or 'collect failed'}"
        return head


class BaseCollector(ABC):
    """A named, dependency-injected signal source."""

    name: str

    @abstractmethod
    def collect(self) -> CollectResult:
        """Run synchronously and return a bounded, renderable result."""