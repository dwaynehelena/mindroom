"""Tests for production provenance memory propagation handlers."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from mindroom.provenance_handlers import (
    HermesMemoryHandler,
    MarkdownMemoryHandler,
    ProvenanceHandlerError,
)
from mindroom.provenance_memory import PropagationAction


def _payload(memory_id: str = "memory-1") -> dict[str, object]:
    return {
        "schema": "mindroom.provenance-memory/1",
        "memory_id": memory_id,
        "owner_id": "@owner:example.com",
        "scope": "private",
        "content": "The user prefers concise reports.",
        "purpose": "assistant-memory",
        "created_at": "2026-07-18T00:00:00+00:00",
        "expires_at": None,
        "citations": [],
        "supersedes": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["mindroom", "openclaw"])
async def test_markdown_handler_upserts_reads_back_and_deletes(tmp_path: Path, target: str) -> None:
    """Native Markdown handlers should be atomic, idempotent and tombstoneable."""
    root = tmp_path / target
    root.mkdir(mode=0o700)
    handler = MarkdownMemoryHandler(target, root)
    upsert = PropagationAction("action-upsert", "memory-1", target, "upsert", _payload())  # type: ignore[arg-type]
    first = await handler(upsert, upsert.action_id)
    second = await handler(upsert, upsert.action_id)
    files = list(root.glob("*.md"))
    assert first == second
    assert len(files) == 1
    assert "concise reports" in files[0].read_text()
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600

    delete = PropagationAction("action-delete", "memory-1", target, "delete", None)  # type: ignore[arg-type]
    assert await handler(delete, delete.action_id) == await handler(delete, delete.action_id)
    assert not list(root.glob("*.md"))


@pytest.mark.asyncio
async def test_markdown_handler_rejects_identity_and_target_substitution(tmp_path: Path) -> None:
    """A handler must reject a payload or target that differs from its action."""
    root = tmp_path / "memory"
    root.mkdir(mode=0o700)
    handler = MarkdownMemoryHandler("openclaw", root)
    substituted = PropagationAction("action", "memory-1", "openclaw", "upsert", _payload("memory-2"))
    with pytest.raises(ProvenanceHandlerError, match="identity mismatch"):
        await handler(substituted, substituted.action_id)
    wrong_target = PropagationAction("action", "memory-1", "mindroom", "upsert", _payload())
    with pytest.raises(ProvenanceHandlerError, match="target"):
        await handler(wrong_target, wrong_target.action_id)


@pytest.mark.asyncio
async def test_hermes_handler_requires_exact_worker_receipt(tmp_path: Path) -> None:
    """Hermes handler should accept only a bounded response bound to its action key."""
    home = tmp_path / "hermes"
    home.mkdir(mode=0o700)
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json,sys\n"
        "request=json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'version':1,'success':True,'idempotency_key':request['idempotency_key'],"
        "'receipt':'hermes-native-receipt'}))\n",
    )
    handler = HermesMemoryHandler(("python3", str(worker)), home)
    action = PropagationAction("action", "memory-1", "hermes", "upsert", _payload())
    assert await handler(action, action.action_id) == "hermes-native-receipt"


@pytest.mark.asyncio
async def test_hermes_handler_rejects_unbound_receipt(tmp_path: Path) -> None:
    """A worker receipt for another action must fail closed."""
    home = tmp_path / "hermes"
    home.mkdir(mode=0o700)
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json\nprint(json.dumps({'version':1,'success':True,'idempotency_key':'other','receipt':'wrong'}))\n",
    )
    handler = HermesMemoryHandler(("python3", str(worker)), home)
    action = PropagationAction("action", "memory-1", "hermes", "upsert", _payload())
    with pytest.raises(ProvenanceHandlerError, match="exact action"):
        await handler(action, action.action_id)


@pytest.mark.asyncio
async def test_bundled_worker_mutates_hermes_native_store_in_isolated_profile(tmp_path: Path) -> None:
    """Bundled worker should use Hermes' unbounded native fact store."""
    installed = Path.home() / ".hermes" / "hermes-agent"
    python = installed / ".venv" / "bin" / "python"
    if not installed.is_dir() or not python.exists():
        pytest.skip("installed Hermes source environment is unavailable")
    home = tmp_path / "hermes"
    home.mkdir(mode=0o700)
    (home / "hermes-agent").symlink_to(installed, target_is_directory=True)
    worker = Path(__file__).parents[1] / "scripts" / "hermes_provenance_memory.py"
    handler = HermesMemoryHandler((str(python), str(worker)), home, timeout_seconds=30)
    payload = _payload("memory-native")
    payload["content"] = "A bounded native fact. " * 300
    upsert = PropagationAction("native-upsert", "memory-native", "hermes", "upsert", payload)
    receipt = await handler(upsert, upsert.action_id)
    database = home / "memory_store.db"
    assert receipt.startswith("hermes-native:sha256:")
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT content FROM facts WHERE category='mindroom_provenance'",
        ).fetchone()
    assert row is not None
    assert "mindroom-provenance:" in row[0]
    assert len(row[0]) > 2_200

    delete = PropagationAction("native-delete", "memory-native", "hermes", "delete", None)
    await handler(delete, delete.action_id)
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM facts WHERE category='mindroom_provenance'",
        ).fetchone()[0]
    assert count == 0
