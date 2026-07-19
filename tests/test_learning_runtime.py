"""Tests for runtime candidate capture and live exact-payload learning review."""

# ruff: noqa: ANN001, ANN003, ANN202, D103

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from mindroom.approval_manager import ApprovalDecision
from mindroom.constants import RuntimePaths, tracking_dir
from mindroom.flight_recorder import FlightRecorder
from mindroom.learning_capture import LearningCandidate
from mindroom.learning_loop import EvaluationEvidence, LearningLoopError, LearningLoopStore
from mindroom.learning_runtime import (
    GovernedLearningRuntime,
    LearningReviewContext,
    RuntimeLearningCandidateEvent,
    capture_runtime_learning_event,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(tmp_path / "config.yaml", tmp_path, tmp_path / ".env", tmp_path / "data")


async def _source_run(paths: RuntimePaths, run_id: str) -> None:
    recorder = FlightRecorder(tracking_dir(paths) / "flight_recorder.db")
    await recorder.open()
    try:
        await recorder.append(
            run_id=run_id,
            kind="message",
            payload={
                "direction": "outbound",
                "is_visible_response": True,
                "status": "completed",
                "suppressed": False,
            },
            side_effect=False,
            occurred_at=NOW,
        )
    finally:
        await recorder.close()


async def _evaluated(paths: RuntimePaths):
    await _source_run(paths, "run-1")
    event = RuntimeLearningCandidateEvent(
        "mindroom",
        LearningCandidate("run-1", "skill", {"safe": "candidate"}),
        NOW,
    )
    proposal = await capture_runtime_learning_event(paths, event)
    store = LearningLoopStore(tracking_dir(paths) / "learning_loop.db")
    await store.open()
    await store.record_evaluation(
        proposal.proposal_id,
        EvaluationEvidence("suite", proposal.artifact_digest, 2, 2, 10, 11),
    )
    return store, proposal


async def test_runtime_candidate_event_is_automatically_captured_idempotently(tmp_path) -> None:
    paths = _paths(tmp_path)
    await _source_run(paths, "run-1")
    event = RuntimeLearningCandidateEvent("hermes", LearningCandidate("run-1", "memory", {"fact": "safe"}), NOW)
    first = await capture_runtime_learning_event(paths, event)
    second = await capture_runtime_learning_event(paths, event)
    assert first == second
    assert first.stage == "proposed"


async def test_live_review_binds_exact_evidence_and_attributes_matrix_actor(tmp_path) -> None:
    paths = _paths(tmp_path)
    store, proposal = await _evaluated(paths)
    calls = []

    class Approvals:
        async def request_approval(self, **kwargs):
            calls.append(kwargs)
            return ApprovalDecision("approved", "Reviewed live", "@owner:test", NOW)

    try:
        runtime = GovernedLearningRuntime(runtime_paths=paths, store=store, approvals=Approvals())
        reviewed = await runtime.review(
            proposal.proposal_id,
            LearningReviewContext("!review:test", "@owner:test", "@owner:test", "$thread", 30),
        )
        assert reviewed.stage == "approved"
        assert reviewed.reviewed_by == "@owner:test"
        assert calls[0]["tool_name"] == "mindroom.learning.promote"
        assert calls[0]["arguments"]["artifact_digest"] == proposal.artifact_digest
        assert calls[0]["arguments"]["tests_run"] == 2
        assert "artifact" not in calls[0]["arguments"]
    finally:
        await store.close()


async def test_expired_live_review_does_not_change_governance_state(tmp_path) -> None:
    paths = _paths(tmp_path)
    store, proposal = await _evaluated(paths)

    class Approvals:
        async def request_approval(self, **_kwargs):
            return ApprovalDecision("expired", "Timed out", None, NOW)

    try:
        runtime = GovernedLearningRuntime(runtime_paths=paths, store=store, approvals=Approvals())
        with pytest.raises(LearningLoopError, match="expired"):
            await runtime.review(
                proposal.proposal_id,
                LearningReviewContext("!review:test", "@owner:test", "@owner:test"),
            )
        assert (await store.get(proposal.proposal_id)).stage == "evaluated"
    finally:
        await store.close()
