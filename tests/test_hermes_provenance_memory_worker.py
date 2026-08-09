"""Unit tests for the Hermes provenance-memory worker's record encoding.

The worker (``scripts/hermes_provenance_memory.py``) bridges the provenance
outbox into Hermes' holographic SQLite fact store. It must accept two record
shapes from :class:`mindroom.provenance_handlers.HermesMemoryHandler`:

* ``full``  — ordinary records whose content fits inside the raised native
  ceiling (50,000 chars); the content is stored inline.
* ``reference`` — records externalized to the ``ProvenanceOverflowStore``
  (Tier 2). The worker stores only a compact pointer carrying a content
  preview and digest, never the full content.

This test locks in the ``reference`` path so a regression cannot silently
reject externalized records again.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_WORKER = Path(__file__).resolve().parents[1] / "scripts" / "hermes_provenance_memory.py"


@pytest.fixture(scope="module")
def worker() -> "object":
    """Import the worker module standalone (stdlib-only surface)."""
    spec = importlib.util.spec_from_file_location("hermes_provenance_memory_under_test", _WORKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_full_entry_keeps_content_inline(worker: object) -> None:
    """Full-mode records embed the content directly under the marker."""
    marker, entry = worker._entry(
        "mem-full-1",
        {
            "schema": "mindroom.provenance-memory/1",
            "memory_id": "mem-full-1",
            "owner_id": "@u:localhost",
            "scope": "s",
            "content": "ordinary citation-bearing record",
            "purpose": "p",
            "citations": [],
            "supersedes": None,
        },
    )
    assert marker in entry
    assert "ordinary citation-bearing record" in entry
    assert "Provenance:" in entry


def test_reference_entry_stores_compact_pointer(worker: object) -> None:
    """Reference-mode records store a pointer, not the full content."""
    full_content = "the full externalized body that must never be embedded in Hermes " * 2000
    payload = {
        "schema": "mindroom.provenance-memory/1",
        "memory_id": "mem-ref-1",
        "mode": "reference",
        "content_preview": "preview of the externalized content",
        "content_digest": "sha256:" + "a" * 64,
        "content_length": len(full_content),
        "provenance_store_path": "/tmp/overflow.db",
        "created_at": "2026-08-07T00:00:00+00:00",
    }
    marker, entry = worker._entry("mem-ref-1", payload)
    assert marker in entry
    # The full content must not be embedded; only the compact preview is kept.
    assert "must never be embedded in Hermes" not in entry
    assert "preview of the externalized content" in entry
    # The pointer must be a parseable JSON object carrying the digest/path.
    pointer = json.loads(entry[len(marker) :].strip())
    assert pointer["mode"] == "reference"
    assert pointer["memory_id"] == "mem-ref-1"
    assert pointer["content_digest"] == "sha256:" + "a" * 64
    assert pointer["content_length"] == len(full_content)
    assert pointer["provenance_store_path"] == "/tmp/overflow.db"
    # The pointer itself must stay well under the raised native ceiling.
    assert len(entry) < 2000


def test_reference_entry_requires_valid_pointer_fields(worker: object) -> None:
    """A reference payload must be a dict with mode == 'reference'."""
    # Missing mode falls through to full content handling → empty content rejected.
    with pytest.raises(ValueError):
        worker._entry("mem-ref-2", {"schema": "mindroom.provenance-memory/1", "memory_id": "mem-ref-2"})