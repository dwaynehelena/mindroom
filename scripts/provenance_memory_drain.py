"""Drain the durable provenance-memory outbox into three native runtime handlers."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from mindroom.provenance_handlers import HermesMemoryHandler, MarkdownMemoryHandler
from mindroom.provenance_memory import (
    MemoryPropagator,
    PropagationHandler,
    ProvenanceMemoryStore,
    RuntimeTarget,
)
from mindroom.provenance_overflow import ProvenanceOverflowStore

_ENV_KEYS = (
    "MINDROOM_PROVENANCE_DB",
    "MINDROOM_PROVENANCE_MINDROOM_ROOT",
    "MINDROOM_PROVENANCE_OPENCLAW_ROOT",
    "MINDROOM_PROVENANCE_HERMES_HOME",
    "MINDROOM_PROVENANCE_HERMES_PYTHON",
    "MINDROOM_PROVENANCE_HERMES_WORKER",
)

# Optional: path to the Tier-2 external overflow store. When unset it defaults
# to a sibling file next to the provenance DB (``<provenance-db>.overflow.db``),
# so the externalization path is always available without breaking callers that
# only configured the provenance DB.
_OVERFLOW_DB_ENV = "MINDROOM_PROVENANCE_OVERFLOW_DB"


def _required_path(name: str, *, directory: bool = False) -> Path:
    raw = os.environ.get(name)
    if not raw:
        message = f"required environment path is missing: {name}"
        raise RuntimeError(message)
    path = Path(raw)
    if not path.is_absolute():
        message = f"required environment path must be absolute: {name}"
        raise RuntimeError(message)
    if directory and (path.is_symlink() or not path.is_dir()):
        message = f"required environment directory is unavailable: {name}"
        raise RuntimeError(message)
    return path


async def _main() -> None:
    for key in _ENV_KEYS:
        if not os.environ.get(key):
            message = f"required provenance worker setting is missing: {key}"
            raise RuntimeError(message)
    store = ProvenanceMemoryStore(_required_path("MINDROOM_PROVENANCE_DB"))
    await store.open()
    provenance_db = Path(os.environ["MINDROOM_PROVENANCE_DB"])
    overflow_path = os.environ.get(_OVERFLOW_DB_ENV) or str(provenance_db.with_name(f"{provenance_db.name}.overflow.db"))
    overflow = ProvenanceOverflowStore(overflow_path)
    await overflow.open()
    try:
        quarantined = await store.recover_uncertain()
        handlers: dict[RuntimeTarget, PropagationHandler] = {
            "mindroom": MarkdownMemoryHandler(
                "mindroom",
                _required_path("MINDROOM_PROVENANCE_MINDROOM_ROOT", directory=True),
            ),
            "openclaw": MarkdownMemoryHandler(
                "openclaw",
                _required_path("MINDROOM_PROVENANCE_OPENCLAW_ROOT", directory=True),
            ),
            "hermes": HermesMemoryHandler(
                (
                    str(_required_path("MINDROOM_PROVENANCE_HERMES_PYTHON")),
                    str(_required_path("MINDROOM_PROVENANCE_HERMES_WORKER")),
                ),
                _required_path("MINDROOM_PROVENANCE_HERMES_HOME", directory=True),
                overflow_store=overflow,
            ),
        }
        outcomes = await MemoryPropagator(store, handlers).drain()
        print(
            json.dumps(
                {"outcomes": outcomes, "quarantined": quarantined},
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )
    finally:
        await overflow.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(_main())
