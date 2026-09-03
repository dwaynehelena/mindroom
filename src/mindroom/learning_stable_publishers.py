"""Active, receipt-bound OpenClaw and Hermes publishers for governed learning."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from mindroom.learning_loop import LearningLoopError
from mindroom.provenance_memory import PropagationAction, PropagationHandler, RuntimeTarget
from mindroom.skill_registry import PortableSkillManifest, RegistryEntry, SandboxEvidence, ScanFinding

if TYPE_CHECKING:
    from pathlib import Path

    from mindroom.learning_loop import LearningProposal
    from mindroom.skill_registry import SkillTrustRegistry

Runtime = Literal["openclaw", "hermes"]


@dataclass(frozen=True, slots=True)
class LearningStablePublisher:
    """Activate exact governed skills or memories through native runtime seams."""

    runtime: Runtime
    skill_root: Path
    memory_handler: PropagationHandler
    skill_registry: SkillTrustRegistry

    def __post_init__(self) -> None:
        """Validate the active skill adapter eagerly."""
        expanded = self.skill_root.expanduser()
        if not expanded.is_absolute() or not expanded.is_dir() or expanded.is_symlink():
            message = "learning stable skill root must be an existing absolute non-symlink directory"
            raise ValueError(message)
        object.__setattr__(self, "skill_root", expanded.resolve(strict=True))

    async def publish(self, proposal: LearningProposal) -> str:
        """Install one exact approved proposal and return its native receipt envelope."""
        _validate_governance(proposal)
        if proposal.kind == "skill":
            entry = _skill_entry(proposal, self.skill_registry)
            artifact = self.skill_root / entry.manifest.skill_id / "SKILL.md"
            payload = _active_skill_payload(proposal, entry, self.runtime)
            _write_active_skill(artifact, payload, self.skill_root)
            native_receipt = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        else:
            action = _memory_action(proposal, self.runtime, "upsert")
            native_receipt = await self.memory_handler(action, action.action_id)
        return _receipt(proposal, self.runtime, native_receipt)

    async def rollback(self, proposal: LearningProposal, receipt: str) -> None:
        """Remove only the active artifact bound to the persisted stable receipt."""
        native_receipt = _validate_receipt(proposal, self.runtime, receipt)
        if proposal.kind == "skill":
            entry = _skill_entry(proposal, self.skill_registry)
            artifact = self.skill_root / entry.manifest.skill_id / "SKILL.md"
            _remove_active_skill(artifact, native_receipt, self.skill_root)
        else:
            action = _memory_action(proposal, self.runtime, "delete")
            await self.memory_handler(action, action.action_id)


def _validate_governance(proposal: LearningProposal) -> None:
    artifact_json = json.dumps(
        proposal.artifact,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    if (
        proposal.stage not in {"approved", "canary"}
        or proposal.evaluation is None
        or not proposal.evaluation.passed
        or proposal.evaluation.artifact_digest != proposal.artifact_digest
        or hashlib.sha256(artifact_json.encode()).hexdigest() != proposal.artifact_digest
        or not proposal.reviewed_by
        or not proposal.review_reason
    ):
        message = "stable learning publication requires exact evaluation and attributable approval"
        raise LearningLoopError(message)


def _skill_entry(proposal: LearningProposal, registry: SkillTrustRegistry) -> RegistryEntry:
    raw = proposal.artifact
    if set(raw) != {"findings", "manifest", "sandbox", "signature"}:
        message = "stable skill proposal has an invalid evidence shape"
        raise LearningLoopError(message)
    try:
        manifest_raw = cast("dict[str, object]", raw["manifest"])
        sandbox_raw = cast("dict[str, object]", raw["sandbox"])
        findings_raw = cast("list[dict[str, object]]", raw["findings"])
        signature = raw["signature"]
        entry = RegistryEntry(
            manifest=PortableSkillManifest.model_validate(manifest_raw),
            stage="signed",
            findings=tuple(_scan_finding(finding) for finding in findings_raw),
            sandbox=_sandbox_evidence(sandbox_raw),
            signature=signature if isinstance(signature, str) else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        message = "stable skill proposal contains invalid signed evidence"
        raise LearningLoopError(message) from exc
    if not registry.verify(entry):
        message = "stable skill proposal signature verification failed"
        raise LearningLoopError(message)
    return entry


def _active_skill_payload(proposal: LearningProposal, entry: RegistryEntry, runtime: Runtime) -> bytes:
    manifest = entry.manifest
    description = f"Governed MindRoom learning skill {manifest.skill_id} ({manifest.version}) for {runtime}."
    return (
        "---\n"
        f"name: {json.dumps(manifest.skill_id)}\n"
        f"description: {json.dumps(description)}\n"
        f"version: {json.dumps(manifest.version)}\n"
        "metadata:\n"
        "  mindroom_learning:\n"
        f"    artifact_sha256: {json.dumps(proposal.artifact_digest)}\n"
        f"    proposal_id: {json.dumps(proposal.proposal_id)}\n"
        f"    signature: {json.dumps(entry.signature)}\n"
        "---\n\n"
        f"# {manifest.skill_id}\n\n"
        "Use the following sandbox-tested entrypoint for this skill:\n\n"
        f"```text\n{manifest.entrypoint}\n```\n"
    ).encode()


def _write_active_skill(artifact: Path, payload: bytes, root: Path) -> None:
    directory = artifact.parent
    with suppress(FileExistsError):
        directory.mkdir(mode=0o700)
    mode = os.lstat(directory).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode) or directory.parent != root:
        message = "learning stable skill path may contain only its real root and skill directory"
        raise LearningLoopError(message)
    if artifact.exists():
        if artifact.is_symlink() or artifact.read_bytes() != payload:
            message = "governed learning active skill identity equivocation denied"
            raise LearningLoopError(message)
        return
    temporary = artifact.with_name(f".{artifact.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(artifact)
        _fsync_directory(directory)
    finally:
        temporary.unlink(missing_ok=True)
    if artifact.is_symlink() or artifact.read_bytes() != payload:
        message = "governed learning active skill readback verification failed"
        raise LearningLoopError(message)


def _remove_active_skill(artifact: Path, receipt: str, root: Path) -> None:
    if not artifact.exists():
        return
    if artifact.parent.parent != root or artifact.is_symlink() or not receipt.startswith("sha256:"):
        message = "governed learning active skill rollback receipt is invalid"
        raise LearningLoopError(message)
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != receipt.removeprefix("sha256:"):
        message = "governed learning active skill rollback receipt does not match the artifact"
        raise LearningLoopError(message)
    artifact.unlink()
    _fsync_directory(artifact.parent)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _scan_finding(raw: dict[str, object]) -> ScanFinding:
    category = raw.get("category")
    detail = raw.get("detail")
    allowed = {"permission", "secret", "network", "dependency", "entrypoint"}
    if not isinstance(category, str) or category not in allowed or not isinstance(detail, str):
        raise TypeError
    return ScanFinding(cast("Literal['permission', 'secret', 'network', 'dependency', 'entrypoint']", category), detail)


def _sandbox_evidence(raw: dict[str, object]) -> SandboxEvidence:
    runner_id = raw.get("runner_id")
    manifest_value = raw.get("manifest_digest")
    isolated = raw.get("isolated")
    network_disabled = raw.get("network_disabled")
    passed = raw.get("passed")
    test_count = raw.get("test_count")
    if (
        not isinstance(runner_id, str)
        or not isinstance(manifest_value, str)
        or not isinstance(isolated, bool)
        or not isinstance(network_disabled, bool)
        or not isinstance(passed, bool)
        or not isinstance(test_count, int)
    ):
        raise TypeError
    return SandboxEvidence(runner_id, manifest_value, isolated, network_disabled, passed, test_count)


def _memory_action(
    proposal: LearningProposal,
    runtime: Runtime,
    operation: Literal["upsert", "delete"],
) -> PropagationAction:
    raw = proposal.artifact
    required = {
        "citations",
        "consent",
        "content",
        "created_at",
        "expires_at",
        "memory_id",
        "owner_id",
        "purpose",
        "scope",
        "supersedes",
    }
    if set(raw) != required:
        message = "stable memory proposal has an invalid evidence shape"
        raise LearningLoopError(message)
    _validate_memory_consent(raw)
    stable_memory_id = f"learning:{hashlib.sha256(proposal.proposal_id.encode()).hexdigest()}"
    payload = None
    if operation == "upsert":
        payload = {
            "schema": "mindroom.provenance-memory/1",
            "memory_id": stable_memory_id,
            "owner_id": raw["owner_id"],
            "scope": raw["scope"],
            "content": raw["content"],
            "purpose": raw["purpose"],
            "created_at": raw["created_at"],
            "expires_at": raw["expires_at"],
            "citations": raw["citations"],
            "supersedes": raw["supersedes"],
        }
    action_id = hashlib.sha256(
        f"{proposal.proposal_id}\x1f{runtime}\x1fstable\x1f{operation}".encode(),
    ).hexdigest()
    return PropagationAction(
        action_id,
        stable_memory_id,
        cast("RuntimeTarget", runtime),
        operation,
        payload,
    )


def _validate_memory_consent(raw: dict[str, object]) -> None:
    consent_value = raw.get("consent")
    citations = raw.get("citations")
    if not isinstance(consent_value, dict) or not isinstance(citations, list) or not citations:
        message = "stable memory proposal requires consent and citations"
        raise LearningLoopError(message)
    consent = cast("dict[str, object]", consent_value)
    try:
        now = datetime.now(UTC)
        granted = _utc_timestamp(consent["granted_at"])
        consent_expiry_raw = consent.get("expires_at")
        consent_expiry = _utc_timestamp(consent_expiry_raw) if consent_expiry_raw else None
        memory_expiry_raw = raw.get("expires_at")
        memory_expiry = _utc_timestamp(memory_expiry_raw) if memory_expiry_raw else None
    except (KeyError, TypeError, ValueError) as exc:
        message = "stable memory proposal contains invalid consent timestamps"
        raise LearningLoopError(message) from exc
    if (
        consent.get("actor_id") != raw.get("owner_id")
        or consent.get("purpose") != raw.get("purpose")
        or granted > now
        or (consent_expiry is not None and now > consent_expiry)
        or (memory_expiry is not None and now > memory_expiry)
    ):
        message = "stable memory proposal consent is not currently valid"
        raise LearningLoopError(message)


def _utc_timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(UTC)


def _receipt(proposal: LearningProposal, runtime: Runtime, native_receipt: str) -> str:
    return json.dumps(
        {
            "artifact_digest": proposal.artifact_digest,
            "native_receipt": native_receipt,
            "proposal_id": proposal.proposal_id,
            "runtime": runtime,
            "version": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_receipt(proposal: LearningProposal, runtime: Runtime, receipt: str) -> str:
    try:
        value = json.loads(receipt)
    except json.JSONDecodeError as exc:
        message = "stable learning rollback receipt is invalid"
        raise LearningLoopError(message) from exc
    expected = {
        "artifact_digest": proposal.artifact_digest,
        "proposal_id": proposal.proposal_id,
        "runtime": runtime,
        "version": 1,
    }
    if not isinstance(value, dict) or {key: value.get(key) for key in expected} != expected:
        message = "stable learning rollback receipt does not match the proposal"
        raise LearningLoopError(message)
    native_receipt = value.get("native_receipt")
    if set(value) != {*expected, "native_receipt"} or not isinstance(native_receipt, str) or not native_receipt:
        message = "stable learning rollback receipt has an invalid native receipt"
        raise LearningLoopError(message)
    return native_receipt
