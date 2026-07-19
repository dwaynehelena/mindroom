"""Tests for governed proposal evaluation, review, canary and rollback."""

# ruff: noqa: ANN001, ANN201, ANN202, D103, EM101, TRY003

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from mindroom.learning_loop import (
    EvaluationEvidence,
    GovernedPublisher,
    LearningLoopError,
    LearningLoopStore,
)

NOW = datetime(2026, 7, 18, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path):
    value = LearningLoopStore(tmp_path / "learning.db")
    await value.open()
    yield value
    await value.close()


async def _approved(store, proposal_id="proposal-1"):
    proposal = await store.propose(
        proposal_id=proposal_id,
        source_run_id="run-1",
        kind="skill",
        artifact={"id": "skill-1", "version": "1"},
        proposed_at=NOW,
    )
    evidence = EvaluationEvidence("suite-1", proposal.artifact_digest, 10, 10, 90, 91)
    await store.record_evaluation(proposal_id, evidence)
    return await store.review(proposal_id, reviewer_id="@owner:test", approved=True, reason="Verified")


async def test_proposal_requires_exact_nonregressing_evaluation_and_review(store: LearningLoopStore) -> None:
    proposal = await store.propose(
        proposal_id="proposal-1",
        source_run_id="run-1",
        kind="memory",
        artifact={"memory": "concise"},
        proposed_at=NOW,
    )
    with pytest.raises(LearningLoopError, match="non-regression"):
        await store.record_evaluation(
            proposal.proposal_id,
            EvaluationEvidence("suite", proposal.artifact_digest, 10, 9, 90, 91),
        )
    evaluated = await store.record_evaluation(
        proposal.proposal_id,
        EvaluationEvidence("suite", proposal.artifact_digest, 10, 10, 90, 90),
    )
    assert evaluated.stage == "evaluated"
    reviewed = await store.review(
        proposal.proposal_id,
        reviewer_id="@owner:test",
        approved=True,
        reason="Regression suite passed",
    )
    assert reviewed.stage == "approved"
    assert reviewed.reviewed_by == "@owner:test"


async def test_two_runtime_canary_then_stable(store: LearningLoopStore) -> None:
    await _approved(store)
    published = []
    stabilized = []

    async def publish(proposal):
        published.append(proposal.proposal_id)
        return f"receipt-{len(published)}"

    async def rollback(_proposal, _receipt):
        raise AssertionError("rollback should not run")

    async def stable_publish(proposal):
        stabilized.append(proposal.proposal_id)
        return f"stable-receipt-{len(stabilized)}"

    publisher = GovernedPublisher(
        store,
        {"openclaw": publish, "hermes": publish},
        {"openclaw": rollback, "hermes": rollback},
        {"openclaw": stable_publish, "hermes": stable_publish},
        {"openclaw": rollback, "hermes": rollback},
    )
    assert (await publisher.canary("proposal-1")).stage == "canary"
    assert set(await store.receipts("proposal-1")) == {"openclaw", "hermes"}
    assert (await publisher.stabilize("proposal-1")).stage == "stable"
    assert stabilized == ["proposal-1", "proposal-1"]


async def test_partial_stable_failure_restores_canary_receipts(store: LearningLoopStore) -> None:
    await _approved(store)
    rolled_back = []

    async def canary(proposal):
        return f"canary-{proposal.proposal_id}-{len(await store.receipts(proposal.proposal_id))}"

    async def unused_rollback(_proposal, _receipt):
        raise AssertionError("canary rollback should not run")

    async def openclaw_stable(_proposal):
        return "openclaw-stable"

    async def hermes_stable(_proposal):
        raise RuntimeError("stable publish failed")

    async def stable_rollback(_proposal, receipt):
        rolled_back.append(receipt)

    publisher = GovernedPublisher(
        store,
        {"openclaw": canary, "hermes": canary},
        {"openclaw": unused_rollback, "hermes": unused_rollback},
        {"openclaw": openclaw_stable, "hermes": hermes_stable},
        {"openclaw": stable_rollback, "hermes": stable_rollback},
    )
    await publisher.canary("proposal-1")
    canary_receipts = await store.receipts("proposal-1")
    with pytest.raises(LearningLoopError, match="partial installation"):
        await publisher.stabilize("proposal-1")
    assert rolled_back == ["openclaw-stable"]
    assert await store.receipts("proposal-1") == canary_receipts
    assert (await store.get("proposal-1")).stage == "canary"


async def test_stable_requires_active_adapters(store: LearningLoopStore) -> None:
    await _approved(store)

    async def publish(_proposal):
        return "receipt"

    async def rollback(_proposal, _receipt):
        return None

    publisher = GovernedPublisher(
        store,
        {"openclaw": publish, "hermes": publish},
        {"openclaw": rollback, "hermes": rollback},
    )
    await publisher.canary("proposal-1")
    with pytest.raises(LearningLoopError, match="active runtime"):
        await publisher.stabilize("proposal-1")


