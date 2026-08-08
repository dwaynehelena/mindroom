"""Mail signal collector (DEFERRED).

Implements the ``BaseCollector`` interface against documented deterministic
fake data. The real Gmail integration is DEFERRED: no Gmail credentials were
found in the runtime environment, so this collector reports a deterministic
placeholder payload and marks the source as deferred with evidence.
"""

from __future__ import annotations

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult

# Deterministic fake data documented for the deferred mail source.
_DEFERRED_MAIL_DATA: dict[str, object] = {
    "deferred": True,
    "reason": "no gmail credentials found",
    "unread_count": 0,
    "top_subjects": [],
}


class MailCollector(BaseCollector):
    """Report the deferred mail signal with deterministic placeholder data."""

    name = "mail"

    def collect(self) -> CollectResult:
        return CollectResult(self.name, True, data=dict(_DEFERRED_MAIL_DATA))