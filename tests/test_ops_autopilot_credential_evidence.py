"""Unit tests for P8 Phase 2 mail/calendar collector credential evidence.

Confirms the fail-soft collectors against the real runtime credential store:
no Gmail or Google Calendar credentials exist, so both collectors fail soft
with no items and never fabricate credentials.
"""

from __future__ import annotations

from pathlib import Path

from mindroom.constants import resolve_runtime_paths
from mindroom.credentials import get_runtime_credentials_manager
from mindroom.ops_autopilot.collectors.calendar import CalendarCollector
from mindroom.ops_autopilot.collectors.mail import MailCollector

RUNTIME_CONFIG = Path.home() / ".mindroom" / "config.yaml"
STORAGE = Path.home() / ".mindroom" / "mindroom_data"


def _runtime_paths() -> object:
    return resolve_runtime_paths(
        config_path=RUNTIME_CONFIG,
        storage_path=STORAGE,
        process_env={},
    )


def test_credential_store_has_no_gmail_or_calendar_credentials() -> None:
    """Evidence: the live credential store has no gmail/google_calendar creds."""
    manager = get_runtime_credentials_manager(_runtime_paths())
    assert manager.load_credentials("google_gmail_oauth") is None
    assert manager.load_credentials("google_calendar_oauth") is None


def test_mail_collector_fails_soft_against_live_store() -> None:
    """MailCollector fails soft with no items against the live store."""
    result = MailCollector().collect()
    assert result.ok is False
    assert result.source == "mail"
    assert result.data == {"items": []}
    assert "credentials unavailable" in (result.error or "")


def test_calendar_collector_fails_soft_against_live_store() -> None:
    """CalendarCollector fails soft with no items against the live store."""
    result = CalendarCollector().collect()
    assert result.ok is False
    assert result.source == "calendar"
    assert result.data == {"items": []}
    assert "credentials unavailable" in (result.error or "")


def test_collectors_never_fabricate_credentials() -> None:
    """Neither collector fabricates credentials or placeholder data."""
    mail = MailCollector().collect()
    cal = CalendarCollector().collect()
    assert mail.data == {"items": []}
    assert cal.data == {"items": []}
    assert mail.error == "credentials unavailable"
    assert cal.error == "credentials unavailable"