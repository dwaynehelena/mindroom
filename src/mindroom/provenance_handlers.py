"""Production propagation handlers for native MindRoom/OpenClaw/Hermes memory seams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mindroom.provenance_memory import PropagationAction, ProvenanceMemoryError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_MAX_WORKER_RESPONSE_BYTES = 64 * 1024
_PORTABLE_SCHEMA = "mindroom.provenance-memory/1"


class ProvenanceHandlerError(ProvenanceMemoryError):
    """A native runtime memory handler rejected or could not prove a mutation."""


@dataclass(frozen=True, slots=True)
class MarkdownMemoryHandler:
    """Atomically manage one Markdown document per portable memory record."""

    target: str
    root: Path

    def __post_init__(self) -> None:
        """Require an existing private, non-symlink native memory directory."""
        if self.target not in {"mindroom", "openclaw"}:
            message = "Markdown provenance handler supports MindRoom or OpenClaw"
            raise ValueError(message)
        if not self.root.is_absolute() or self.root.is_symlink() or not self.root.is_dir():
            message = "provenance memory root must be an existing absolute non-symlink directory"
            raise ValueError(message)
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            message = "provenance memory root must be current-user-owned and not group/world writable"
            raise ValueError(message)

    async def __call__(self, action: PropagationAction, idempotency_key: str) -> str:
        """Apply one exact upsert/delete and return a read-back-bound receipt."""
        _validate_action(action, expected_target=self.target, idempotency_key=idempotency_key)
        path = self.root / f"mindroom-provenance-{hashlib.sha256(action.memory_id.encode()).hexdigest()}.md"
        if action.operation == "delete":
            await asyncio.to_thread(_delete_file, path, self.root)
            return _receipt(self.target, action, None)
        assert action.payload is not None
        document = _markdown_document(action.payload)
        await asyncio.to_thread(_atomic_write_exact, path, document, self.root)
        read_back = await asyncio.to_thread(path.read_bytes)
        if read_back != document:
            message = "native memory read-back mismatch"
            raise ProvenanceHandlerError(message)
        return _receipt(self.target, action, hashlib.sha256(read_back).hexdigest())


@dataclass(frozen=True, slots=True)
class HermesMemoryHandler:
    """Invoke a bounded Hermes-native memory worker over strict NDJSON."""

    argv: tuple[str, ...]
    hermes_home: Path
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        """Validate the explicit worker command and profile root."""
        if not self.argv or any(not isinstance(value, str) or not value or "\x00" in value for value in self.argv):
            message = "Hermes memory worker argv is invalid"
            raise ValueError(message)
        if not self.hermes_home.is_absolute() or self.hermes_home.is_symlink() or not self.hermes_home.is_dir():
            message = "Hermes home must be an existing absolute non-symlink directory"
            raise ValueError(message)
        if self.timeout_seconds <= 0 or self.timeout_seconds > 120:
            message = "Hermes memory worker timeout must be between zero and 120 seconds"
            raise ValueError(message)

    async def __call__(self, action: PropagationAction, idempotency_key: str) -> str:
        """Apply one action through Hermes' guarded MemoryStore implementation."""
        _validate_action(action, expected_target="hermes", idempotency_key=idempotency_key)
        request = {
            "idempotency_key": idempotency_key,
            "memory_id": action.memory_id,
            "operation": action.operation,
            "payload": action.payload,
            "version": 1,
        }
        process = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={
                "HOME": str(self.hermes_home.parent),
                "HERMES_HOME": str(self.hermes_home),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            },
        )
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        try:
            async with asyncio.timeout(self.timeout_seconds):
                stdout, _ = await process.communicate(payload)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            message = "Hermes memory worker timed out"
            raise ProvenanceHandlerError(message) from exc
        if process.returncode != 0 or len(stdout) > _MAX_WORKER_RESPONSE_BYTES or not stdout.endswith(b"\n"):
            message = "Hermes memory worker failed its bounded response contract"
            raise ProvenanceHandlerError(message)
        try:
            response = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            message = "Hermes memory worker returned invalid JSON"
            raise ProvenanceHandlerError(message) from exc
        expected = {"idempotency_key", "receipt", "success", "version"}
        if not isinstance(response, dict) or set(response) != expected or response.get("version") != 1:
            message = "Hermes memory worker returned an invalid exact response shape"
            raise ProvenanceHandlerError(message)
        if response.get("idempotency_key") != idempotency_key or response.get("success") is not True:
            message = "Hermes memory worker did not confirm the exact action"
            raise ProvenanceHandlerError(message)
        receipt = response.get("receipt")
        if not isinstance(receipt, str) or not receipt:
            message = "Hermes memory worker returned no receipt"
            raise ProvenanceHandlerError(message)
        return receipt


def _validate_action(action: PropagationAction, *, expected_target: str, idempotency_key: str) -> None:
    if action.target != expected_target or idempotency_key != action.action_id:
        message = "propagation target or idempotency key mismatch"
        raise ProvenanceHandlerError(message)
    if action.operation == "upsert":
        payload = action.payload
        if not isinstance(payload, dict) or payload.get("schema") != _PORTABLE_SCHEMA:
            message = "portable memory payload schema is invalid"
            raise ProvenanceHandlerError(message)
        if payload.get("memory_id") != action.memory_id:
            message = "portable memory payload identity mismatch"
            raise ProvenanceHandlerError(message)
    elif action.payload is not None:
        message = "portable memory deletion must not contain payload content"
        raise ProvenanceHandlerError(message)


def _markdown_document(payload: Mapping[str, Any]) -> bytes:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        message = "portable memory content is required"
        raise ProvenanceHandlerError(message)
    metadata = {key: value for key, value in payload.items() if key != "content"}
    encoded = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return f"<!-- mindroom-provenance {encoded} -->\n\n{content.strip()}\n".encode()


def _atomic_write_exact(path: Path, content: bytes, root: Path) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    temporary = root / f".{path.name}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        _fsync_directory(root)
    finally:
        if temporary.exists():
            temporary.unlink()


def _delete_file(path: Path, root: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()
    _fsync_directory(root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _receipt(target: str, action: PropagationAction, state_digest: str | None) -> str:
    value = {
        "action_id": action.action_id,
        "memory_id": action.memory_id,
        "operation": action.operation,
        "state_digest": state_digest,
        "target": target,
    }
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return f"{target}:sha256:{hashlib.sha256(encoded).hexdigest()}"
