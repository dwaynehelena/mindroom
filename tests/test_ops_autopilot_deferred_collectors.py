"""Unit tests for the deferred mail and calendar collectors."""

from __future__ import annotations

from mindroom.ops_autopilot.collectors.calendar import CalendarCollector
from mindroom.ops_autopilot.collectors.mail import MailCollector


def test_mail_collector_reports_deferred_with_evidence() -> None:
    result = MailCollector().collect()
    assert result.ok is True
    assert result.source == "mail"
    assert result.data["deferred"] is True
    assert result.data["reason"] == "no gmail credentials found"
    assert result.data["unread_count"] == 0


def test_calendar_collector_reports_deferred_with_evidence() -> None:
    result = CalendarCollector().collect()
    assert result.ok is True
    assert result.source == "calendar"
    assert result.data["deferred"] is True
    assert result.data["reason"] == "no google_calendar credentials found"
    assert result.data["upcoming_events"] == []


def test_mail_collector_data_is_deterministic() -> None:
    a = MailCollector().collect().data
    b = MailCollector().collect().data
    assert a == b


def test_calendar_collector_data_is_deterministic() -> None:
    a = CalendarCollector().collect().data
    b = CalendarCollector().collect().data
    assert a == b