"""Calendar signal collector (DEFERRED).

Implements the ``BaseCollector`` interface against documented deterministic
fake data. The real Google Calendar integration is DEFERRED: no
``google_calendar`` credentials were found in the runtime environment, so this
collector reports a deterministic placeholder payload and marks the source as
deferred with evidence.
"""

from __future__ import annotations

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult

# Deterministic fake data documented for the deferred calendar source.
_DEFERRED_CALENDAR_DATA: dict[str, object] = {
    "deferred": True,
    "reason": "no google_calendar credentials found",
    "upcoming_events": [],
}


class CalendarCollector(BaseCollector):
    """Report the deferred calendar signal with deterministic placeholder data."""

    name = "calendar"

    def collect(self) -> CollectResult:
        return CollectResult(self.name, True, data=dict(_DEFERRED_CALENDAR_DATA))