"""Tests for consent-aware portable memory and propagation behavior."""

# ruff: noqa: ANN001, ANN003, ANN201, ANN202, D103

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from mindroom.provenance_memory import (
    ConsentGrant,
    MemoryCitation,
    MemoryPropagator,
    ProvenanceMemoryError,
    ProvenanceMemoryStore,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 7, 18, tzinfo=UTC)


@pytest_asyncio.fixture
async def store(tmp_path):
    value = ProvenanceMemoryStore(tmp_path / "memory.db")
    await value.open()
    yield value
    await value.close()


def _consent(*, expires_at=None):
    return ConsentGrant("@owner:test", "personalization", NOW, expires_at)


async def _remember(store, memory_id="memory-1", **kwargs):
    return await store.remember(
        memory_id=memory_id,
        owner_id="@owner:test",
        scope="room:!private:test",
        content=kwargs.pop("content", "Prefers concise reports"),
        purpose="personalization",
        citations=(MemoryCitation("matrix", "$source", "a" * 64),),
        consent=kwargs.pop("consent", _consent()),
        created_at=NOW,
        **kwargs,
    )


async def test_consent_scope_ttl_citations_and_export(store: ProvenanceMemoryStore) -> None:
    record = await _remember(store, expires_at=NOW + timedelta(days=1))
    assert record.citations[0].source_event_id == "$source"
    assert await store.export(
        owner_id="@owner:test",
        scope="room:!private:test",
        actor_id="@owner:test",
        purpose="personalization",
        observed_at=NOW + timedelta(hours=1),
    ) == (record,)
    assert await store.export(
        owner_id="@owner:test",
        scope="room:!private:test",
        actor_id="@other:test",
        purpose="personalization",
        observed_at=NOW,
    ) == ()
    assert await store.export(
        owner_id="@owner:test",
        scope="room:!private:test",
        actor_id="@owner:test",
        purpose="personalization",
        observed_at=NOW + timedelta(days=2),
    ) == ()


async def test_contradiction_supersedes_prior_record(store: ProvenanceMemoryStore) -> None:
    await _remember(store)
    replacement = await _remember(
        store,
        memory_id="memory-2",
        content="Prefers detailed reports",
        contradicts="memory-1",
    )
    exported = await store.export(
        owner_id="@owner:test",
        scope="room:!private:test",
        actor_id="@owner:test",
        purpose="personalization",
        observed_at=NOW,
    )
    assert exported == (replacement,)


async def test_delete_propagates_to_all_runtimes(store: ProvenanceMemoryStore) -> None:
    await _remember(store)
    initial = await store.pending_actions()
    assert {(action.target, action.operation) for action in initial} == {
        ("mindroom", "upsert"),
        ("openclaw", "upsert"),
        ("hermes", "upsert"),
    }
    for action in initial:
        await store.mark_delivered(action.action_id)
    await store.delete("memory-1", actor_id="@owner:test", observed_at=NOW)
    deletes = await store.pending_actions()
    assert {(action.target, action.operation) for action in deletes} == {
        ("mindroom", "delete"),
        ("openclaw", "delete"),
        ("hermes", "delete"),
    }
    assert all(action.payload is None for action in deletes)


async def test_expired_or_wrong_purpose_consent_fails_closed(store: ProvenanceMemoryStore) -> None:
    with pytest.raises(ProvenanceMemoryError, match="expired"):
        await _remember(store, consent=_consent(expires_at=NOW - timedelta(seconds=1)))
    with pytest.raises(ProvenanceMemoryError, match="purpose"):
        await _remember(store, consent=ConsentGrant("@owner:test", "analytics", NOW))


async def test_propagator_delivers_all_targets_with_stable_idempotency_keys(store: ProvenanceMemoryStore) -> None:
    await _remember(store)
    calls = []

    async def handler(action, idempotency_key):
        calls.append((action.target, action.operation, idempotency_key))
        return f"{action.target}-receipt"

    propagator = MemoryPropagator(store, dict.fromkeys(("mindroom", "openclaw", "hermes"), handler))
    outcomes = await propagator.drain()
    assert set(outcomes.values()) == {"delivered"}
    assert {target for target, _operation, _key in calls} == {"mindroom", "openclaw", "hermes"}
    assert all(key for _target, _operation, key in calls)
    assert await store.pending_actions() == ()


async def test_propagator_isolates_failure_without_leaking_content(store: ProvenanceMemoryStore) -> None:
    await _remember(store)

    async def success(action, _key):
        return f"{action.target}-receipt"

    async def failure(_action, _key):
        message = "private memory content"
        raise RuntimeError(message)

    handlers = {"mindroom": success, "openclaw": failure, "hermes": success}
    outcomes = await MemoryPropagator(store, handlers).drain()
    failed_id = next(action_id for action_id, status in outcomes.items() if status == "failed")
    assert await store.propagation_status(failed_id) == ("failed", "builtins.RuntimeError", None)
    assert list(outcomes.values()).count("delivered") == 2


async def test_interrupted_propagation_is_uncertain_and_not_pending(store: ProvenanceMemoryStore) -> None:
    await _remember(store, targets=("mindroom",))
    action = (await store.pending_actions())[0]
    await store.claim(action.action_id)
    assert await store.recover_uncertain() == 1
    assert (await store.propagation_status(action.action_id))[0] == "uncertain"
    assert await store.pending_actions() == ()


async def test_original_propagation_schema_migrates_without_losing_rows(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE memory_propagation (
              action_id TEXT PRIMARY KEY,
              memory_id TEXT NOT NULL,
              target TEXT NOT NULL CHECK(target IN ('mindroom','openclaw','hermes')),
              operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
              payload_json TEXT,
              status TEXT NOT NULL CHECK(status IN ('pending','delivered','failed')),
              failure TEXT,
              UNIQUE(memory_id,target,operation)
            );
            INSERT INTO memory_propagation VALUES
              ('legacy-action','memory-1','mindroom','delete',NULL,'pending',NULL);
            """,
        )
    migrated = ProvenanceMemoryStore(path)
    await migrated.open()
    try:
        actions = await migrated.pending_actions()
        assert [action.action_id for action in actions] == ["legacy-action"]
        await migrated.claim("legacy-action")
        await migrated.settle("legacy-action", receipt="legacy-receipt")
        assert await migrated.propagation_status("legacy-action") == (
            "delivered",
            None,
            "legacy-receipt",
        )
    finally:
        await migrated.close()
