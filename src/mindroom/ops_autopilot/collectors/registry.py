"""Collector registry and default factory."""

from __future__ import annotations

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult
from mindroom.ops_autopilot.collectors.git import GitCollector
from mindroom.ops_autopilot.collectors.scheduler import SchedulerCollector


class CollectorRegistry:
    """Ordered collection of named collectors."""

    def __init__(self, collectors: list[BaseCollector] | None = None) -> None:
        self._collectors: list[BaseCollector] = list(collectors or [])

    def add(self, collector: BaseCollector) -> None:
        self._collectors.append(collector)

    def run_all(self) -> list[CollectResult]:
        """Run every collector and return results in registration order."""
        return [collector.collect() for collector in self._collectors]


def build_default_registry() -> CollectorRegistry:
    """Build the standard collector set for the autopilot pipeline."""
    return CollectorRegistry([GitCollector(), SchedulerCollector()])