async def test_stable_resume_skips_a_runtime_with_persisted_stable_receipt(store: LearningLoopStore) -> None:
    await _approved(store)
    stable_calls = []

    async def publish(proposal):
        return f"canary-{proposal.proposal_id}"

    async def rollback(_proposal, _receipt):
        return None

    async def stable(proposal):
        stable_calls.append(proposal.proposal_id)
        return "hermes-stable"

    publisher = GovernedPublisher(
        store,
        {"openclaw": publish, "hermes": publish},
        {"openclaw": rollback, "hermes": rollback},
        {"openclaw": stable, "hermes": stable},
        {"openclaw": rollback, "hermes": rollback},
    )
    await publisher.canary("proposal-1")
    await store.publication("proposal-1", "openclaw", receipt="openclaw-stable", status="stable")
    assert (await publisher.stabilize("proposal-1")).stage == "stable"
    assert stable_calls == ["proposal-1"]


async def test_failed_stable_rollback_is_quarantined_as_uncertain(store: LearningLoopStore) -> None:
    await _approved(store)

    async def canary(_proposal):
        return "canary"

    async def unused_rollback(_proposal, _receipt):
        return None

    async def openclaw_stable(_proposal):
        return "openclaw-stable"

    async def hermes_stable(_proposal):
        raise RuntimeError("stable failure")

    async def failed_rollback(_proposal, _receipt):
        raise RuntimeError("rollback failure")

    publisher = GovernedPublisher(
        store,
        {"openclaw": canary, "hermes": canary},
        {"openclaw": unused_rollback, "hermes": unused_rollback},
        {"openclaw": openclaw_stable, "hermes": hermes_stable},
        {"openclaw": failed_rollback, "hermes": failed_rollback},
    )
    await publisher.canary("proposal-1")
    with pytest.raises(LearningLoopError, match="uncertain"):
        await publisher.stabilize("proposal-1")
    assert (await store.get("proposal-1")).stage == "uncertain"
    assert (await store.receipts("proposal-1"))["openclaw"][0] == "uncertain"


async def test_version_one_schema_migrates_uncertainty_without_losing_proposals(tmp_path) -> None:
    database = tmp_path / "learning-v1.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE learning_proposal (
              proposal_id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('skill','memory')), artifact_json TEXT NOT NULL,
              artifact_digest TEXT NOT NULL,
              stage TEXT NOT NULL CHECK(stage IN ('proposed','evaluated','approved','rejected','canary','stable','rolled_back')),
              proposed_at TEXT NOT NULL, evaluation_json TEXT, reviewed_by TEXT, review_reason TEXT
            );
            CREATE TABLE learning_publication (
              proposal_id TEXT NOT NULL,
              runtime TEXT NOT NULL CHECK(runtime IN ('openclaw','hermes')),
              receipt TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('canary','stable','rolled_back')),
              PRIMARY KEY(proposal_id,runtime)
            );
            """,
        )
        artifact = json.dumps({"safe": True}, separators=(",", ":"), sort_keys=True)
        connection.execute(
            "INSERT INTO learning_proposal VALUES(?,?,?,?,?,'proposed',?,NULL,NULL,NULL)",
            ("proposal-v1", "run-v1", "skill", artifact, "digest", NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO learning_publication VALUES('proposal-v1','openclaw','receipt','canary')",
        )
    migrated = LearningLoopStore(database)
    await migrated.open()
    try:
        assert (await migrated.get("proposal-v1")).source_run_id == "run-v1"
        assert (await migrated.receipts("proposal-v1"))["openclaw"] == ("canary", "receipt")
        await migrated.publication("proposal-v1", "openclaw", receipt="receipt", status="uncertain")
    finally:
        await migrated.close()


async def test_partial_canary_failure_rolls_back_and_blocks_stable(store: LearningLoopStore) -> None:
    await _approved(store)
    rolled_back = []

    async def openclaw(_proposal):
        return "openclaw-receipt"

    async def hermes(_proposal):
        raise RuntimeError("publish failed")

    async def rollback(_proposal, receipt):
        rolled_back.append(receipt)

    publisher = GovernedPublisher(
        store,
        {"openclaw": openclaw, "hermes": hermes},
        {"openclaw": rollback, "hermes": rollback},
    )
    with pytest.raises(LearningLoopError, match="rolled back"):
        await publisher.canary("proposal-1")
    assert rolled_back == ["openclaw-receipt"]
    assert (await store.get("proposal-1")).stage == "rolled_back"
    with pytest.raises(LearningLoopError, match="requires"):
        await publisher.stabilize("proposal-1")


async def test_rejected_review_cannot_publish(store: LearningLoopStore) -> None:
    proposal = await store.propose(
        proposal_id="proposal-1",
        source_run_id="run-1",
        kind="skill",
        artifact={"id": "unsafe"},
        proposed_at=NOW,
    )
    await store.record_evaluation(
        proposal.proposal_id,
        EvaluationEvidence("suite", proposal.artifact_digest, 1, 1, 1, 1),
    )
    await store.review(proposal.proposal_id, reviewer_id="@owner:test", approved=False, reason="Unsafe")

    async def unused(_proposal):
        return "unused"

    async def rollback(_proposal, _receipt):
        return None

    publisher = GovernedPublisher(
        store,
        {"openclaw": unused, "hermes": unused},
        {"openclaw": rollback, "hermes": rollback},
    )
    with pytest.raises(LearningLoopError, match="approved"):
        await publisher.canary(proposal.proposal_id)
