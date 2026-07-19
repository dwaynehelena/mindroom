"""Schedule-safe Personal Ops source adaptation and brief delivery composition."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite

from mindroom.personal_ops import DailyBrief, OpsItem, OpsSource, PersonalOpsAutopilot, PersonalOpsError, SourceReader
from mindroom.personal_ops_delivery import MatrixBriefReceipt, MatrixPortalBriefSender

if TYPE_CHECKING:
    from pathlib import Path

RawSourceFetcher = Callable[[datetime], Awaitable[Sequence[Mapping[str, object]]]]
_OPTIONAL_ITEM_FIELDS = frozenset({"action_hint", "due_at", "importance"})
_REQUIRED_ITEM_FIELDS = frozenset({"item_id", "observed_at", "summary"})
_MAX_SOURCE_ITEMS = 1000


@dataclass(frozen=True, slots=True)
class PersonalOpsBriefOutcome:
    """One collected brief and its durable Matrix delivery outcome."""

    brief: DailyBrief
    receipt: MatrixBriefReceipt
    newly_delivered: bool


class BriefDeliveryLedger:
    """Prevent duplicate or equivocated daily briefs across scheduler retries."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the delivery ledger."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS personal_ops_brief_delivery (
              display_date TEXT PRIMARY KEY,
              body_digest TEXT NOT NULL,
              transaction_id TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK(status IN ('delivering','delivered')),
              event_id TEXT
            );
            """,
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the delivery ledger."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def claim(self, brief: DailyBrief) -> MatrixBriefReceipt | None:
        """Claim an exact date/body transaction or return its completed receipt."""
        digest, transaction_id = _brief_identity(brief)
        async with self._lock:
            cursor = await self._required_db().execute(
                "INSERT OR IGNORE INTO personal_ops_brief_delivery VALUES(?,?,?,'delivering',NULL)",
                (brief.display_date, digest, transaction_id),
            )
            if cursor.rowcount == 1:
                await self._required_db().commit()
                return None
            row = await (
                await self._required_db().execute(
                    "SELECT body_digest,transaction_id,status,event_id FROM personal_ops_brief_delivery "
                    "WHERE display_date=?",
                    (brief.display_date,),
                )
            ).fetchone()
            if row is None or str(row[0]) != digest or str(row[1]) != transaction_id:
                message = "personal ops daily brief identity equivocation denied"
                raise PersonalOpsError(message)
            if row[2] == "delivered":
                if not row[3]:
                    message = "personal ops delivered brief is missing its event receipt"
                    raise PersonalOpsError(message)
                return MatrixBriefReceipt(str(row[3]), transaction_id, digest)
            return None

    async def settle(self, brief: DailyBrief, receipt: MatrixBriefReceipt) -> None:
        """Persist the exact Matrix receipt after an idempotent send."""
        digest, transaction_id = _brief_identity(brief)
        if receipt.body_digest != digest or receipt.transaction_id != transaction_id or not receipt.event_id:
            message = "personal ops delivery receipt does not match the claimed brief"
            raise PersonalOpsError(message)
        cursor = await self._required_db().execute(
            "UPDATE personal_ops_brief_delivery SET status='delivered',event_id=? "
            "WHERE display_date=? AND body_digest=? AND transaction_id=? AND status='delivering'",
            (receipt.event_id, brief.display_date, digest, transaction_id),
        )
        if cursor.rowcount != 1:
            row = await (
                await self._required_db().execute(
                    "SELECT status,event_id FROM personal_ops_brief_delivery "
                    "WHERE display_date=? AND body_digest=? AND transaction_id=?",
                    (brief.display_date, digest, transaction_id),
                )
            ).fetchone()
            if row is None or row[0] != "delivered" or row[1] != receipt.event_id:
                message = "personal ops brief delivery transition is invalid"
                raise PersonalOpsError(message)
        await self._required_db().commit()

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "personal ops brief delivery ledger is not open"
            raise RuntimeError(message)
        return self._db


class PersonalOpsBriefRunner:
    """Collect and deliver one daily brief safely under scheduler retries."""

    def __init__(
        self,
        *,
        autopilot: PersonalOpsAutopilot,
        sender: MatrixPortalBriefSender,
        ledger: BriefDeliveryLedger,
    ) -> None:
        self._autopilot = autopilot
        self._sender = sender
        self._ledger = ledger
        self._lock = asyncio.Lock()

    async def run(self, observed_at: datetime, *, limit: int = 20) -> PersonalOpsBriefOutcome:
        """Collect sources and send at most one exact brief for its local date."""
        async with self._lock:
            brief = await self._autopilot.brief(observed_at, limit=limit)
            existing = await self._ledger.claim(brief)
            if existing is not None:
                return PersonalOpsBriefOutcome(brief, existing, False)
            receipt = await self._sender.send(brief)
            await self._ledger.settle(brief, receipt)
            return PersonalOpsBriefOutcome(brief, receipt, True)


def source_reader(source: OpsSource, fetcher: RawSourceFetcher) -> SourceReader:
    """Adapt one external read connector to bounded, source-attributed Ops items."""

    async def read(observed_at: datetime) -> Sequence[OpsItem]:
        raw_items = await fetcher(observed_at)
        if isinstance(raw_items, (str, bytes)) or len(raw_items) > _MAX_SOURCE_ITEMS:
            message = "personal ops source returned an invalid or oversized item sequence"
            raise PersonalOpsError(message)
        return tuple(_parse_item(source, item) for item in raw_items)

    return read


def _parse_item(source: OpsSource, value: Mapping[str, object]) -> OpsItem:
    if set(value) - (_REQUIRED_ITEM_FIELDS | _OPTIONAL_ITEM_FIELDS) or not set(value) >= _REQUIRED_ITEM_FIELDS:
        message = "personal ops source item fields are invalid"
        raise PersonalOpsError(message)
    item_id = value["item_id"]
    summary = value["summary"]
    observed_at = _timestamp(value["observed_at"])
    due_value = value.get("due_at")
    due_at = _timestamp(due_value) if due_value is not None else None
    importance = value.get("importance", 0)
    action_hint = value.get("action_hint")
    if (
        not isinstance(item_id, str)
        or not isinstance(summary, str)
        or isinstance(importance, bool)
        or not isinstance(importance, int)
        or (action_hint is not None and not isinstance(action_hint, str))
    ):
        message = "personal ops source item types are invalid"
        raise PersonalOpsError(message)
    return OpsItem(item_id, source, summary, observed_at, due_at, importance, action_hint)


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError as exc:
            message = "personal ops source timestamp is invalid"
            raise PersonalOpsError(message) from exc
    else:
        message = "personal ops source timestamp is invalid"
        raise PersonalOpsError(message)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        message = "personal ops source timestamp must include a timezone"
        raise PersonalOpsError(message)
    return timestamp


def _brief_identity(brief: DailyBrief) -> tuple[str, str]:
    digest = hashlib.sha256(brief.render().encode()).hexdigest()
    return digest, f"personal-ops-{brief.display_date}-{digest[:24]}"
