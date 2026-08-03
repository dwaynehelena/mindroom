"""Cross-runtime skill translation, scanning, attestation and staged promotion."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SkillRuntime = Literal["mindroom", "openclaw", "hermes"]
PromotionStage = Literal["translated", "scanned", "tested", "signed", "canary", "stable", "rejected"]


class SkillRegistryError(RuntimeError):
    """A skill trust, provenance, or promotion invariant failed."""


class PortableSkillManifest(BaseModel):
    """Strict runtime-neutral skill metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    version: str = Field(min_length=1, max_length=64)
    source_runtime: SkillRuntime
    entrypoint: str = Field(min_length=1, max_length=512)
    permissions: tuple[str, ...] = ()
    network_hosts: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    configuration: dict[str, object] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScanPolicy:
    """Explicit permission, host and dependency bounds."""

    allowed_permissions: frozenset[str]
    allowed_network_hosts: frozenset[str]
    allowed_dependencies: frozenset[str]


@dataclass(frozen=True, slots=True)
class ScanFinding:
    """One deterministic trust-policy violation."""

    category: Literal["permission", "secret", "network", "dependency", "entrypoint"]
    detail: str


@dataclass(frozen=True, slots=True)
class SandboxEvidence:
    """Content-free evidence from an isolated skill test run."""

    runner_id: str
    manifest_digest: str
    isolated: bool
    network_disabled: bool
    passed: bool
    test_count: int


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """Current immutable evidence bundle for one skill version."""

    manifest: PortableSkillManifest
    stage: PromotionStage
    findings: tuple[ScanFinding, ...] = ()
    sandbox: SandboxEvidence | None = None
    signature: str | None = None


