"""Tests for cross-runtime skill translation and trust promotion."""

# ruff: noqa: ANN001, ANN003, ANN202, D103

from __future__ import annotations

import pytest

from mindroom.skill_registry import (
    SandboxEvidence,
    ScanPolicy,
    SkillRegistryError,
    SkillTrustRegistry,
    manifest_digest,
    translate_hermes_skill,
    translate_openclaw_skill,
)


def _openclaw(**overrides):
    value = {
        "id": "calendar.read",
        "version": "1.0.0",
        "command": "bin/calendar-read",
        "permissions": ["calendar:read"],
        "network_hosts": ["calendar.example"],
        "dependencies": ["httpx==1"],
        "configuration": {"api_key": {"env": "API_KEY"}},
    }
    value.update(overrides)
    return translate_openclaw_skill(value)


def _policy() -> ScanPolicy:
    return ScanPolicy(
        allowed_permissions=frozenset({"calendar:read"}),
        allowed_network_hosts=frozenset({"calendar.example"}),
        allowed_dependencies=frozenset({"httpx==1"}),
    )


def test_openclaw_and_hermes_translation_is_strict() -> None:
    openclaw = _openclaw()
    hermes = translate_hermes_skill(
        {
            "id": "research.web",
            "version": "2",
            "entrypoint": "skills/research.py",
            "permissions": [],
            "network_hosts": [],
            "dependencies": [],
        },
    )
    assert openclaw.source_runtime == "openclaw"
    assert hermes.source_runtime == "hermes"
    assert hermes.entrypoint == "skills/research.py"


def test_scan_sandbox_sign_canary_stable_pipeline() -> None:
    registry = SkillTrustRegistry(b"s" * 32)
    manifest = _openclaw()
    registry.register(manifest)
    assert registry.scan(manifest.skill_id, manifest.version, _policy()).stage == "scanned"
    evidence = SandboxEvidence("sandbox-1", manifest_digest(manifest), True, True, True, 12)
    assert registry.record_sandbox(manifest.skill_id, manifest.version, evidence).stage == "tested"
    signed = registry.sign(manifest.skill_id, manifest.version)
    assert registry.verify(signed)
    assert registry.promote(manifest.skill_id, manifest.version, "canary").stage == "canary"
    assert registry.promote(manifest.skill_id, manifest.version, "stable").stage == "stable"


@pytest.mark.parametrize(
    "override",
    [
        {"permissions": ["shell:write"]},
        {"network_hosts": ["evil.example"]},
        {"dependencies": ["unknown==1"]},
        {"command": "../escape"},
        {"configuration": {"api_token": "inline-secret"}},
    ],
)
def test_scan_rejects_permission_network_dependency_path_and_secret_risks(override) -> None:
    registry = SkillTrustRegistry(b"s" * 32)
    manifest = _openclaw(**override)
    registry.register(manifest)
    entry = registry.scan(manifest.skill_id, manifest.version, _policy())
    assert entry.stage == "rejected"
    assert entry.findings


def test_sandbox_and_stage_skipping_fail_closed() -> None:
    registry = SkillTrustRegistry(b"s" * 32)
    manifest = _openclaw()
    registry.register(manifest)
    registry.scan(manifest.skill_id, manifest.version, _policy())
    with pytest.raises(SkillRegistryError, match="sandbox evidence"):
        registry.record_sandbox(
            manifest.skill_id,
            manifest.version,
            SandboxEvidence("sandbox-1", manifest_digest(manifest), False, True, True, 1),
        )
    with pytest.raises(SkillRegistryError, match="does not permit"):
        registry.promote(manifest.skill_id, manifest.version, "canary")
