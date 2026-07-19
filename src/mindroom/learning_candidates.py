"""Verified skill and consent-bound memory producers for governed learning capture."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mindroom.learning_capture import LearningCandidate
from mindroom.learning_loop import LearningLoopError

if TYPE_CHECKING:
    from mindroom.provenance_memory import PortableMemory
    from mindroom.skill_registry import RegistryEntry, SkillTrustRegistry


def candidate_from_signed_skill(
    *,
    source_run_id: str,
    entry: RegistryEntry,
    registry: SkillTrustRegistry,
) -> LearningCandidate:
    """Produce a skill candidate only from exact signature-verified registry evidence."""
    if entry.stage != "signed" or entry.signature is None or entry.sandbox is None or not registry.verify(entry):
        message = "learning skill candidate requires verified signed registry evidence"
        raise LearningLoopError(message)
    artifact: dict[str, object] = {
        "findings": [asdict(finding) for finding in entry.findings],
        "manifest": entry.manifest.model_dump(mode="json"),
        "sandbox": asdict(entry.sandbox),
        "signature": entry.signature,
    }
    return LearningCandidate(source_run_id, "skill", artifact)


def candidate_from_portable_memory(
    *,
    source_run_id: str,
    memory: PortableMemory,
    actor_id: str,
    purpose: str,
    observed_at: datetime,
) -> LearningCandidate:
    """Produce a memory candidate only while its exact purpose-bound consent is valid."""
    observed = _utc(observed_at)
    consent = memory.consent
    if (
        memory.status != "active"
        or not actor_id
        or actor_id != memory.owner_id
        or consent.actor_id != actor_id
        or not purpose
        or purpose != memory.purpose
        or consent.purpose != purpose
        or _utc(consent.granted_at) > observed
        or (consent.expires_at is not None and observed > _utc(consent.expires_at))
        or (memory.expires_at is not None and observed > _utc(memory.expires_at))
        or not memory.citations
    ):
        message = "learning memory candidate requires active cited memory with valid purpose-bound consent"
        raise LearningLoopError(message)
    artifact: dict[str, object] = {
        "citations": [asdict(citation) for citation in memory.citations],
        "consent": {
            "actor_id": consent.actor_id,
            "expires_at": _utc(consent.expires_at).isoformat() if consent.expires_at is not None else None,
            "granted_at": _utc(consent.granted_at).isoformat(),
            "purpose": consent.purpose,
        },
        "content": memory.content,
        "created_at": _utc(memory.created_at).isoformat(),
        "expires_at": _utc(memory.expires_at).isoformat() if memory.expires_at is not None else None,
        "memory_id": memory.memory_id,
        "owner_id": memory.owner_id,
        "purpose": memory.purpose,
        "scope": memory.scope,
        "supersedes": memory.supersedes,
    }
    return LearningCandidate(source_run_id, "memory", artifact)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "learning candidate timestamps must include a timezone"
        raise LearningLoopError(message)
    return value.astimezone(UTC)
