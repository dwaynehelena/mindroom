"""Unit tests for the fail-soft mail and calendar collectors."""

from __future__ import annotations

from mindroom.ops_autopilot.collectors.calendar import CalendarCollector
from mindroom.ops_autopilot.collectors.mail import MailCollector
from mindroom.ops_autopilot.collectors.registry import build_default_registry
from mindroom.ops_autopilot.composer import compose_brief


class _FakeCredentialsManager:
    """A credentials manager stub that reports no credentials for any service."""

    def __init__(self, credentials: dict[str, dict] | None = None) -> None:
        self._credentials = credentials or {}

    def load_credentials(self, service: str) -> dict | None:
        return self._credentials.get(service)


def test_mail_collector_fails_soft_without_credentials() -> None:
    result = MailCollector(credentials_manager=_FakeCredentialsManager()).collect()
    assert result.ok is False
    assert result.source == "mail"
    assert result.error == "credentials unavailable"
    assert result.data == {"items": []}


def test_calendar_collector_fails_soft_without_credentials() -> None:
    result = CalendarCollector(credentials_manager=_FakeCredentialsManager()).collect()
    assert result.ok is False
    assert result.source == "calendar"
    assert result.error == "credentials unavailable"
    assert result.data == {"items": []}


def test_mail_collector_never_fabricates_credentials() -> None:
    """Even when a manager raises, the collector fails soft with no items."""
    class _Boom:
        def load_credentials(self, service: str) -> dict | None:
            raise RuntimeError("store unavailable")

    result = MailCollector(credentials_manager=_Boom()).collect()
    assert result.ok is False
    assert result.data == {"items": []}
    assert "credentials unavailable" in (result.error or "")


def test_calendar_collector_never_fabricates_credentials() -> None:
    class _Boom:
        def load_credentials(self, service: str) -> dict | None:
            raise RuntimeError("store unavailable")

    result = CalendarCollector(credentials_manager=_Boom()).collect()
    assert result.ok is False
    assert result.data == {"items": []}
    assert "credentials unavailable" in (result.error or "")


def test_default_registry_surfaces_fail_soft_mail_and_calendar_in_brief() -> None:
    """The full default pipeline (registry -> composer) surfaces both fail-soft sources."""
    results = build_default_registry().run_all()
    sources = [r.source for r in results]
    assert "mail" in sources
    assert "calendar" in sources
    body = compose_brief(results)
    assert "• mail: unavailable — credentials unavailable" in body
    assert "• calendar: unavailable — credentials unavailable" in body