"""Durable, owner-authenticated native-Matrix milestone relay outbox."""
# ruff: noqa: D101, D102, D204, E701, E702, EM101, TC003, TRY003, TRY300, TRY301
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from mindroom.delivery_gateway import SendTextRequest
from mindroom.message_target import MessageTarget
from mindroom.redaction import redact_sensitive_text

PORTAL_ROOM_ID = "!kNowWhbQOKJMwCNzqB:localhost"
MAX_MILESTONE_BYTES = 16 * 1024

@dataclass(frozen=True, slots=True)
class MilestoneRelayEntry:
    key: str
    sequence: int
    room_id: str
    body: str
    project: str = "project-1"
    ignore_mentions: bool = True

def milestone_entry(*, sequence: int, body: str, authenticated_owner_id: str,
                    owner_user_ids: tuple[str, ...], room_id: str = PORTAL_ROOM_ID,
                    project: str = "project-1") -> MilestoneRelayEntry:
    """Derive authorization from the trusted principal, with no caller boolean."""
    if authenticated_owner_id not in frozenset(owner_user_ids):
        raise PermissionError("milestone relay owner identity is not allowlisted")
    if sequence < 1 or room_id != PORTAL_ROOM_ID or not project:
        raise ValueError("invalid milestone project, sequence, or fixed portal")
    body = redact_sensitive_text(body).strip()
    if not body or len(body.encode()) > MAX_MILESTONE_BYTES:
        raise ValueError("milestone body is blank or exceeds the bound")
    digest = hashlib.sha256(f"{project}\x1f{sequence}\x1f{body}".encode()).hexdigest()
    return MilestoneRelayEntry(f"milestone-{digest}", sequence, room_id, body, project)

class MilestoneRelayStore:
    """Transactional strict-monotonic relay ledger and same-txn outbox."""
    def __init__(self, path: Path) -> None:
        self.path, self.db, self.lock = path, None, asyncio.Lock()
    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        await self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS relay(
          project TEXT NOT NULL, sequence INTEGER NOT NULL, owner_id TEXT NOT NULL,
          room_id TEXT NOT NULL, body TEXT NOT NULL, txn_id TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL CHECK(status IN ('response_ready','delivering','delivered','failed')),
          event_id TEXT, failure TEXT, PRIMARY KEY(project,sequence));""")
        await self.db.commit()
    async def close(self) -> None:
        if self.db is not None: await self.db.close(); self.db = None
    async def enqueue(self, entry: MilestoneRelayEntry, *, authenticated_owner_id: str) -> bool:
        async with self.lock:
            assert self.db is not None
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                current = await (await self.db.execute(
                    "SELECT MAX(sequence) FROM relay WHERE project=?", (entry.project,))).fetchone()
                existing = await (await self.db.execute(
                    "SELECT owner_id,room_id,body,txn_id FROM relay WHERE project=? AND sequence=?",
                    (entry.project, entry.sequence))).fetchone()
                if existing:
                    expected = (authenticated_owner_id, entry.room_id, entry.body, entry.key)
                    if tuple(existing) != expected: raise ValueError("milestone equivocation denied")
                    await self.db.commit(); return False
                if current and current[0] is not None and entry.sequence != int(current[0]) + 1:
                    raise ValueError("milestone sequence must be strictly monotonic")
                await self.db.execute("INSERT INTO relay VALUES(?,?,?,?,?,?,'response_ready',NULL,NULL)",
                    (entry.project, entry.sequence, authenticated_owner_id, entry.room_id, entry.body, entry.key))
                await self.db.commit(); return True
            except BaseException:
                await self.db.rollback(); raise
    async def recover(self) -> tuple[MilestoneRelayEntry, ...]:
        assert self.db is not None
        rows = await (await self.db.execute("SELECT project,sequence,room_id,body,txn_id FROM relay WHERE status IN ('response_ready','delivering') ORDER BY project,sequence")).fetchall()
        return tuple(MilestoneRelayEntry(r[4], r[1], r[2], r[3], r[0]) for r in rows)
    async def transition(self, entry: MilestoneRelayEntry, source: str, target: str, *, event_id: str | None = None) -> None:
        assert self.db is not None
        cursor = await self.db.execute("UPDATE relay SET status=?,event_id=COALESCE(?,event_id) WHERE project=? AND sequence=? AND status=?",
            (target,event_id,entry.project,entry.sequence,source))
        if cursor.rowcount != 1: await self.db.rollback(); raise RuntimeError("invalid relay transition")
        await self.db.commit()

class MilestoneRelay:
    """Deliver persisted entries through the E2EE-native gateway only."""
    def __init__(self, store: MilestoneRelayStore, gateway: object, room_encrypted: object) -> None:
        self.store, self.gateway, self.room_encrypted = store, gateway, room_encrypted
    async def recover(self) -> tuple[str, ...]:
        delivered=[]
        for entry in await self.store.recover():
            if not self.room_encrypted(entry.room_id): raise RuntimeError("milestone portal E2EE is not authoritative")
            # Redact again immediately before deterministic payload construction.
            body = redact_sensitive_text(entry.body).strip()
            if not body or len(body.encode()) > MAX_MILESTONE_BYTES: raise RuntimeError("relay body invalid at send")
            await self.store.transition(entry, "response_ready", "delivering") if await self._is_ready(entry) else None
            event_id = await self.gateway.send_text(SendTextRequest(
                target=MessageTarget.resolve(entry.room_id,None,entry.key,room_mode=True), response_text=body,
                skip_mentions=True, transaction_id=entry.key,
                extra_content={"org.mindroom.milestone": {"project": entry.project,"sequence": entry.sequence}}))
            if not event_id: raise RuntimeError("milestone native Matrix delivery returned no event ID")
            await self.store.transition(entry,"delivering","delivered",event_id=event_id); delivered.append(event_id)
        return tuple(delivered)
    async def _is_ready(self, entry: MilestoneRelayEntry) -> bool:
        assert self.store.db is not None
        row=await (await self.store.db.execute("SELECT status FROM relay WHERE project=? AND sequence=?",(entry.project,entry.sequence))).fetchone()
        return bool(row and row[0]=="response_ready")
