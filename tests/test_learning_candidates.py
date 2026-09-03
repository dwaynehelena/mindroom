"""Tests for verified governed-learning candidate producers."""

# ruff: noqa: D103

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mindroom.learning_candidates import candidate_from_portable_memory, candidate_from_signed_skill
from mindroom.learning_loop import LearningLoopError
from mindroom.provenance_memory import ConsentGrant, MemoryCitation, PortableMemory
from mindroom.skill_registry import (
    RegistryEntry,
    SandboxEvidence,
    ScanPolicy,
    SkillTrustRegistry,
    manifest_digest,
    translate_openclaw_skill,
)

NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _signed() -> tuple[SkillTrustRegistry, RegistryEntry]:
    registry = SkillTrustRegistry(b"s" * 32)
    manifest = translate_openclaw_skill({"id": "safe.read", "version": "1", "command": "bin/read"})
    registry.register(manifest)
    registry.scan(manifest.skill_id, manifest.version, ScanPolicy(frozenset(), frozenset(), frozenset()))
    registry.record_sandbox(
        manifest.skill_id,
        manifest.version,
        SandboxEvidence("runner", manifest_digest(manifest), True, True, True, 2),
    )
    return registry, registry.sign(manifest.skill_id, manifest.version)


def _memory() -> PortableMemory:
    return PortableMemory(
        memory_id="memory-1",
        owner_id="@owner:test",
        scope="room:!private:test",
        content="Prefers concise reports",
        purpose="personalization",
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
        citations=(MemoryCitation("matrix", "$source", "a" * 64),),
        consent=ConsentGrant("@owner:test", "personalization", NOW, NOW + timedelta(days=1)),
        status="active",
    )


def test_signed_skill_producer_preserves_exact_registry_evidence() -> None:
    registry, entry = _signed()
    candidate = candidate_from_signed_skill(source_run_id="run-1", entry=entry, registry=registry)
    assert candidate.kind == "skill"
    assert candidate.artifact["manifest"] == entry.manifest.model_dump(mode="json")
    assert candidate.artifact["signature"] == entry.signature


def test_skill_producer_rejects_unsigned_or_wrong_registry() -> None:
    registry, entry = _signed()
    with pytest.raises(LearningLoopError, match="verified signed"):
        candidate_from_signed_skill(source_run_id="run-1", entry=replace(entry, signature=None), registry=registry)
    with pytest.raises(LearningLoopError, match="verified signed"):
        candidate_from_signed_skill(
            source_run_id="run-1",
            entry=entry,
            registry=SkillTrustRegistry(b"x" * 32),
        )


def test_memory_producer_preserves_scope_citations_and_expiry() -> None:
    memory = _memory()
    candidate = candidate_from_portable_memory(
        source_run_id="run-1",
        memory=memory,
        actor_id="@owner:test",
        purpose="personalization",
        observed_at=NOW,
    )
    assert candidate.kind == "memory"
    assert candidate.artifact["scope"] == memory.scope
    assert candidate.artifact["citations"] == [
        {"source": "matrix", "source_event_id": "$source", "content_digest": "a" * 64},
    ]
    assert candidate.artifact["consent"] == {
        "actor_id": "@owner:test",
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "granted_at": NOW.isoformat(),
        "purpose": "personalization",
    }


@pytest.mark.parametrize("invalid", ["actor", "purpose", "expired", "deleted", "uncited"])
def test_memory_producer_fails_closed_without_current_consent_and_citation(invalid: str) -> None:
    memory = _memory()
    actor = "@owner:test"
    purpose = "personalization"
    observed = NOW
    if invalid == "actor":
        actor = "@other:test"
    elif invalid == "purpose":
        purpose = "analytics"
    elif invalid == "expired":
        observed = NOW + timedelta(days=2)
    elif invalid == "deleted":
        memory = replace(memory, status="deleted")
    else:
        memory = replace(memory, citations=())
    with pytest.raises(LearningLoopError, match="purpose-bound consent"):
        candidate_from_portable_memory(
            source_run_id="run-1",
            memory=memory,
            actor_id=actor,
            purpose=purpose,
            observed_at=observed,
        )
