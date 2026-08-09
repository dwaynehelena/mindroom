"""Production propagation handlers for native MindRoom/OpenClaw/Hermes memory seams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mindroom.provenance_memory import PropagationAction, ProvenanceMemoryError
from mindroom.provenance_overflow import ProvenanceOverflowError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from mindroom.provenance_overflow import ProvenanceOverflowStore

_MAX_WORKER_RESPONSE_BYTES = 64 * 1024
_PORTABLE_SCHEMA = "mindroom.provenance-memory/1"

# Hermes native MemoryStore capacity ceiling.
#
# The holographic SQLite store backs the ``facts`` table with ``content TEXT``,
# which has no inherent character limit. The historical ~2,200-char figure came
# from the *memory_tool* file store (MEMORY.md / USER.md), NOT from the
# holographic fact store that the provenance worker writes to. Raising this cap
# lets ordinary citation-bearing records write directly into the holographic
# store and reserves the external ProvenanceOverflowStore (reference mode) for
# genuinely huge records only.
_HERMES_CONTENT_LIMIT_CHARS = 50_000


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
    """Invoke a bounded Hermes-native memory worker over strict NDJSON.

    When the payload content exceeds the Hermes native store ceiling (now
    50,000 chars; the holographic SQLite store has no inherent TEXT limit),
    the handler transparently stores the full content in a
    ProvenanceOverflowStore (Tier 2) and sends a compact reference pointer to
    the Hermes worker (Tier 1).

    Attributes
    ----------
    argv: Worker command as a tuple of argument strings.
    hermes_home: Absolute path to the Hermes profile root.
    overflow_store: Optional ProvenanceOverflowStore for large records.
    content_threshold_chars: Content length that triggers reference mode.
        Defaults to 40,000, providing headroom below the 50,000 ceiling.
    timeout_seconds: Worker subprocess timeout (0–120s, default 15s).
    """

    argv: tuple[str, ...]
    hermes_home: Path
    overflow_store: ProvenanceOverflowStore | None = None
    content_threshold_chars: int = 40_000
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
        if self.content_threshold_chars < 100 or self.content_threshold_chars > _HERMES_CONTENT_LIMIT_CHARS:
            message = f"content threshold must be between 100 and {_HERMES_CONTENT_LIMIT_CHARS} characters"
            raise ValueError(message)

    async def __call__(self, action: PropagationAction, idempotency_key: str) -> str:
        """Apply one action through Hermes' guarded MemoryStore implementation.

        For upserts with content exceeding the threshold, the full payload is
        stored in the overflow store (Tier 2) and a compact reference pointer
        is sent to the Hermes worker (Tier 1). The read path detects reference
        pointers and fetches full content from the overflow store.
        """
        _validate_action(action, expected_target="hermes", idempotency_key=idempotency_key)

        if action.operation == "upsert" and action.payload is not None:
            payload = action.payload
            content = payload.get("content", "")
            if isinstance(content, str) and len(content) > self.content_threshold_chars:
                return await self._write_reference(action, idempotency_key)

        receipt = await self._write_direct(action, idempotency_key)

        # Deletes must purge the Tier-2 overflow record alongside the Tier-1
        # reference pointer so no orphaned full content is left behind when a
        # previously externalized record is tombstoned.
        if action.operation == "delete" and self.overflow_store is not None:
            with suppress(ProvenanceOverflowError):
                await self.overflow_store.delete(action.memory_id)

        return receipt

    async def _write_reference(self, action: PropagationAction, idempotency_key: str) -> str:
        """Store full content in overflow store and write a reference pointer to Hermes."""
        assert action.payload is not None

        if self.overflow_store is None:
            message = "overflow store is required for content exceeding the Hermes limit"
            raise ProvenanceHandlerError(message)

        # Store full content in Tier 2 (overflow store)
        content_hash = await self.overflow_store.store(action.memory_id, action.payload)

        # Build compact reference pointer for Tier 1 (Hermes native store)
        content = action.payload.get("content", "")
        preview = content[:500]
        reference_payload = {
            "schema": _PORTABLE_SCHEMA,
            "memory_id": action.memory_id,
            "mode": "reference",
            "content_preview": preview,
            "content_digest": f"sha256:{content_hash}",
            "content_length": len(content),
            "provenance_store_path": self.overflow_store.path,
            "created_at": datetime.now(UTC).isoformat(),
        }

        reference_action = PropagationAction(
            action_id=action.action_id,
            memory_id=action.memory_id,
            target=action.target,
            operation=action.operation,
            payload=reference_payload,
        )

        return await self._write_direct(reference_action, idempotency_key)

    async def _write_direct(self, action: PropagationAction, idempotency_key: str) -> str:
        """Write a payload directly to the Hermes worker (existing behavior)."""
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

    @staticmethod
    def is_reference_pointer(content: str) -> bool:
        """Detect whether Hermes content is a reference pointer (Tier 1 → Tier 2).

        Reference pointers are JSON payloads with ``"mode": "reference"``.
        """
        if not content.startswith("{"):
            return False
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        return isinstance(data, dict) and data.get("mode") == "reference"

    @staticmethod
    def parse_reference_pointer(content: str) -> dict[str, Any] | None:
        """Parse a reference pointer to extract the memory_id and content_digest.

        Returns the parsed dict, or None if the content is not a valid reference.
        """
        if not HermesMemoryHandler.is_reference_pointer(content):
            return None
        return json.loads(content)


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
        if payload.get("mode") == "reference":
            _validate_reference_payload(payload)
    elif action.payload is not None:
        message = "portable memory deletion must not contain payload content"
        raise ProvenanceHandlerError(message)


def _validate_reference_payload(payload: dict[str, object]) -> None:
    """Validate that a reference-mode payload has the required fields."""
    if not isinstance(payload.get("content_digest"), str) or not payload["content_digest"]:
        message = "reference payload must include a non-empty content_digest"
        raise ProvenanceHandlerError(message)
    if not isinstance(payload.get("content_preview"), str):
        message = "reference payload must include a content_preview string"
        raise ProvenanceHandlerError(message)
    if not isinstance(payload.get("content_length"), int) or payload["content_length"] <= 0:
        message = "reference payload must include a positive content_length"
        raise ProvenanceHandlerError(message)
    if not isinstance(payload.get("provenance_store_path"), str) or not payload["provenance_store_path"]:
        message = "reference payload must include a non-empty provenance_store_path"
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
