"""Signal collectors for the Personal Ops Autopilot."""

from __future__ import annotations

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult
from mindroom.ops_autopilot.collectors.git import GitCollector
from mindroom.ops_autopilot.collectors.registry import CollectorRegistry, build_default_registry
from mindroom.ops_autopilot.collectors.scheduler import SchedulerCollector

__all__ = [
    "BaseCollector",
    "CollectResult",
    "CollectorRegistry",
    "GitCollector",
    "SchedulerCollector",
    "build_default_registry",
]