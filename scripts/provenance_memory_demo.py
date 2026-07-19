"""Run a reversible three-runtime provenance-memory propagation demonstration."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mindroom.provenance_handlers import HermesMemoryHandler, MarkdownMemoryHandler
from mindroom.provenance_memory import (
    ConsentGrant,
    MemoryCitation,
    MemoryPropagator,
    PropagationAction,
    PropagationHandler,
    ProvenanceMemoryStore,
    RuntimeTarget,
)


class _DemoError(RuntimeError):
    """The reversible demonstration failed an evidence or cleanup invariant."""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mindroom-root",
        type=Path,
        default=Path.home() / "mindroom/mindroom_data/agents/mind/workspace/memory",
    )
    parser.add_argument("--openclaw-root", type=Path, default=Path.home() / ".openclaw/memory")
    parser.add_argument("--hermes-home", type=Path, default=Path.home() / ".hermes")
    parser.add_argument(
        "--hermes-python",
        type=Path,
        default=Path.home() / ".hermes/hermes-agent/.venv/bin/python",
    )
    return parser.parse_args()


def _markdown_path(root: Path, memory_id: str) -> Path:
    digest = hashlib.sha256(memory_id.encode()).hexdigest()
    return root / f"mindroom-provenance-{digest}.md"


def _hermes_fact_count(database: Path, marker: str) -> int:
    if not database.is_file():
        return 0
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM facts WHERE category=? AND instr(content, ?) > 0",
            ("mindroom_provenance", marker),
        ).fetchone()
    return int(row[0]) if row else 0


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    memory_id = f"portfolio-demo-{secrets.token_hex(12)}"
    marker = f"[mindroom-provenance:{hashlib.sha256(memory_id.encode()).hexdigest()}]"
    worker = Path(__file__).with_name("hermes_provenance_memory.py").resolve()
    hermes_database = arguments.hermes_home / "memory_store.db"
    handlers: dict[RuntimeTarget, PropagationHandler] = {
        "mindroom": MarkdownMemoryHandler("mindroom", arguments.mindroom_root.resolve()),
        "openclaw": MarkdownMemoryHandler("openclaw", arguments.openclaw_root.resolve()),
        "hermes": HermesMemoryHandler(
            (str(arguments.hermes_python.absolute()), str(worker)),
            arguments.hermes_home.resolve(),
            timeout_seconds=30,
        ),
    }
    cleanup_actions = {
        target: PropagationAction(f"cleanup-{memory_id}-{target}", memory_id, target, "delete", None)
        for target in handlers
    }
    now = datetime.now(UTC)
    content = "A reversible portable-memory capacity demonstration record. " * 64
    if len(content) <= 2_200:
        raise _DemoError

    with tempfile.TemporaryDirectory(prefix="mindroom-provenance-demo-") as directory:
        store = ProvenanceMemoryStore(Path(directory) / "provenance.db")
        await store.open()
        try:
            await store.remember(
                memory_id=memory_id,
                owner_id="portfolio-demo-owner",
                scope="private-demo",
                content=content,
                purpose="portfolio-verification",
                citations=(
                    MemoryCitation(
                        source="portfolio-demo",
                        source_event_id="local-reversible-run",
                        content_digest=f"sha256:{hashlib.sha256(content.encode()).hexdigest()}",
                    ),
                ),
                consent=ConsentGrant(
                    actor_id="portfolio-demo-owner",
                    purpose="portfolio-verification",
                    granted_at=now,
                    expires_at=now + timedelta(hours=1),
                ),
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
            upserts = await MemoryPropagator(store, handlers).drain()
            if sorted(upserts.values()) != ["delivered"] * 3:
                raise _DemoError
            if not all(
                _markdown_path(root, memory_id).is_file()
                for root in (arguments.mindroom_root, arguments.openclaw_root)
            ):
                raise _DemoError
            if _hermes_fact_count(hermes_database, marker) != 1:
                raise _DemoError

            await store.delete(memory_id, actor_id="portfolio-demo-owner", observed_at=now + timedelta(minutes=1))
            deletes = await MemoryPropagator(store, handlers).drain()
            if sorted(deletes.values()) != ["delivered"] * 3:
                raise _DemoError
            if any(
                _markdown_path(root, memory_id).exists()
                for root in (arguments.mindroom_root, arguments.openclaw_root)
            ) or _hermes_fact_count(hermes_database, marker):
                raise _DemoError
            return {
                "cleanup_residue": 0,
                "content_characters": len(content),
                "targets_deleted": 3,
                "targets_upserted": 3,
            }
        finally:
            cleanup_failures = 0
            for target, handler in handlers.items():
                try:
                    action = cleanup_actions[target]
                    await handler(action, action.action_id)
                except Exception:
                    cleanup_failures += 1
            await store.close()
            cleanup_residue = sum(
                _markdown_path(root, memory_id).exists()
                for root in (arguments.mindroom_root, arguments.openclaw_root)
            ) + _hermes_fact_count(hermes_database, marker)
            if cleanup_failures or cleanup_residue:
                raise _DemoError


def main() -> None:
    """Execute the demonstration and print content-free evidence."""
    print(json.dumps(asyncio.run(_run(_arguments())), separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
