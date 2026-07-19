"""Hardened filesystem publishers for OpenClaw and Hermes skill roots."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import suppress
from typing import TYPE_CHECKING

from mindroom.skill_publication import SkillPublicationError

if TYPE_CHECKING:
    from pathlib import Path

    from mindroom.skill_registry import RegistryEntry

_CANARY_DIRECTORY = ".mindroom-canary"
_STABLE_FILENAME = "SKILL.md"


class FilesystemCanaryPublisher:
    """Publish signed metadata to a non-executable runtime canary namespace."""

    def __init__(self, root: Path, runtime: str) -> None:
        if runtime not in {"openclaw", "hermes"}:
            message = "skill canary runtime must be openclaw or hermes"
            raise ValueError(message)
        resolved = root.expanduser()
        if not resolved.is_absolute() or not resolved.is_dir() or resolved.is_symlink():
            message = "skill canary root must be an existing absolute non-symlink directory"
            raise ValueError(message)
        self._root = resolved.resolve(strict=True)
        self._runtime = runtime

    async def publish(self, entry: RegistryEntry, publication_key: str) -> str:
        """Atomically write and read back one exact signed canary artifact."""
        artifact = self._artifact(entry, publication_key)
        payload = _artifact_payload(entry, publication_key, self._runtime)
        digest = hashlib.sha256(payload).hexdigest()
        _ensure_private_directories(self._root, (_CANARY_DIRECTORY, entry.manifest.skill_id))
        if artifact.exists():
            if artifact.is_symlink() or artifact.read_bytes() != payload:
                message = "skill canary identity equivocation denied"
                raise SkillPublicationError(message)
        else:
            temporary = artifact.with_name(f".{artifact.name}.{os.getpid()}.tmp")
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(artifact)
                _fsync_directory(artifact.parent)
            finally:
                temporary.unlink(missing_ok=True)
        if artifact.is_symlink() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
            message = "skill canary readback verification failed"
            raise SkillPublicationError(message)
        return f"sha256:{digest}"

    async def rollback(self, entry: RegistryEntry, receipt: str) -> None:
        """Delete only the exact canary artifact bound to the supplied digest receipt."""
        artifact = self._artifact_from_entry(entry)
        if not artifact.exists():
            return
        if artifact.is_symlink() or not receipt.startswith("sha256:"):
            message = "skill canary rollback receipt is invalid"
            raise SkillPublicationError(message)
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != receipt.removeprefix("sha256:"):
            message = "skill canary rollback receipt does not match the artifact"
            raise SkillPublicationError(message)
        artifact.unlink()
        _fsync_directory(artifact.parent)

    def _artifact(self, entry: RegistryEntry, publication_key: str) -> Path:
        expected_prefix = f"{entry.manifest.skill_id}:{entry.manifest.version}:{self._runtime}:"
        if not publication_key.startswith(expected_prefix) or entry.signature is None:
            message = "skill canary publication key does not match the signed entry and runtime"
            raise SkillPublicationError(message)
        if publication_key != f"{expected_prefix}{entry.signature}":
            message = "skill canary publication key does not match the signed entry and runtime"
            raise SkillPublicationError(message)
        return self._artifact_from_entry(entry)

    def _artifact_from_entry(self, entry: RegistryEntry) -> Path:
        return self._root / _CANARY_DIRECTORY / entry.manifest.skill_id / f"{entry.manifest.version}.json"


class FilesystemStablePublisher:
    """Install one signed manifest as an active, receipt-bound runtime skill."""

    def __init__(self, root: Path, runtime: str) -> None:
        if runtime not in {"openclaw", "hermes"}:
            message = "stable skill runtime must be openclaw or hermes"
            raise ValueError(message)
        resolved = root.expanduser()
        if not resolved.is_absolute() or not resolved.is_dir() or resolved.is_symlink():
            message = "stable skill root must be an existing absolute non-symlink directory"
            raise ValueError(message)
        self._root = resolved.resolve(strict=True)
        self._runtime = runtime

    async def publish(self, entry: RegistryEntry, publication_key: str) -> str:
        """Atomically activate and read back one exact signed skill definition."""
        _validate_publication_key(entry, publication_key, self._runtime)
        payload = _stable_payload(entry, publication_key, self._runtime)
        digest = hashlib.sha256(payload).hexdigest()
        _ensure_private_directories(self._root, (entry.manifest.skill_id,))
        artifact = self._artifact(entry)
        if artifact.exists():
            if artifact.is_symlink() or artifact.read_bytes() != payload:
                message = "stable skill identity equivocation denied"
                raise SkillPublicationError(message)
        else:
            temporary = artifact.with_name(f".{artifact.name}.{os.getpid()}.tmp")
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(artifact)
                _fsync_directory(artifact.parent)
            finally:
                temporary.unlink(missing_ok=True)
        if artifact.is_symlink() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
            message = "stable skill readback verification failed"
            raise SkillPublicationError(message)
        return f"sha256:{digest}"

    async def rollback(self, entry: RegistryEntry, receipt: str) -> None:
        """Remove only the active skill file matching the stable receipt."""
        artifact = self._artifact(entry)
        if not artifact.exists():
            return
        if artifact.is_symlink() or not receipt.startswith("sha256:"):
            message = "stable skill rollback receipt is invalid"
            raise SkillPublicationError(message)
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != receipt.removeprefix("sha256:"):
            message = "stable skill rollback receipt does not match the artifact"
            raise SkillPublicationError(message)
        artifact.unlink()
        _fsync_directory(artifact.parent)

    def _artifact(self, entry: RegistryEntry) -> Path:
        return self._root / entry.manifest.skill_id / _STABLE_FILENAME


def _artifact_payload(entry: RegistryEntry, publication_key: str, runtime: str) -> bytes:
    if entry.signature is None or entry.sandbox is None or entry.stage != "signed":
        message = "skill canary publication requires signed sandbox evidence"
        raise SkillPublicationError(message)
    return json.dumps(
        {
            "manifest": entry.manifest.model_dump(mode="json"),
            "publication_key": publication_key,
            "runtime": runtime,
            "sandbox": {
                "isolated": entry.sandbox.isolated,
                "manifest_digest": entry.sandbox.manifest_digest,
                "network_disabled": entry.sandbox.network_disabled,
                "passed": entry.sandbox.passed,
                "runner_id": entry.sandbox.runner_id,
                "test_count": entry.sandbox.test_count,
            },
            "signature": entry.signature,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode()


def _stable_payload(entry: RegistryEntry, publication_key: str, runtime: str) -> bytes:
    if entry.signature is None or entry.sandbox is None or entry.stage != "signed":
        message = "stable skill publication requires signed sandbox evidence"
        raise SkillPublicationError(message)
    manifest = entry.manifest
    description = f"Signed MindRoom skill {manifest.skill_id} ({manifest.version}) for {runtime}."
    provenance = hashlib.sha256(_artifact_payload(entry, publication_key, runtime)).hexdigest()
    return (
        "---\n"
        f"name: {json.dumps(manifest.skill_id)}\n"
        f"description: {json.dumps(description)}\n"
        f"version: {json.dumps(manifest.version)}\n"
        "metadata:\n"
        "  mindroom:\n"
        f"    provenance_sha256: {json.dumps(provenance)}\n"
        f"    signature: {json.dumps(entry.signature)}\n"
        "---\n\n"
        f"# {manifest.skill_id}\n\n"
        "Use the following sandbox-tested entrypoint for this skill:\n\n"
        f"```text\n{manifest.entrypoint}\n```\n"
    ).encode()


def _validate_publication_key(entry: RegistryEntry, publication_key: str, runtime: str) -> None:
    expected = f"{entry.manifest.skill_id}:{entry.manifest.version}:{runtime}:{entry.signature}"
    if entry.signature is None or publication_key != expected:
        message = "stable skill publication key does not match the signed entry and runtime"
        raise SkillPublicationError(message)


def _ensure_private_directories(root: Path, components: tuple[str, ...]) -> None:
    current = root
    for component in components:
        current = current / component
        with suppress(FileExistsError):
            current.mkdir(mode=0o700)
        mode = os.lstat(current).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            message = "skill canary path may contain only real directories"
            raise SkillPublicationError(message)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
