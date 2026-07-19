"""Tests for concrete read-only Personal Ops MindRoom tool bindings."""

# ruff: noqa: ANN001, ANN202, D103

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from mindroom.personal_ops import PersonalOpsError
from mindroom.personal_ops_connectors import PersonalOpsConnectorConfig, mindroom_source_readers

NOW = datetime(2026, 7, 18, 8, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


def _results():
    return {
        ("gmail", "get_unread_emails"): (
            "Subject: Review needed\nFrom: Alice <alice@example.test>\n"
            "Date: Sat, 18 Jul 2026 09:00:00 +1000\nBody: Private body\nMessage ID: mail-1\n"
            "In-Reply-To: \nReferences: \nThread ID: thread-1\n----------------------------------------"
        ),
        ("google_calendar", "list_events"): json.dumps(
            [{"id": "event-1", "summary": "Planning", "start": {"dateTime": "2026-07-18T10:00:00+10:00"}}],
        ),
        ("todo", "list_todos"): ("Work plan: 0/1 complete.\n\n**Actionable:**\n- 🔴 `task-1` Finish report [critical]"),
        ("github", "list_issues"): json.dumps(
            {
                "data": [
                    {
                        "number": 42,
                        "title": "Fix deployment",
                        "created_at": "2026-07-18T00:00:00Z",
                    },
                ],
                "meta": {"current_page": 1},
            },
        ),
    }


async def test_all_four_bindings_use_exact_read_only_tools_and_normalize_items() -> None:
    calls = []
    results = _results()

    async def invoke(tool, function, arguments):
        calls.append((tool, function, dict(arguments)))
        return results[(tool, function)]

    readers = mindroom_source_readers(
        invoker=invoke,
        config=PersonalOpsConnectorConfig("mindroom-ai/mindroom", item_limit=10),
    )
    items = {source: await reader(NOW) for source, reader in readers.items()}
    assert set(items) == {"mail", "calendar", "tasks", "github"}
    assert items["mail"][0].summary == "Review needed — Alice <alice@example.test>"
    assert items["calendar"][0].due_at == datetime(2026, 7, 18, 10, tzinfo=items["calendar"][0].due_at.tzinfo)
    assert items["tasks"][0].importance == 100
    assert items["github"][0].item_id == "mindroom-ai/mindroom#42"
    assert {(tool, function) for tool, function, _args in calls} == {
        ("gmail", "get_unread_emails"),
        ("google_calendar", "list_events"),
        ("todo", "list_todos"),
        ("github", "list_issues"),
    }
    assert all(
        function not in {"send_email", "create_event", "update_todo", "create_issue"} for _, function, _ in calls
    )


@pytest.mark.parametrize(
    ("source", "bad_result"),
    [
        ("mail", "Unexpected error retrieving unread emails: secret"),
        ("calendar", json.dumps({"oauth_connection_required": True, "connect_url": "private"})),
        ("tasks", "Work plan: 0/1 complete.\n\n**Actionable:**\nmalformed"),
        ("github", json.dumps({"error": "private API error"})),
    ],
)
async def test_connector_errors_fail_source_without_returning_private_details(source, bad_result) -> None:
    results = _results()
    key_by_source = {
        "mail": ("gmail", "get_unread_emails"),
        "calendar": ("google_calendar", "list_events"),
        "tasks": ("todo", "list_todos"),
        "github": ("github", "list_issues"),
    }
    results[key_by_source[source]] = bad_result

    async def invoke(tool, function, _arguments):
        return results[(tool, function)]

    reader = mindroom_source_readers(
        invoker=invoke,
        config=PersonalOpsConnectorConfig("mindroom-ai/mindroom"),
    )[source]
    with pytest.raises(PersonalOpsError) as exc_info:
        await reader(NOW)
    assert "private" not in str(exc_info.value)


@pytest.mark.parametrize("repository", ["", "missing-owner", "owner/repo/extra", "owner/repo with spaces"])
async def test_connector_config_requires_exact_repository_scope(repository: str) -> None:
    with pytest.raises(PersonalOpsError, match="owner/repository"):
        PersonalOpsConnectorConfig(repository)
