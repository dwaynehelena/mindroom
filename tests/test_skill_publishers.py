"""Tests for hardened OpenClaw and Hermes filesystem canary publishers."""

# ruff: noqa: ANN001, ANN202, D103

from __future__ import annotations

import json

import pytest

from mindroom.skill_publication import SkillPublicationError
from mindroom.skill_publishers import FilesystemCanaryPublisher, FilesystemStablePublisher
from mindroom.skill_registry import (
    SandboxEvidence,
    ScanPolicy,
    SkillTrustRegistry,
    manifest_digest,
    translate_openclaw_skill,
)

pytestmark = pytest.mark.asyncio


def _signed():
    registry = SkillTrustRegistry(b"p" * 32)
    manifest = translate_openclaw_skill({"id": "safe.read", "version": "1", "command": "bin/read"})
    registry.register(manifest)
    registry.scan(manifest.skill_id, manifest.version, ScanPolicy(frozenset(), frozenset(), frozenset()))
    registry.record_sandbox(
        manifest.skill_id,
        manifest.version,
        SandboxEvidence("isolated-runner", manifest_digest(manifest), True, True, True, 3),
    )
    return registry.sign(manifest.skill_id, manifest.version)


async def test_publish_readback_and_receipt_verified_rollback(tmp_path) -> None:
    entry = _signed()
    root = tmp_path / "openclaw-skills"
    root.mkdir()
    publisher = FilesystemCanaryPublisher(root, "openclaw")
    key = f"safe.read:1:openclaw:{entry.signature}"
    receipt = await publisher.publish(entry, key)
    artifact = root / ".mindroom-canary" / "safe.read" / "1.json"
    assert receipt.startswith("sha256:")
    assert json.loads(artifact.read_text())["publication_key"] == key
    await publisher.rollback(entry, receipt)
    assert not artifact.exists()


async def test_publication_is_idempotent_but_equivocation_fails(tmp_path) -> None:
    entry = _signed()
    root = tmp_path / "hermes-skills"
    root.mkdir()
    publisher = FilesystemCanaryPublisher(root, "hermes")
    key = f"safe.read:1:hermes:{entry.signature}"
    receipt = await publisher.publish(entry, key)
    assert await publisher.publish(entry, key) == receipt
    artifact = root / ".mindroom-canary" / "safe.read" / "1.json"
    artifact.write_text("changed")
    with pytest.raises(SkillPublicationError, match="equivocation"):
        await publisher.publish(entry, key)


async def test_runtime_or_signature_substitution_is_denied(tmp_path) -> None:
    entry = _signed()
    root = tmp_path / "skills"
    root.mkdir()
    publisher = FilesystemCanaryPublisher(root, "openclaw")
    with pytest.raises(SkillPublicationError, match="does not match"):
        await publisher.publish(entry, f"safe.read:1:hermes:{entry.signature}")
    with pytest.raises(SkillPublicationError, match="does not match"):
        await publisher.publish(entry, "safe.read:1:openclaw:substituted")


async def test_rollback_refuses_wrong_receipt_and_symlink(tmp_path) -> None:
    entry = _signed()
    root = tmp_path / "skills"
    root.mkdir()
    publisher = FilesystemCanaryPublisher(root, "openclaw")
    key = f"safe.read:1:openclaw:{entry.signature}"
    await publisher.publish(entry, key)
    artifact = root / ".mindroom-canary" / "safe.read" / "1.json"
    with pytest.raises(SkillPublicationError, match="does not match"):
        await publisher.rollback(entry, "sha256:" + "0" * 64)
    artifact.unlink()
    target = tmp_path / "outside"
    target.write_text("outside")
    artifact.symlink_to(target)
    with pytest.raises(SkillPublicationError, match="invalid"):
        await publisher.rollback(entry, "sha256:" + "0" * 64)
    assert target.read_text() == "outside"


async def test_stable_publish_activates_signed_skill_and_rolls_back_exact_receipt(tmp_path) -> None:
    entry = _signed()
    root = tmp_path / "active-skills"
    root.mkdir()
    publisher = FilesystemStablePublisher(root, "openclaw")
    key = f"safe.read:1:openclaw:{entry.signature}"
    receipt = await publisher.publish(entry, key)
    artifact = root / "safe.read" / "SKILL.md"
    content = artifact.read_text()
    assert 'name: "safe.read"' in content
    assert entry.manifest.entrypoint in content
    assert entry.signature in content
    assert await publisher.publish(entry, key) == receipt
    await publisher.rollback(entry, receipt)
    assert not artifact.exists()


async def test_stable_publish_refuses_existing_active_skill_equivocation(tmp_path) -> None:
    entry = _signed()
    root = tmp_path / "active-skills"
    (root / "safe.read").mkdir(parents=True)
    artifact = root / "safe.read" / "SKILL.md"
    artifact.write_text("user-owned skill")
    publisher = FilesystemStablePublisher(root, "hermes")
    key = f"safe.read:1:hermes:{entry.signature}"
    with pytest.raises(SkillPublicationError, match="equivocation"):
        await publisher.publish(entry, key)
    assert artifact.read_text() == "user-owned skill"
