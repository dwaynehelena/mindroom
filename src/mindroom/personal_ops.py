"""Resilient personal-operations briefs and ARIP-gated action execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from mindroom.arip import JsonValue, canonical_json

if TYPE_CHECKING:
    from pathlib import Path

    from mindroom.arip_control import ApprovalControlStore

OpsSource = Literal["mail", "calendar", "tasks", "github"]
WorkerRuntime = Literal["mindroom", "openclaw", "hermes"]
SourceReader = Callable[[datetime], Awaitable[Sequence["OpsItem"]]]
ActionExecutor = Callable[["OpsAction", str], Awaitable[str]]

_SOURCES: tuple[OpsSource, ...] = ("mail", "calendar", "tasks", "github")
_MAX_SUMMARY_CHARS = 500


class PersonalOpsError(RuntimeError):
    """A personal-operations safety or persistence invariant failed."""


@dataclass(frozen=True, slots=True)
class OpsItem:
    """A bounded, source-attributed read item for a daily brief."""

    item_id: str
    source: OpsSource
    summary: str
    observed_at: datetime
    due_at: datetime | None = None
    importance: int = 0
    action_hint: str | None = None

    def __post_init__(self) -> None:
        """Validate bounded item fields."""
        if not self.item_id or not self.summary.strip():
            message = "ops item identity and summary are required"
            raise PersonalOpsError(message)
        if self.importance not in range(101):
            message = "ops item importance must be between zero and 100"
            raise PersonalOpsError(message)
        _utc(self.observed_at)
        if self.due_at is not None:
            _utc(self.due_at)


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """One connector's bounded collection outcome."""

    source: OpsSource
    available: bool
    item_count: int


@dataclass(frozen=True, slots=True)
class DailyBrief:
    """A deterministic cross-source brief suitable for Telegram delivery."""

    generated_at: datetime
    items: tuple[OpsItem, ...]
    sources: tuple[SourceHealth, ...]
    display_timezone: str = "UTC"

    @property
    def display_date(self) -> str:
        """Return the brief's calendar date in its configured display timezone."""
        return self.generated_at.astimezone(_timezone(self.display_timezone)).date().isoformat()

    def render(self) -> str:
        """Render a bounded plain-text brief without connector error details."""
        lines = [f"Personal ops brief — {self.display_date}", ""]
        if not self.items:
            lines.append("No current items.")
        for item in self.items:
            due = f" · due {_utc(item.due_at).isoformat(timespec='minutes')}" if item.due_at else ""
            summary = " ".join(item.summary.split())[:_MAX_SUMMARY_CHARS]
            lines.append(f"• [{item.source}] {summary}{due}")
        unavailable = [health.source for health in self.sources if not health.available]
        if unavailable:
            lines.extend(("", f"Unavailable sources: {', '.join(unavailable)}"))
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class OpsAction:
    """One immutable consequential action awaiting exact-payload authorization."""

    action_id: str
    source: OpsSource
    tool_name: str
    arguments: JsonValue
    runtime: WorkerRuntime
    approval_id: str
    created_at: datetime

    @property
    def idempotency_key(self) -> str:
        """Return a stable key derived from the complete immutable action."""
        value = {
            "action_id": self.action_id,
            "arguments": self.arguments,
            "runtime": self.runtime,
            "source": self.source,
            "tool_name": self.tool_name,
        }
        return hashlib.sha256(canonical_json(value)).hexdigest()


