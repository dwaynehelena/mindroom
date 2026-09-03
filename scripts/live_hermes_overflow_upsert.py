"""Live end-to-end proof of the Hermes provenance overflow reference-pointer mode.

P2 UNBLOCK evidence: seeds one over-cap portable memory record, runs the real
HermesMemoryHandler against the real ProvenanceOverflowStore and the real Hermes
holographic store (via scripts/hermes_provenance_memory.py), then proves:

  1. the full payload is stored in the overflow store (Tier 2),
  2. a compact reference pointer (not the full body) is written into Hermes
     (Tier 1) and a worker receipt is returned,
  3. the pointer's memory_id resolves back to the full payload (read path).

Run:  MINDROOM_PROVENANCE_OVERFLOW_DB=<path> \\
       .venv/bin/python scripts/live_hermes_overflow_upsert.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from mindroom.provenance_handlers import HermesMemoryHandler
from mindroom.provenance_memory import PropagationAction
from mindroom.provenance_overflow import ProvenanceOverflowStore

# Real live locations (mirrors the LaunchAgent plist wiring).
_HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/Users/dwayne/.hermes"))
_OVERFLOW_DB = Path(
    os.environ.get("MINDROOM_PROVENANCE_OVERFLOW_DB")
    or "/Users/dwayne/.mindroom/mindroom_data/provenance_memory.db.overflow.db"
)
_WORKER = Path(os.environ.get("MINDROOM_PROVENANCE_HERMES_WORKER", "/Users/dwayne/mindroom/scripts/hermes_provenance_memory.py"))
_HERMES_PYTHON = Path(os.environ.get("MINDROOM_PROVENANCE_HERMES_PYTHON", "/Users/dwayne/.hermes/hermes-agent/.venv/bin/python"))

_MEMORY_ID = "live:p2:hermes-overflow:2026-08-09"
_OWNER = "@telegram_8411753427:localhost"
_SCOPE = "p2-hermes-overflow-proof"
_PURPOSE = "p2-hermes-overflow-validation"
_CONTENT = "Hermes P2 reference-mode live proof. " * 220  # ~6.5 KB, far over 2,200


def _summary(receipt: str, overflow: ProvenanceOverflowStore) -> dict[str, object]:
    return {
        "memory_id": _MEMORY_ID,
        "worker_receipt": receipt,
        "overflow_store": str(overflow.path),
        "content_length": len(_CONTENT),
        "hermes_home": str(_HERMES_HOME),
    }


async def main() -> int:
    if not _HERMES_HOME.is_dir():
        print(f"BLOCKED: HERMES_HOME {_HERMES_HOME} not a directory")
        return 2
    if not _HERMES_PYTHON.exists():
        print(f"BLOCKED: hermes python {_HERMES_PYTHON} missing")
        return 2
    if not _WORKER.exists():
        print(f"BLOCKED: worker {_WORKER} missing")
        return 2

    overflow = ProvenanceOverflowStore(str(_OVERFLOW_DB))
    await overflow.open()

    action = PropagationAction(
        action_id=hashlib_hex(_MEMORY_ID + "\x1fhermes\x1fupsert"),
        memory_id=_MEMORY_ID,
        target="hermes",
        operation="upsert",
        payload={
            "schema": "mindroom.provenance-memory/1",
            "memory_id": _MEMORY_ID,
            "owner_id": _OWNER,
            "scope": _SCOPE,
            "content": _CONTENT,
            "purpose": _PURPOSE,
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": None,
            "citations": [{"source": "live-p2-proof", "content_digest": None, "source_event_id": None}],
            "supersedes": None,
        },
    )

    handler = HermesMemoryHandler(
        (str(_HERMES_PYTHON), str(_WORKER)),
        _HERMES_HOME,
        overflow_store=overflow,
        content_threshold_chars=2000,  # force reference mode for this over-cap proof
        timeout_seconds=60,
    )

    try:
        receipt = await handler(action, action.action_id)
    except Exception as exc:  # noqa: BLE001
        print(f"BLOCKED: HermesMemoryHandler raised {type(exc).__name__}: {exc}")
        await overflow.close()
        return 3

    # 1. Full payload stored in the overflow store.
    stored = await overflow.fetch(_MEMORY_ID)
    if stored is None or stored.get("content") != _CONTENT:
        print("BLOCKED: full payload not resolvable from overflow store")
        await overflow.close()
        return 4

    # 2. Worker receipt confirmed.
    if not receipt.startswith("hermes-native:sha256:"):
        print(f"BLOCKED: unexpected worker receipt {receipt!r}")
        await overflow.close()
        return 5

    # 3. Reference pointer written into Hermes (compact), not the full body.
    stored_pointer = await _read_hermes_pointer()
    if stored_pointer is None:
        print("BLOCKED: no reference pointer found in Hermes holographic store")
        await overflow.close()
        return 6
    if len(stored_pointer) > 2200:
        print(f"BLOCKED: pointer not compact ({len(stored_pointer)} chars)")
        await overflow.close()
        return 8
    pointer = json.loads(stored_pointer)
    # The pointer must be a compact reference carrying a digest + preview, and
    # must NOT embed the full content body (the word "content" as a bare key is
    # expected only inside content_digest/content_preview/content_length).
    if not isinstance(pointer, dict) or pointer.get("mode") != "reference":
        print("BLOCKED: Hermes pointer is not a reference pointer")
        await overflow.close()
        return 7
    if "content" in pointer or pointer.get("content_preview") == _CONTENT:
        print("BLOCKED: full content leaked into Hermes pointer (not compact)")
        await overflow.close()
        return 7
    if not isinstance(pointer.get("content_digest"), str) or not pointer["content_digest"].startswith("sha256:"):
        print("BLOCKED: pointer missing content_digest")
        await overflow.close()
        return 7

    # 4. Pointer resolves to full payload (read path closure).
    resolved = await overflow.fetch(pointer.get("memory_id", _MEMORY_ID))
    if resolved is None or resolved.get("content") != _CONTENT:
        print("BLOCKED: pointer->payload read-back did not resolve full content")
        await overflow.close()
        return 9

    evidence = _summary(receipt, overflow)
    evidence["pointer_in_hermes"] = stored_pointer
    evidence["full_payload_resolvable"] = True
    evidence["pointer_chars"] = len(stored_pointer)
    print(json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False))
    await overflow.close()
    return 0


async def _read_hermes_pointer() -> str | None:
    """Read the reference-pointer fact back from the real Hermes holographic store."""
    sys.path.insert(0, str(_HERMES_HOME / "hermes-agent"))
    from plugins.memory.holographic.store import MemoryStore  # type: ignore[import-not-found,no-redef]

    store = MemoryStore(db_path=_HERMES_HOME / "memory_store.db")
    try:
        marker = f"[mindroom-provenance:{hashlib_hex(_MEMORY_ID)}]"
        for fact in store.list_facts(category="mindroom_provenance", limit=100_000):
            if marker in fact["content"]:
                # Strip the marker prefix; the rest is the pointer JSON.
                return fact["content"].split(marker, 1)[1].strip()
        return None
    finally:
        store.close()


def hashlib_hex(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))