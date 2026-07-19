"""Immutable trusted-skill storage and two-runtime canary publication."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING, Literal

import aiosqlite

from mindroom.skill_registry import (
    PortableSkillManifest,
    RegistryEntry,
    SandboxEvidence,
    ScanFinding,
    SkillTrustRegistry,
)

if TYPE_CHECKING:
    from pathlib import Path

Runtime = Literal["openclaw", "hermes"]
SkillPublisher = Callable[[RegistryEntry, str], Awaitable[str]]
SkillRollback = Callable[[RegistryEntry, str], Awaitable[None]]


class SkillPublicationError(RuntimeError):
    """A trusted-skill persistence or publication invariant failed."""


class TrustedSkillStore:
    """Persist only verified signed evidence and exact runtime receipts."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the immutable trusted-skill registry."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS trusted_skill (
              skill_id TEXT NOT NULL,
              version TEXT NOT NULL,
              entry_json TEXT NOT NULL,
              signature TEXT NOT NULL,
              PRIMARY KEY(skill_id,version)
            );
            CREATE TRIGGER IF NOT EXISTS trusted_skill_no_update
              BEFORE UPDATE ON trusted_skill BEGIN SELECT RAISE(ABORT, 'trusted skills are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trusted_skill_no_delete
              BEFORE DELETE ON trusted_skill BEGIN SELECT RAISE(ABORT, 'trusted skills are immutable'); END;
            CREATE TABLE IF NOT EXISTS skill_publication (
              skill_id TEXT NOT NULL,
              version TEXT NOT NULL,
              runtime TEXT NOT NULL CHECK(runtime IN ('openclaw','hermes')),
              status TEXT NOT NULL CHECK(status IN ('publishing','canary','stable','rolled_back','failed','uncertain')),
              receipt TEXT,
              failure_class TEXT,
              PRIMARY KEY(skill_id,version,runtime)
            );
            """,
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the registry."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def add_verified(self, entry: RegistryEntry, registry: SkillTrustRegistry) -> None:
        """Persist an exact signed evidence bundle only after signature verification."""
        if entry.stage != "signed" or entry.signature is None or entry.sandbox is None or not registry.verify(entry):
            message = "trusted skill persistence requires verified signed evidence"
            raise SkillPublicationError(message)
        entry_json = _entry_json(entry)
        values = (entry.manifest.skill_id, entry.manifest.version, entry_json, entry.signature)
        cursor = await self._required_db().execute(
            "INSERT OR IGNORE INTO trusted_skill VALUES(?,?,?,?)",
            values,
        )
        if cursor.rowcount != 1:
            row = await (
                await self._required_db().execute(
                    "SELECT skill_id,version,entry_json,signature FROM trusted_skill WHERE skill_id=? AND version=?",
                    values[:2],
                )
            ).fetchone()
            if tuple(row or ()) != values:
                message = "trusted skill version equivocation denied"
                raise SkillPublicationError(message)
        await self._required_db().commit()

    async def get(self, skill_id: str, version: str) -> RegistryEntry:
        """Load one immutable verified evidence bundle."""
        row = await (
            await self._required_db().execute(
                "SELECT entry_json FROM trusted_skill WHERE skill_id=? AND version=?",
                (skill_id, version),
            )
        ).fetchone()
        if row is None:
            message = "trusted skill not found"
            raise SkillPublicationError(message)
        return _entry_from_json(str(row[0]))

    async def begin(self, skill_id: str, version: str, runtime: Runtime) -> None:
        """Claim a runtime canary publication once."""
        cursor = await self._required_db().execute(
            "INSERT OR IGNORE INTO skill_publication VALUES(?,?,?,'publishing',NULL,NULL)",
            (skill_id, version, runtime),
        )
        if cursor.rowcount != 1:
            message = "skill runtime publication is not claimable"
            raise SkillPublicationError(message)
        await self._required_db().commit()

    async def settle(
        self,
        skill_id: str,
        version: str,
        runtime: Runtime,
        *,
        status: Literal["canary", "stable", "rolled_back"],
        receipt: str,
        expected: tuple[str, ...],
    ) -> None:
        """Apply a receipt-bound runtime publication transition."""
        if not receipt:
            message = "skill publication receipt is required"
            raise SkillPublicationError(message)
        placeholders = ",".join("?" for _ in expected)
        cursor = await self._required_db().execute(
            f"UPDATE skill_publication SET status=?,receipt=?,failure_class=NULL "  # noqa: S608
            f"WHERE skill_id=? AND version=? AND runtime=? AND status IN ({placeholders})",
            (status, receipt, skill_id, version, runtime, *expected),
        )
        if cursor.rowcount != 1:
            message = "skill publication transition is invalid"
            raise SkillPublicationError(message)
        await self._required_db().commit()

    async def fail(self, skill_id: str, version: str, runtime: Runtime, failure_class: str) -> None:
        """Persist a sanitized known-failure outcome."""
        cursor = await self._required_db().execute(
            "UPDATE skill_publication SET status='failed',failure_class=? "
            "WHERE skill_id=? AND version=? AND runtime=? AND status='publishing'",
            (failure_class[:256], skill_id, version, runtime),
        )
        if cursor.rowcount != 1:
            message = "skill publication failure transition is invalid"
            raise SkillPublicationError(message)
        await self._required_db().commit()

    async def receipts(self, skill_id: str, version: str) -> dict[str, tuple[str, str | None]]:
        """Return content-free per-runtime publication evidence."""
        rows = await (
            await self._required_db().execute(
                "SELECT runtime,status,receipt FROM skill_publication WHERE skill_id=? AND version=?",
                (skill_id, version),
            )
        ).fetchall()
        return {str(row[0]): (str(row[1]), str(row[2]) if row[2] else None) for row in rows}

    async def recover_uncertain(self) -> int:
        """Quarantine interrupted publication attempts without replay."""
        cursor = await self._required_db().execute(
            "UPDATE skill_publication SET status='uncertain' WHERE status='publishing'",
        )
        await self._required_db().commit()
        return cursor.rowcount

    def lock(self) -> asyncio.Lock:
        """Return the cross-runtime publication lock."""
        return self._lock

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "trusted skill store is not open"
            raise RuntimeError(message)
        return self._db


class TrustedSkillPublisher:
    """Canary both runtimes or roll back every partial success."""

    def __init__(
        self,
        store: TrustedSkillStore,
        publishers: Mapping[Runtime, SkillPublisher],
        rollbacks: Mapping[Runtime, SkillRollback],
        stable_publishers: Mapping[Runtime, SkillPublisher],
        stable_rollbacks: Mapping[Runtime, SkillRollback],
    ) -> None:
        required = {"openclaw", "hermes"}
        if any(set(adapters) != required for adapters in (publishers, rollbacks, stable_publishers, stable_rollbacks)):
            message = "trusted skill publishing requires canary and stable adapters for both runtimes"
            raise ValueError(message)
        self._store = store
        self._publishers = dict(publishers)
        self._rollbacks = dict(rollbacks)
        self._stable_publishers = dict(stable_publishers)
        self._stable_rollbacks = dict(stable_rollbacks)

    async def canary(self, skill_id: str, version: str) -> dict[str, tuple[str, str | None]]:
        """Publish exact signed evidence to both canaries or roll back partial success."""
        async with self._store.lock():
            entry = await self._store.get(skill_id, version)
            published: list[tuple[Runtime, str]] = []
            for runtime in ("openclaw", "hermes"):
                await self._store.begin(skill_id, version, runtime)
                try:
                    receipt = await self._publishers[runtime](entry, _publication_key(entry, runtime))
                    await self._store.settle(
                        skill_id,
                        version,
                        runtime,
                        status="canary",
                        receipt=receipt,
                        expected=("publishing",),
                    )
                    published.append((runtime, receipt))
                except Exception as exc:
                    await self._store.fail(
                        skill_id,
                        version,
                        runtime,
                        f"{type(exc).__module__}.{type(exc).__qualname__}",
                    )
                    for successful_runtime, successful_receipt in reversed(published):
                        await self._rollbacks[successful_runtime](entry, successful_receipt)
                        await self._store.settle(
                            skill_id,
                            version,
                            successful_runtime,
                            status="rolled_back",
                            receipt=successful_receipt,
                            expected=("canary",),
                        )
                    message = "skill canary publication failed and partial success was rolled back"
                    raise SkillPublicationError(message) from exc
            return await self._store.receipts(skill_id, version)

    async def stabilize(self, skill_id: str, version: str) -> dict[str, tuple[str, str | None]]:
        """Install both active runtime skills or roll back every partial installation."""
        async with self._store.lock():
            entry = await self._store.get(skill_id, version)
            receipts = await self._store.receipts(skill_id, version)
            if set(receipts) != {"openclaw", "hermes"} or any(
                status not in {"canary", "stable"} or receipt is None for status, receipt in receipts.values()
            ):
                message = "stable skill promotion requires both runtime canaries"
                raise SkillPublicationError(message)
            installed: list[tuple[Runtime, str, str]] = []
            for runtime in ("openclaw", "hermes"):
                previous_status, previous_receipt = receipts[runtime]
                assert previous_receipt is not None
                try:
                    stable_receipt = await self._stable_publishers[runtime](entry, _publication_key(entry, runtime))
                    if previous_status == "canary":
                        await self._store.settle(
                            skill_id,
                            version,
                            runtime,
                            status="stable",
                            receipt=stable_receipt,
                            expected=("canary",),
                        )
                        installed.append((runtime, stable_receipt, previous_receipt))
                except Exception as exc:
                    for installed_runtime, stable_receipt, canary_receipt in reversed(installed):
                        await self._stable_rollbacks[installed_runtime](entry, stable_receipt)
                        await self._store.settle(
                            skill_id,
                            version,
                            installed_runtime,
                            status="canary",
                            receipt=canary_receipt,
                            expected=("stable",),
                        )
                    message = "stable skill installation failed and partial success was rolled back"
                    raise SkillPublicationError(message) from exc
            return await self._store.receipts(skill_id, version)


def _publication_key(entry: RegistryEntry, runtime: Runtime) -> str:
    assert entry.signature is not None
    return f"{entry.manifest.skill_id}:{entry.manifest.version}:{runtime}:{entry.signature}"


def _entry_json(entry: RegistryEntry) -> str:
    assert entry.sandbox is not None
    return json.dumps(
        {
            "findings": [asdict(finding) for finding in entry.findings],
            "manifest": entry.manifest.model_dump(mode="json"),
            "sandbox": asdict(entry.sandbox),
            "signature": entry.signature,
            "stage": "signed",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _entry_from_json(value: str) -> RegistryEntry:
    raw = json.loads(value)
    return RegistryEntry(
        manifest=PortableSkillManifest(**raw["manifest"]),
        stage="signed",
        findings=tuple(ScanFinding(**finding) for finding in raw["findings"]),
        sandbox=SandboxEvidence(**raw["sandbox"]),
        signature=raw["signature"],
    )
