"""Tests for governed-learning production filesystem canary adapters."""

# ruff: noqa: ANN001, D103

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from mindroom.learning_loop import EvaluationEvidence, LearningLoopError, LearningProposal
from mindroom.learning_publishers import LearningFilesystemPublisher

pytestmark = pytest.mark.asyncio


def _approved() -> LearningProposal:
    artifact = {"id": "safe-learning", "version": "1"}
    digest = hashlib.sha256(json.dumps(artifact, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    return LearningProposal(
        proposal_id="proposal/../../../unsafe",
        source_run_id="run-1",
        kind="skill",
        artifact=artifact,
        artifact_digest=digest,
        stage="approved",
        proposed_at=datetime(2026, 7, 18, tzinfo=UTC),
        evaluation=EvaluationEvidence("suite", digest, 2, 2, 10, 11),
        reviewed_by="@owner:test",
        review_reason="verified",
    )


async def test_publish_hashes_untrusted_id_and_rolls_back_by_receipt(tmp_path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    proposal = _approved()
    publisher = LearningFilesystemPublisher(root, "openclaw")
    receipt = await publisher.publish(proposal)
    artifact = publisher._artifact(proposal)
    assert artifact.parent == root / ".mindroom-learning-canary"
    assert proposal.proposal_id not in artifact.name
    assert artifact.is_file()
    await publisher.rollback(proposal, receipt)
    assert not artifact.exists()


async def test_publish_refuses_unapproved_or_digest_substituted_proposal(tmp_path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    publisher = LearningFilesystemPublisher(root, "hermes")
    proposal = _approved()
    with pytest.raises(LearningLoopError, match="approved"):
        await publisher.publish(replace(proposal, stage="evaluated"))
    with pytest.raises(LearningLoopError, match="exact"):
        await publisher.publish(replace(proposal, artifact={"changed": True}))


async def test_runtime_bound_payloads_have_distinct_receipts(tmp_path) -> None:
    proposal = _approved()
    roots = (tmp_path / "openclaw", tmp_path / "hermes")
    for root in roots:
        root.mkdir()
    first = LearningFilesystemPublisher(roots[0], "openclaw")
    second = LearningFilesystemPublisher(roots[1], "hermes")
    assert await first.publish(proposal) != await second.publish(proposal)


async def test_rollback_rejects_wrong_receipt_and_symlink(tmp_path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    proposal = _approved()
    publisher = LearningFilesystemPublisher(root, "openclaw")
    await publisher.publish(proposal)
    with pytest.raises(LearningLoopError, match="does not match"):
        await publisher.rollback(proposal, "sha256:" + "0" * 64)
    artifact = publisher._artifact(proposal)
    artifact.unlink()
    outside = tmp_path / "outside"
    outside.write_text("outside")
    artifact.symlink_to(outside)
    with pytest.raises(LearningLoopError, match="invalid"):
        await publisher.rollback(proposal, "sha256:" + "0" * 64)
    assert outside.read_text() == "outside"
