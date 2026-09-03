"""Bounded NDJSON bridge into Hermes' durable holographic fact store."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

_REQUEST_KEYS = {"idempotency_key", "memory_id", "operation", "payload", "version"}


class _WorkerError(RuntimeError):
    """The bounded native-memory worker request or mutation is invalid."""


class _MemoryStore(Protocol):
    """Hermes holographic MemoryStore surface used by the worker."""

    def add_fact(self, content: str, category: str = "general", tags: str = "") -> int:
        """Add one durable fact."""

    def list_facts(self, category: str | None = None, min_trust: float = 0, limit: int = 50) -> list[dict[str, Any]]:
        """List durable facts."""

    def update_fact(self, fact_id: int, content: str | None = None, tags: str | None = None) -> bool:
        """Update one durable fact."""

    def remove_fact(self, fact_id: int) -> bool:
        """Remove one durable fact."""

    def close(self) -> None:
        """Close the native store."""


def _response(key: str, *, success: bool, receipt: str = "") -> None:
    print(
        json.dumps(
            {"idempotency_key": key, "receipt": receipt, "success": success, "version": 1},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )


def _entry(memory_id: str, payload: dict[str, Any]) -> tuple[str, str]:
    marker = f"[mindroom-provenance:{hashlib.sha256(memory_id.encode()).hexdigest()}]"
    if payload.get("mode") == "reference":
        # Tier-2 externalized record: the full content lives in the overflow
        # store, and Hermes holds a compact reference pointer that carries a
        # content preview and digest rather than the full content.
        reference = {
            key: payload.get(key)
            for key in (
                "content_digest",
                "content_length",
                "content_preview",
                "created_at",
                "memory_id",
                "mode",
                "provenance_store_path",
                "schema",
            )
        }
        serialized = json.dumps(reference, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
        return marker, f"{marker} {serialized}"
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError
    provenance = {
        "citations": payload.get("citations"),
        "expires_at": payload.get("expires_at"),
        "owner_id": payload.get("owner_id"),
        "purpose": payload.get("purpose"),
        "scope": payload.get("scope"),
        "supersedes": payload.get("supersedes"),
    }
    metadata = json.dumps(provenance, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return marker, f"{marker} {content.strip()}\nProvenance: {metadata}"


def _read_request() -> dict[str, Any]:
    try:
        request = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _WorkerError from exc
    if not isinstance(request, dict) or set(request) != _REQUEST_KEYS or request.get("version") != 1:
        raise _WorkerError
    key = request.get("idempotency_key")
    memory_id = request.get("memory_id")
    if not isinstance(key, str) or not key or not isinstance(memory_id, str) or not memory_id:
        raise _WorkerError
    return request


def _apply_action(store: _MemoryStore, request: dict[str, Any]) -> None:
    memory_id = request["memory_id"]
    operation = request["operation"]
    marker = f"[mindroom-provenance:{hashlib.sha256(memory_id.encode()).hexdigest()}]"
    matches = [
        fact for fact in store.list_facts(category="mindroom_provenance", limit=100_000) if marker in fact["content"]
    ]
    if len(matches) > 1:
        raise _WorkerError
    if operation == "delete":
        if request.get("payload") is not None:
            raise _WorkerError
        success = not matches or store.remove_fact(int(matches[0]["fact_id"]))
    elif operation == "upsert":
        payload = request.get("payload")
        if not isinstance(payload, dict) or payload.get("memory_id") != memory_id:
            raise _WorkerError
        marker, entry = _entry(memory_id, payload)
        if matches and matches[0]["content"] == entry:
            success = True
        elif matches:
            success = store.update_fact(int(matches[0]["fact_id"]), content=entry, tags=marker)
        else:
            success = store.add_fact(entry, category="mindroom_provenance", tags=marker) > 0
    else:
        raise _WorkerError
    if not success:
        raise _WorkerError


def _mutate(request: dict[str, Any]) -> None:
    hermes_home = Path(os.environ.get("HERMES_HOME", "")).resolve()
    source_root = hermes_home / "hermes-agent"
    if not source_root.is_dir():
        raise _WorkerError
    sys.path.insert(0, str(source_root))
    from plugins.memory.holographic.store import MemoryStore  # noqa: PLC0415  # ty: ignore[unresolved-import]
    from tools.memory_tool import _scan_memory_content  # noqa: PLC0415  # ty: ignore[unresolved-import]

    if request["operation"] == "upsert":
        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise _WorkerError
        _marker, candidate = _entry(request["memory_id"], payload)
        if _scan_memory_content(candidate):
            raise _WorkerError
    store = MemoryStore(db_path=hermes_home / "memory_store.db")
    try:
        _apply_action(store, request)
    finally:
        store.close()


def _run() -> int:
    try:
        request = _read_request()
    except _WorkerError:
        _response("", success=False)
        return 2
    key = request["idempotency_key"]
    memory_id = request["memory_id"]
    operation = request["operation"]

    try:
        _mutate(request)
    except Exception:
        _response(key, success=False)
        return 1

    digest = hashlib.sha256(
        json.dumps(
            {"idempotency_key": key, "memory_id": memory_id, "operation": operation},
            separators=(",", ":"),
            sort_keys=True,
        ).encode(),
    ).hexdigest()
    _response(key, success=True, receipt=f"hermes-native:sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())
