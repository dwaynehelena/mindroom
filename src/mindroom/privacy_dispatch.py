"""Durable fail-closed dispatch through one privacy-policy-selected route."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import JsonValue

if TYPE_CHECKING:
    from pathlib import Path

    from mindroom.privacy_router import PrivacyRouter, RouteRequest

RouteHandler = Callable[[JsonValue], Awaitable[JsonValue]]


class PrivacyDispatchError(RuntimeError):
    """A dispatch identity, route binding, or lifecycle invariant failed."""


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    """Content-free evidence for one policy-bound execution."""

    request_id: str
    route_id: str
    request_digest: str
    result_digest: str
    matched_constraints: tuple[str, ...]


class PrivacyDispatchStore:
    """Persist exact request-to-route bindings without request content."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the dispatch ledger."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS privacy_dispatch (
              request_id TEXT PRIMARY KEY,
              request_digest TEXT NOT NULL,
              route_id TEXT NOT NULL,
              constraints_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('reserved','executing','completed','failed','uncertain')),
              result_digest TEXT,
              failure_class TEXT
            );
            """,
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the dispatch ledger."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def reserve(
        self,
        *,
        request_id: str,
        request_digest: str,
        route_id: str,
        constraints: tuple[str, ...],
    ) -> None:
        """Bind a request identity immutably to one selected route."""
        if not request_id or not route_id:
            message = "privacy dispatch request and route identities are required"
            raise PrivacyDispatchError(message)
        values = (request_id, request_digest, route_id, _json(list(constraints)))
        async with self._lock:
            cursor = await self._required_db().execute(
                "INSERT OR IGNORE INTO privacy_dispatch VALUES(?,?,?,?,'reserved',NULL,NULL)",
                values,
            )
            if cursor.rowcount != 1:
                row = await (
                    await self._required_db().execute(
                        "SELECT request_id,request_digest,route_id,constraints_json,status "
                        "FROM privacy_dispatch WHERE request_id=?",
                        (request_id,),
                    )
                ).fetchone()
                if tuple(row[:4]) != values:
                    message = "privacy dispatch identity equivocation denied"
                    raise PrivacyDispatchError(message)
                message = f"privacy dispatch is not replayable (status={row[4]})"
                raise PrivacyDispatchError(message)
            await self._required_db().commit()

    async def begin(self, request_id: str) -> None:
        """Mark the single selected route attempt executing."""
        await self._transition(request_id, "reserved", "executing")

    async def complete(self, request_id: str, result_digest: str) -> None:
        """Persist content-free completion evidence."""
        cursor = await self._required_db().execute(
            "UPDATE privacy_dispatch SET status='completed',result_digest=? "
            "WHERE request_id=? AND status='executing'",
            (result_digest, request_id),
        )
        if cursor.rowcount != 1:
            message = "privacy dispatch completion transition is invalid"
            raise PrivacyDispatchError(message)
        await self._required_db().commit()

    async def fail(self, request_id: str, failure_class: str) -> None:
        """Persist only the failure class; never handler content."""
        cursor = await self._required_db().execute(
            "UPDATE privacy_dispatch SET status='failed',failure_class=? "
            "WHERE request_id=? AND status='executing'",
            (failure_class[:256], request_id),
        )
        if cursor.rowcount != 1:
            message = "privacy dispatch failure transition is invalid"
            raise PrivacyDispatchError(message)
        await self._required_db().commit()

    async def recover_uncertain(self) -> int:
        """Quarantine executions interrupted before a durable outcome."""
        cursor = await self._required_db().execute(
            "UPDATE privacy_dispatch SET status='uncertain' WHERE status='executing'",
        )
        await self._required_db().commit()
        return cursor.rowcount

    async def status(self, request_id: str) -> tuple[str, str | None, str | None]:
        """Return status, result digest, and sanitized failure class."""
        row = await (
            await self._required_db().execute(
                "SELECT status,result_digest,failure_class FROM privacy_dispatch WHERE request_id=?",
                (request_id,),
            )
        ).fetchone()
        if row is None:
            message = "privacy dispatch request not found"
            raise PrivacyDispatchError(message)
        return str(row[0]), str(row[1]) if row[1] else None, str(row[2]) if row[2] else None

    async def _transition(self, request_id: str, expected: str, status: str) -> None:
        cursor = await self._required_db().execute(
            "UPDATE privacy_dispatch SET status=? WHERE request_id=? AND status=?",
            (status, request_id, expected),
        )
        if cursor.rowcount != 1:
            message = "privacy dispatch lifecycle transition is invalid"
            raise PrivacyDispatchError(message)
        await self._required_db().commit()

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "privacy dispatch store is not open"
            raise RuntimeError(message)
        return self._db


class GovernedDispatcher:
    """Select exactly one safe route and never substitute on execution failure."""

    def __init__(
        self,
        *,
        router: PrivacyRouter,
        store: PrivacyDispatchStore,
        handlers: Mapping[str, RouteHandler],
    ) -> None:
        if not handlers:
            message = "governed dispatcher requires explicit route handlers"
            raise ValueError(message)
        self._router = router
        self._store = store
        self._handlers = dict(handlers)

    async def dispatch(
        self,
        *,
        request_id: str,
        policy: RouteRequest,
        payload: JsonValue,
    ) -> tuple[JsonValue, DispatchReceipt]:
        """Execute once through the selected handler with a durable route binding."""
        decision = self._router.route(policy)
        route_id = decision.candidate.route_id
        handler = self._handlers.get(route_id)
        if handler is None:
            message = "selected privacy route has no execution handler"
            raise PrivacyDispatchError(message)
        request_digest = _digest(payload)
        await self._store.reserve(
            request_id=request_id,
            request_digest=request_digest,
            route_id=route_id,
            constraints=decision.matched_constraints,
        )
        await self._store.begin(request_id)
        try:
            result = await handler(payload)
            result_digest = _digest(result)
        except Exception as exc:
            await self._store.fail(request_id, f"{type(exc).__module__}.{type(exc).__qualname__}")
            raise
        await self._store.complete(request_id, result_digest)
        return result, DispatchReceipt(
            request_id,
            route_id,
            request_digest,
            result_digest,
            decision.matched_constraints,
        )


def _digest(value: JsonValue) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        message = "privacy dispatch payload must be finite JSON"
        raise PrivacyDispatchError(message) from exc
