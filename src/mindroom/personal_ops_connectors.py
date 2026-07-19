"""Read-only MindRoom tool bindings for the four Personal Ops source lanes."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import cast

from mindroom.personal_ops import OpsItem, OpsSource, PersonalOpsError, SourceReader

ToolReadInvoker = Callable[[str, str, Mapping[str, object]], Awaitable[object]]

_GMAIL_BLOCK = re.compile(
    r"Subject: (?P<subject>.*?)\nFrom: (?P<sender>.*?)\nDate: (?P<date>.*?)\n"
    r"Body: .*?\nMessage ID: (?P<id>.*?)\n.*?Thread ID: (?P<thread>.*?)\n-{20,}",
    re.DOTALL,
)
_TODO_LINE = re.compile(
    r"^-\s+(?:\S+\s+)?`(?P<id>[^`]+)`\s+(?P<title>.*?)\s+"
    r"\[(?P<priority>low|medium|high|critical)\](?:\s+@\S+)?$",
)
_PRIORITY = {"low": 25, "medium": 50, "high": 75, "critical": 100}
_MAIL: OpsSource = "mail"
_CALENDAR: OpsSource = "calendar"
_TASKS: OpsSource = "tasks"
_GITHUB: OpsSource = "github"


@dataclass(frozen=True, slots=True)
class PersonalOpsConnectorConfig:
    """Bounded read limits and the required GitHub repository scope."""

    github_repository: str
    item_limit: int = 20

    def __post_init__(self) -> None:
        """Reject unscoped repositories and unbounded reads."""
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.github_repository)
            or self.item_limit < 1
            or self.item_limit > 100
        ):
            message = "personal ops connectors require an owner/repository scope and bounded item limit"
            raise PersonalOpsError(message)


def mindroom_source_readers(
    *,
    invoker: ToolReadInvoker,
    config: PersonalOpsConnectorConfig,
) -> dict[OpsSource, SourceReader]:
    """Bind all required Personal Ops sources to exact read-only MindRoom tool calls."""
    return {
        "mail": _mail_reader(invoker, config.item_limit),
        "calendar": _calendar_reader(invoker, config.item_limit),
        "tasks": _tasks_reader(invoker),
        "github": _github_reader(invoker, config.github_repository, config.item_limit),
    }


def _mail_reader(invoker: ToolReadInvoker, limit: int) -> SourceReader:
    async def read(observed_at: datetime) -> Sequence[OpsItem]:
        result = await invoker("gmail", "get_unread_emails", {"count": limit})
        if not isinstance(result, str) or result.startswith(("Error ", "Unexpected error")):
            raise _source_error(_MAIL)
        items = []
        for match in _GMAIL_BLOCK.finditer(result):
            timestamp = _mail_timestamp(match.group("date"), observed_at)
            summary = f"{match.group('subject').strip()} — {match.group('sender').strip()}"
            items.append(OpsItem(match.group("id").strip(), "mail", summary, timestamp, importance=60))
        if result.strip() and not items:
            raise _source_error(_MAIL)
        return tuple(items)

    return read


def _calendar_reader(invoker: ToolReadInvoker, limit: int) -> SourceReader:
    async def read(observed_at: datetime) -> Sequence[OpsItem]:
        result = await invoker(
            "google_calendar",
            "list_events",
            {"limit": limit, "start_date": _utc(observed_at).isoformat()},
        )
        value = _json_result("calendar", result)
        if isinstance(value, Mapping):
            value_mapping = cast("Mapping[str, object]", value)
            if value_mapping.get("message") == "No upcoming events found.":
                return ()
        if not isinstance(value, list):
            raise _source_error(_CALENDAR)
        items = []
        for raw in value:
            if not isinstance(raw, Mapping):
                raise _source_error(_CALENDAR)
            event = cast("Mapping[str, object]", raw)
            event_id = event.get("id")
            summary = event.get("summary")
            start = event.get("start")
            if not isinstance(event_id, str) or not isinstance(summary, str) or not isinstance(start, dict):
                raise _source_error(_CALENDAR)
            due_at = _calendar_timestamp(cast("Mapping[str, object]", start))
            items.append(OpsItem(event_id, "calendar", summary, observed_at, due_at=due_at, importance=70))
        return tuple(items)

    return read


def _tasks_reader(invoker: ToolReadInvoker) -> SourceReader:
    async def read(observed_at: datetime) -> Sequence[OpsItem]:
        result = await invoker("todo", "list_todos", {"show_all": False})
        if result == "No items in this thread's work plan.":
            return ()
        if not isinstance(result, str):
            raise _source_error(_TASKS)
        items = []
        for line in result.splitlines():
            match = _TODO_LINE.fullmatch(line.strip())
            if match is None:
                continue
            priority = match.group("priority")
            items.append(
                OpsItem(
                    match.group("id"),
                    "tasks",
                    match.group("title"),
                    observed_at,
                    importance=_PRIORITY[priority],
                ),
            )
        if "**Actionable:**" in result and not items:
            raise _source_error(_TASKS)
        return tuple(items)

    return read


def _github_reader(invoker: ToolReadInvoker, repository: str, limit: int) -> SourceReader:
    async def read(observed_at: datetime) -> Sequence[OpsItem]:
        del observed_at
        result = await invoker(
            "github",
            "list_issues",
            {"repo_name": repository, "state": "open", "page": 1, "per_page": limit},
        )
        value = _json_result("github", result)
        if not isinstance(value, Mapping):
            raise _source_error(_GITHUB)
        response = cast("Mapping[str, object]", value)
        data = response.get("data")
        if not isinstance(data, list):
            raise _source_error(_GITHUB)
        items = []
        for raw in cast("list[object]", data):
            if not isinstance(raw, Mapping):
                raise _source_error(_GITHUB)
            issue = cast("Mapping[str, object]", raw)
            number = issue.get("number")
            title = issue.get("title")
            created_at = issue.get("created_at")
            if isinstance(number, bool) or not isinstance(number, int) or not isinstance(title, str):
                raise _source_error(_GITHUB)
            timestamp = _iso_timestamp(_GITHUB, created_at)
            items.append(OpsItem(f"{repository}#{number}", "github", title, timestamp, importance=50))
        return tuple(items)

    return read


def _json_result(source: OpsSource, result: object) -> object:
    if not isinstance(result, (str, bytes, bytearray)):
        value = result
    else:
        try:
            value = json.loads(result)
        except (TypeError, ValueError) as exc:
            raise _source_error(source) from exc
    if isinstance(value, Mapping):
        response = cast("Mapping[str, object]", value)
        if "error" in response or response.get("oauth_connection_required") is True:
            raise _source_error(source)
    return value


def _calendar_timestamp(value: Mapping[str, object]) -> datetime:
    timestamp = value.get("dateTime") or value.get("date")
    return _iso_timestamp(_CALENDAR, timestamp)


def _iso_timestamp(source: OpsSource, value: object) -> datetime:
    if not isinstance(value, str):
        raise _source_error(source)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _source_error(source) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _mail_timestamp(value: str, fallback: datetime) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return _utc(fallback)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _source_error(_CALENDAR)
    return value.astimezone(UTC)


def _source_error(source: OpsSource) -> PersonalOpsError:
    return PersonalOpsError(f"personal ops {source} source returned an invalid read result")
