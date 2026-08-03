"""Tests for active governed-learning runtime publishers."""

# ruff: noqa: ANN001, ANN202, D103, EM101, TRY003

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from mindroom.learning_candidates import candidate_from_portable_memory, candidate_from_signed_skill
from mindroom.learning_loop import EvaluationEvidence, LearningLoopError, LearningProposal
from mindroom.learning_stable_publishers import LearningStablePublisher
from mindroom.provenance_memory import ConsentGrant, MemoryCitation, PortableMemory
from mindroom.skill_registry import (
    RegistryEntry,
    SandboxEvidence,
    ScanPolicy,
    SkillTrustRegistry,
    manifest_digest,
    translate_openclaw_skill,
)

pytestmark = pytest.mark.asyncio
NOW = datetime.now(UTC)


def _registry() -> tuple[SkillTrustRegistry, RegistryEntry]:
    registry = SkillTrustRegistry(b"s" * 32)
    manifest = translate_openclaw_skill({"id": "learned.safe", "version": "1", "command": "bin/read"})
    registry.register(manifest)
    registry.scan(manifest.skill_id, manifest.version, ScanPolicy(frozenset(), frozenset(), frozenset()))
    registry.record_sandbox(
        manifest.skill_id,
        manifest.version,
        SandboxEvidence("runner", manifest_digest(manifest), True, True, True, 2),
    )
    return registry, registry.sign(manifest.skill_id, manifest.version)


def _approved(kind, artifact) -> LearningProposal:
    digest = hashlib.sha256(
        json.dumps(artifact, separators=(",", ":"), sort_keys=True).encode(),
    ).hexdigest()
    return LearningProposal(
        proposal_id=f"proposal-{kind}",
        source_run_id="run-1",
        kind=kind,
        artifact=artifact,
        artifact_digest=digest,
        stage="canary",
        proposed_at=NOW,
        evaluation=EvaluationEvidence("suite", digest, 2, 2, 10, 11),
        reviewed_by="@owner:test",
        review_reason="verified",
    )


async def test_signed_skill_is_actively_installed_and_receipt_rolled_back(tmp_path) -> None:
    registry, entry = _registry()
    candidate = candidate_from_signed_skill(source_run_id="run-1", entry=entry, registry=registry)  # type: ignore[arg-type]

    async def unused(_action, _key):
        raise AssertionError("memory handler should not run")

    root = tmp_path / "skills"
    root.mkdir()
    publisher = LearningStablePublisher("openclaw", root, unused, registry)
    proposal = _approved("skill", candidate.artifact)
    receipt = await publisher.publish(proposal)
    assert await publisher.publish(proposal) == receipt
    artifact = root / "learned.safe" / "SKILL.md"
    assert artifact.is_file()
    assert "proposal-skill" in artifact.read_text()
    await publisher.rollback(proposal, receipt)
    assert not artifact.exists()


async def test_skill_refuses_to_adopt_an_existing_non_learning_install(tmp_path) -> None:
    registry, entry = _registry()
    candidate = candidate_from_signed_skill(source_run_id="run-1", entry=entry, registry=registry)

    async def unused(_action, _key):
        return "unused"

    root = tmp_path / "skills"
    (root / "learned.safe").mkdir(parents=True)
    (root / "learned.safe" / "SKILL.md").write_text("pre-existing")
    publisher = LearningStablePublisher("openclaw", root, unused, registry)
    with pytest.raises(LearningLoopError, match="equivocation"):
        await publisher.publish(_approved("skill", candidate.artifact))
    assert (root / "learned.safe" / "SKILL.md").read_text() == "pre-existing"


async def test_memory_uses_native_upsert_and_exact_delete(tmp_path) -> None:
    registry, _entry = _registry()
    memory = PortableMemory(
        memory_id="memory-1",
        owner_id="@owner:test",
        scope="private",
        content="Prefers concise reports",
        purpose="personalization",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        citations=(MemoryCitation("matrix", "$event", "a" * 64),),
        consent=ConsentGrant("@owner:test", "personalization", NOW, NOW + timedelta(hours=1)),
        status="active",
    )
    candidate = candidate_from_portable_memory(
        source_run_id="run-1",
        memory=memory,
        actor_id="@owner:test",
        purpose="personalization",
        observed_at=NOW,
    )
    actions = []

    async def handle(action, key):
        assert action.action_id == key
        actions.append(action)
        return f"native-{action.operation}"

    root = tmp_path / "skills"
    root.mkdir()
    publisher = LearningStablePublisher("hermes", root, handle, registry)
    proposal = _approved("memory", candidate.artifact)
    receipt = await publisher.publish(proposal)
    await publisher.rollback(proposal, receipt)
    assert [action.operation for action in actions] == ["upsert", "delete"]
    assert actions[0].memory_id == actions[1].memory_id
    assert actions[0].payload["content"] == memory.content
    assert actions[1].payload is None


async def test_skill_requires_the_deployment_signature_key(tmp_path) -> None:
    registry, entry = _registry()
    candidate = candidate_from_signed_skill(source_run_id="run-1", entry=entry, registry=registry)  # type: ignore[arg-type]

    async def unused(_action, _key):
        return "unused"

    root = tmp_path / "skills"
    root.mkdir()
    publisher = LearningStablePublisher("openclaw", root, unused, SkillTrustRegistry(b"x" * 32))
    with pytest.raises(LearningLoopError, match="signature verification"):
        await publisher.publish(_approved("skill", candidate.artifact))