class PersonalOpsStore:
    """Durable explicit preferences and fail-closed action outcomes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the database and create the version-one schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS ops_preference (
              preference_key TEXT PRIMARY KEY,
              value_json TEXT NOT NULL,
              learned_from TEXT NOT NULL,
              learned_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ops_action (
              action_id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              tool_name TEXT NOT NULL,
              arguments_json TEXT NOT NULL,
              runtime TEXT NOT NULL,
              approval_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              idempotency_key TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK(status IN ('pending','executing','completed','failed','uncertain')),
              receipt TEXT
            );
            """,
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the database."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def learn_preference(
        self,
        key: str,
        value: JsonValue,
        *,
        explicit_feedback_id: str,
        learned_at: datetime,
    ) -> None:
        """Learn only from an explicit, attributable user feedback event."""
        if not key.strip() or not explicit_feedback_id.strip():
            message = "preference learning requires a key and explicit feedback event"
            raise PersonalOpsError(message)
        await self._required_db().execute(
            "INSERT INTO ops_preference VALUES(?,?,?,?) "
            "ON CONFLICT(preference_key) DO UPDATE SET value_json=excluded.value_json,"
            "learned_from=excluded.learned_from,learned_at=excluded.learned_at",
            (key.strip(), canonical_json(value).decode(), explicit_feedback_id, _utc(learned_at).isoformat()),
        )
        await self._required_db().commit()

    async def preferences(self) -> dict[str, JsonValue]:
        """Return the current explicit preference map."""
        rows = await (await self._required_db().execute("SELECT preference_key,value_json FROM ops_preference")).fetchall()
        return {key: json.loads(value) for key, value in rows}

    async def stage_action(self, action: OpsAction) -> None:
        """Persist an immutable action before approval or execution."""
        if not action.action_id or not action.tool_name or not action.approval_id:
            message = "action identity, tool, and approval are required"
            raise PersonalOpsError(message)
        values = (
            action.action_id,
            action.source,
            action.tool_name,
            canonical_json(action.arguments).decode(),
            action.runtime,
            action.approval_id,
            _utc(action.created_at).isoformat(),
            action.idempotency_key,
            "pending",
        )
        cursor = await self._required_db().execute(
            "INSERT OR IGNORE INTO ops_action VALUES(?,?,?,?,?,?,?,?,?,NULL)",
            values,
        )
        if cursor.rowcount != 1:
            row = await (
                await self._required_db().execute(
                    "SELECT action_id,source,tool_name,arguments_json,runtime,approval_id,created_at,"
                    "idempotency_key FROM ops_action WHERE action_id=?",
                    (action.action_id,),
                )
            ).fetchone()
            if tuple(row or ()) != values[:-1]:
                message = "action identity equivocation denied"
                raise PersonalOpsError(message)
        await self._required_db().commit()

    async def begin(self, action: OpsAction) -> None:
        """Move a pending action to executing exactly once."""
        cursor = await self._required_db().execute(
            "UPDATE ops_action SET status='executing' WHERE action_id=? AND idempotency_key=? AND status='pending'",
            (action.action_id, action.idempotency_key),
        )
        if cursor.rowcount != 1:
            status = await self.status(action.action_id)
            message = f"action is not safely executable (status={status})"
            raise PersonalOpsError(message)
        await self._required_db().commit()

    async def finish(self, action_id: str, *, receipt: str | None, failed: bool = False) -> None:
        """Persist a completed or known-failed execution outcome."""
        status = "failed" if failed else "completed"
        if not failed and not receipt:
            message = "successful action execution requires a receipt"
            raise PersonalOpsError(message)
        cursor = await self._required_db().execute(
            "UPDATE ops_action SET status=?,receipt=? WHERE action_id=? AND status='executing'",
            (status, receipt, action_id),
        )
        if cursor.rowcount != 1:
            message = "action completion transition is invalid"
            raise PersonalOpsError(message)
        await self._required_db().commit()

    async def mark_interrupted_uncertain(self) -> int:
        """Fail closed after restart when an external write outcome is unknowable."""
        cursor = await self._required_db().execute(
            "UPDATE ops_action SET status='uncertain' WHERE status='executing'",
        )
        await self._required_db().commit()
        return cursor.rowcount

    async def status(self, action_id: str) -> str:
        """Return an action status."""
        row = await (
            await self._required_db().execute("SELECT status FROM ops_action WHERE action_id=?", (action_id,))
        ).fetchone()
        if row is None:
            message = "action not found"
            raise PersonalOpsError(message)
        return str(row[0])

    def lock(self) -> asyncio.Lock:
        """Return the store execution lock."""
        return self._lock

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "personal ops store is not open"
            raise RuntimeError(message)
        return self._db


