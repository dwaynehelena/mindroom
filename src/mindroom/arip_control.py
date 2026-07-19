"""Durable exact-payload approval authority for live ARIP consumers."""

# Transaction blocks deliberately validate and raise before their shared rollback handler.
# ruff: noqa: TC003, TRY300, TRY301

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite

from mindroom.arip import JsonValue, canonical_json, sha256_digest

Decision = Literal["approved", "denied"]


class ApprovalControlError(RuntimeError):
    """A live approval invariant was violated."""


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    """Content-free state for one exact-payload approval request."""

    approval_id: str
    status: Literal["pending", "approved", "denied", "expired", "consumed"]
    approvals: int
    quorum: int


def executable_payload_digest(tool_name: str, arguments: JsonValue) -> str:
    """Hash the complete executable payload; never render this input as a preview."""
    return sha256_digest({"arguments": arguments, "tool_name": tool_name})


class ApprovalControlStore:
    """SQLite-backed exact-payload request, decision, and single-consumption ledger."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._transaction_lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the ledger and create its version-one schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS approval_request (
              approval_id TEXT PRIMARY KEY,
              tool_call_event_id TEXT NOT NULL UNIQUE,
              payload_digest TEXT NOT NULL,
              eligible_actors_json TEXT NOT NULL,
              quorum INTEGER NOT NULL CHECK (quorum > 0),
              expires_at TEXT NOT NULL,
              consumed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS approval_decision (
              approval_id TEXT NOT NULL REFERENCES approval_request(approval_id),
              actor_id TEXT NOT NULL,
              decision TEXT NOT NULL CHECK (decision IN ('approved', 'denied')),
              decided_at TEXT NOT NULL,
              PRIMARY KEY (approval_id, actor_id)
            );
            """,
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the ledger."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def request(
        self,
        *,
        approval_id: str,
        tool_call_event_id: str,
        tool_name: str,
        arguments: JsonValue,
        eligible_actors: tuple[str, ...],
        quorum: int,
        expires_at: datetime,
    ) -> ApprovalOutcome:
        """Serialize and persist one exact-payload request."""
        async with self._transaction_lock:
            return await self._request(
                approval_id=approval_id,
                tool_call_event_id=tool_call_event_id,
                tool_name=tool_name,
                arguments=arguments,
                eligible_actors=eligible_actors,
                quorum=quorum,
                expires_at=expires_at,
            )

    async def _request(
        self,
        *,
        approval_id: str,
        tool_call_event_id: str,
        tool_name: str,
        arguments: JsonValue,
        eligible_actors: tuple[str, ...],
        quorum: int,
        expires_at: datetime,
    ) -> ApprovalOutcome:
        """Persist an idempotent request bound to the complete executable payload."""
        db = self._required_db()
        actors = tuple(dict.fromkeys(eligible_actors))
        expiry = _utc(expires_at)
        if not approval_id or not tool_call_event_id or not actors or quorum < 1 or quorum > len(actors):
            message = "invalid approval identity, actors, or quorum"
            raise ApprovalControlError(message)
        digest = executable_payload_digest(tool_name, arguments)
        actors_json = canonical_json(list(actors)).decode("utf-8")
        await db.execute("BEGIN IMMEDIATE")
        try:
            existing = await (
                await db.execute(
                    "SELECT tool_call_event_id,payload_digest,eligible_actors_json,quorum,expires_at "
                    "FROM approval_request WHERE approval_id=?",
                    (approval_id,),
                )
            ).fetchone()
            expected = (tool_call_event_id, digest, actors_json, quorum, expiry.isoformat())
            if existing is None:
                await db.execute(
                    "INSERT INTO approval_request VALUES(?,?,?,?,?,?,NULL)",
                    (approval_id, *expected),
                )
            elif tuple(existing) != expected:
                message = "approval request equivocation denied"
                raise ApprovalControlError(message)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        return ApprovalOutcome(approval_id, "pending", 0, quorum)

    async def decide(
        self,
        *,
        approval_id: str,
        actor_id: str,
        decision: Decision,
        decided_at: datetime,
    ) -> ApprovalOutcome:
        """Serialize one immutable actor decision."""
        async with self._transaction_lock:
            return await self._decide(
                approval_id=approval_id,
                actor_id=actor_id,
                decision=decision,
                decided_at=decided_at,
            )

    async def _decide(
        self,
        *,
        approval_id: str,
        actor_id: str,
        decision: Decision,
        decided_at: datetime,
    ) -> ApprovalOutcome:
        """Record one eligible actor's immutable decision and calculate current state."""
        db = self._required_db()
        observed = _utc(decided_at)
        await db.execute("BEGIN IMMEDIATE")
        try:
            request = await self._request_row(approval_id)
            actors = json.loads(request[2])
            if actor_id not in actors:
                message = "approval actor is not eligible"
                raise ApprovalControlError(message)
            if observed > datetime.fromisoformat(request[4]):
                message = "approval decision is expired"
                raise ApprovalControlError(message)
            existing = await (
                await db.execute(
                    "SELECT decision,decided_at FROM approval_decision WHERE approval_id=? AND actor_id=?",
                    (approval_id, actor_id),
                )
            ).fetchone()
            expected = (decision, observed.isoformat())
            if existing is None:
                await db.execute(
                    "INSERT INTO approval_decision VALUES(?,?,?,?)",
                    (approval_id, actor_id, *expected),
                )
            elif tuple(existing) != expected:
                message = "approval decision equivocation denied"
                raise ApprovalControlError(message)
            outcome = await self._outcome(approval_id, observed)
            await db.commit()
            return outcome
        except BaseException:
            await db.rollback()
            raise

    async def consume(
        self,
        *,
        approval_id: str,
        tool_name: str,
        arguments: JsonValue,
        observed_at: datetime,
    ) -> None:
        """Serialize exact-payload authorization and consumption."""
        async with self._transaction_lock:
            await self._consume(
                approval_id=approval_id,
                tool_name=tool_name,
                arguments=arguments,
                observed_at=observed_at,
            )

    async def _consume(
        self,
        *,
        approval_id: str,
        tool_name: str,
        arguments: JsonValue,
        observed_at: datetime,
    ) -> None:
        """Atomically authorize and consume one matching approved payload exactly once."""
        db = self._required_db()
        observed = _utc(observed_at)
        await db.execute("BEGIN IMMEDIATE")
        try:
            request = await self._request_row(approval_id)
            if request[5] is not None:
                message = "approval was already consumed"
                raise ApprovalControlError(message)
            if request[1] != executable_payload_digest(tool_name, arguments):
                message = "executable payload does not match the approved digest"
                raise ApprovalControlError(message)
            outcome = await self._outcome(approval_id, observed)
            if outcome.status != "approved":
                message = f"approval is not executable (status={outcome.status})"
                raise ApprovalControlError(message)
            cursor = await db.execute(
                "UPDATE approval_request SET consumed_at=? WHERE approval_id=? AND consumed_at IS NULL",
                (observed.isoformat(), approval_id),
            )
            if cursor.rowcount != 1:
                message = "approval consumption raced with another consumer"
                raise ApprovalControlError(message)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    async def _request_row(self, approval_id: str) -> tuple[str, str, str, int, str, str | None]:
        row = await (
            await self._required_db().execute(
                "SELECT tool_call_event_id,payload_digest,eligible_actors_json,quorum,expires_at,consumed_at "
                "FROM approval_request WHERE approval_id=?",
                (approval_id,),
            )
        ).fetchone()
        if row is None:
            message = "approval request not found"
            raise ApprovalControlError(message)
        return row

    async def _outcome(self, approval_id: str, observed_at: datetime) -> ApprovalOutcome:
        request = await self._request_row(approval_id)
        decisions = await (
            await self._required_db().execute(
                "SELECT decision FROM approval_decision WHERE approval_id=?",
                (approval_id,),
            )
        ).fetchall()
        approvals = sum(row[0] == "approved" for row in decisions)
        if request[5] is not None:
            status = "consumed"
        elif observed_at > datetime.fromisoformat(request[4]):
            status = "expired"
        elif any(row[0] == "denied" for row in decisions):
            status = "denied"
        elif approvals >= request[3]:
            status = "approved"
        else:
            status = "pending"
        return ApprovalOutcome(approval_id, status, approvals, request[3])

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "approval control store is not open"
            raise RuntimeError(message)
        return self._db


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "approval timestamps must include a timezone"
        raise ApprovalControlError(message)
    return value.astimezone(UTC)