class SkillTrustRegistry:
    """Enforce scan, sandbox, signature and promotion gates."""

    def __init__(self, signing_key: bytes) -> None:
        if len(signing_key) < 32:
            message = "skill registry signing key must contain at least 32 bytes"
            raise ValueError(message)
        self._signing_key = signing_key
        self._entries: dict[tuple[str, str], RegistryEntry] = {}

    def register(self, manifest: PortableSkillManifest) -> RegistryEntry:
        """Register an exact translated manifest idempotently."""
        key = (manifest.skill_id, manifest.version)
        existing = self._entries.get(key)
        if existing is not None:
            if existing.manifest != manifest:
                message = "skill version equivocation denied"
                raise SkillRegistryError(message)
            return existing
        entry = RegistryEntry(manifest, "translated")
        self._entries[key] = entry
        return entry

    def scan(self, skill_id: str, version: str, policy: ScanPolicy) -> RegistryEntry:
        """Scan permissions, inline secrets, network, dependencies and entrypoint shape."""
        entry = self._entry(skill_id, version)
        self._require_stage(entry, {"translated", "scanned"})
        manifest = entry.manifest
        findings: list[ScanFinding] = []
        findings.extend(
            ScanFinding("permission", permission)
            for permission in manifest.permissions
            if permission not in policy.allowed_permissions
        )
        findings.extend(
            ScanFinding("network", host)
            for host in manifest.network_hosts
            if host not in policy.allowed_network_hosts
        )
        findings.extend(
            ScanFinding("dependency", dependency)
            for dependency in manifest.dependencies
            if dependency not in policy.allowed_dependencies
        )
        if manifest.entrypoint.startswith(("/", "~")) or ".." in manifest.entrypoint.split("/"):
            findings.append(ScanFinding("entrypoint", "entrypoint must be relative and traversal-free"))
        findings.extend(ScanFinding("secret", path) for path in _secret_paths(manifest.configuration))
        updated = RegistryEntry(manifest, "rejected" if findings else "scanned", tuple(sorted(findings, key=str)))
        self._entries[(skill_id, version)] = updated
        return updated

    def record_sandbox(self, skill_id: str, version: str, evidence: SandboxEvidence) -> RegistryEntry:
        """Accept only matching isolated, network-disabled, passing sandbox evidence."""
        entry = self._entry(skill_id, version)
        self._require_stage(entry, {"scanned", "tested"})
        if (
            evidence.manifest_digest != manifest_digest(entry.manifest)
            or not evidence.runner_id
            or not evidence.isolated
            or not evidence.network_disabled
            or not evidence.passed
            or evidence.test_count < 1
        ):
            message = "sandbox evidence does not prove an isolated passing test"
            raise SkillRegistryError(message)
        updated = RegistryEntry(entry.manifest, "tested", entry.findings, evidence)
        self._entries[(skill_id, version)] = updated
        return updated

    def sign(self, skill_id: str, version: str) -> RegistryEntry:
        """Sign the exact manifest plus sandbox evidence with the deployment key."""
        entry = self._entry(skill_id, version)
        self._require_stage(entry, {"tested", "signed"})
        assert entry.sandbox is not None
        signature = hmac.new(self._signing_key, _signature_payload(entry), hashlib.sha256).hexdigest()
        updated = RegistryEntry(entry.manifest, "signed", entry.findings, entry.sandbox, signature)
        self._entries[(skill_id, version)] = updated
        return updated

    def promote(self, skill_id: str, version: str, target: Literal["canary", "stable"]) -> RegistryEntry:
        """Promote signed evidence to canary, then stable; skipping stages is forbidden."""
        entry = self._entry(skill_id, version)
        expected = "signed" if target == "canary" else "canary"
        self._require_stage(entry, {expected})
        if not self.verify(entry):
            message = "skill provenance signature verification failed"
            raise SkillRegistryError(message)
        updated = RegistryEntry(entry.manifest, target, entry.findings, entry.sandbox, entry.signature)
        self._entries[(skill_id, version)] = updated
        return updated

    def verify(self, entry: RegistryEntry) -> bool:
        """Verify a signed evidence bundle without changing registry state."""
        if entry.signature is None or entry.sandbox is None:
            return False
        expected = hmac.new(
            self._signing_key,
            _signature_payload(RegistryEntry(entry.manifest, "tested", entry.findings, entry.sandbox)),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(entry.signature, expected)

    def _entry(self, skill_id: str, version: str) -> RegistryEntry:
        try:
            return self._entries[(skill_id, version)]
        except KeyError as exc:
            message = "skill version is not registered"
            raise SkillRegistryError(message) from exc

    @staticmethod
    def _require_stage(entry: RegistryEntry, allowed: set[PromotionStage]) -> None:
        if entry.stage not in allowed:
            message = f"skill stage {entry.stage!r} does not permit this transition"
            raise SkillRegistryError(message)


def translate_openclaw_skill(value: dict[str, object]) -> PortableSkillManifest:
    """Translate an OpenClaw skill descriptor into the portable manifest."""
    return _translate(value, runtime="openclaw", entrypoint_key="command")


def translate_hermes_skill(value: dict[str, object]) -> PortableSkillManifest:
    """Translate a Hermes skill descriptor into the portable manifest."""
    return _translate(value, runtime="hermes", entrypoint_key="entrypoint")


def manifest_digest(manifest: PortableSkillManifest) -> str:
    """Return a deterministic digest for sandbox and signing evidence."""
    return hashlib.sha256(_json(manifest.model_dump(mode="json"))).hexdigest()


def _translate(
    value: dict[str, object],
    *,
    runtime: SkillRuntime,
    entrypoint_key: str,
) -> PortableSkillManifest:
    def strings(key: str) -> tuple[str, ...]:
        raw = value.get(key, ())
        if not isinstance(raw, (list, tuple)) or any(not isinstance(item, str) for item in raw):
            message = f"skill field {key!r} must be a string list"
            raise SkillRegistryError(message)
        return tuple(raw)

    configuration = value.get("configuration", {})
    if not isinstance(configuration, dict):
        message = "skill configuration must be an object"
        raise SkillRegistryError(message)
    return PortableSkillManifest(
        skill_id=value.get("id"),
        version=value.get("version"),
        source_runtime=runtime,
        entrypoint=value.get(entrypoint_key),
        permissions=strings("permissions"),
        network_hosts=strings("network_hosts"),
        dependencies=strings("dependencies"),
        configuration=configuration,
    )


def _secret_paths(value: object, path: str = "configuration") -> tuple[str, ...]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            normalized = str(key).lower().replace("-", "_")
            if any(
                marker in normalized for marker in ("password", "secret", "token", "api_key", "private_key")
            ) and item not in (None, "", {"env": str(key).upper()}):
                findings.append(child)
            findings.extend(_secret_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_secret_paths(item, f"{path}[{index}]"))
    return tuple(findings)


def _signature_payload(entry: RegistryEntry) -> bytes:
    assert entry.sandbox is not None
    return _json(
        {
            "manifest": entry.manifest.model_dump(mode="json"),
            "sandbox": {
                "isolated": entry.sandbox.isolated,
                "manifest_digest": entry.sandbox.manifest_digest,
                "network_disabled": entry.sandbox.network_disabled,
                "passed": entry.sandbox.passed,
                "runner_id": entry.sandbox.runner_id,
                "test_count": entry.sandbox.test_count,
            },
        },
    )


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()