class PersonalOpsAutopilot:
    """Coordinate resilient reads and authorized writes across personal systems."""

    def __init__(
        self,
        *,
        store: PersonalOpsStore,
        approval_store: ApprovalControlStore,
        readers: Mapping[OpsSource, SourceReader],
        executors: Mapping[WorkerRuntime, ActionExecutor],
        display_timezone: str = "UTC",
    ) -> None:
        if set(readers) != set(_SOURCES):
            message = "personal ops requires mail, calendar, tasks, and GitHub readers"
            raise PersonalOpsError(message)
        if "mindroom" not in executors or "openclaw" not in executors:
            message = "personal ops requires MindRoom coordination and OpenClaw reach"
            raise PersonalOpsError(message)
        self._store = store
        self._approval_store = approval_store
        self._readers = dict(readers)
        self._executors = dict(executors)
        self._display_timezone = _timezone(display_timezone).key

    async def brief(self, observed_at: datetime, *, limit: int = 20) -> DailyBrief:
        """Collect every source independently and prioritize available results."""
        if limit < 1 or limit > 100:
            message = "brief limit must be between one and 100"
            raise PersonalOpsError(message)
        observed = _utc(observed_at)
        results = await asyncio.gather(
            *(self._read_source(source, observed) for source in _SOURCES),
        )
        items = [item for _health, source_items in results for item in source_items]
        items.sort(key=lambda item: self._priority_key(item, observed))
        return DailyBrief(
            observed,
            tuple(items[:limit]),
            tuple(health for health, _items in results),
            self._display_timezone,
        )

    async def execute(self, action: OpsAction, *, observed_at: datetime) -> str:
        """Consume exact ARIP authorization immediately before one durable write attempt."""
        executor = self._executors.get(action.runtime)
        if executor is None:
            message = "no executor exists for the selected runtime"
            raise PersonalOpsError(message)
        async with self._store.lock():
            await self._store.stage_action(action)
            await self._approval_store.consume(
                approval_id=action.approval_id,
                tool_name=action.tool_name,
                arguments=action.arguments,
                observed_at=observed_at,
            )
            await self._store.begin(action)
            try:
                receipt = await executor(action, action.idempotency_key)
            except Exception:
                await self._store.finish(action.action_id, receipt=None, failed=True)
                raise
            await self._store.finish(action.action_id, receipt=receipt)
            return receipt

    async def _read_source(self, source: OpsSource, observed_at: datetime) -> tuple[SourceHealth, tuple[OpsItem, ...]]:
        try:
            items = _validated_source_items(source, await self._readers[source](observed_at))
        except Exception:
            return SourceHealth(source, False, 0), ()
        return SourceHealth(source, True, len(items)), items

    @staticmethod
    def _priority_key(item: OpsItem, observed_at: datetime) -> tuple[int, float, str, str]:
        overdue = item.due_at is not None and _utc(item.due_at) <= observed_at
        due_timestamp = _utc(item.due_at).timestamp() if item.due_at else float("inf")
        return (-int(overdue), -item.importance, due_timestamp, item.source, item.item_id)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "personal ops timestamps must include a timezone"
        raise PersonalOpsError(message)
    return value.astimezone(UTC)


def _validated_source_items(source: OpsSource, items: Sequence[OpsItem]) -> tuple[OpsItem, ...]:
    result = tuple(items)
    if any(item.source != source for item in result):
        message = "connector returned an item with the wrong source"
        raise PersonalOpsError(message)
    return result


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        message = "personal ops display timezone must be a valid IANA timezone"
        raise PersonalOpsError(message) from exc
