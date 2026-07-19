---
title: Provenance Memory Fabric
summary: Consent-bound portable memory and receipt-backed deletion across three native runtimes.
---

# Provenance Memory Fabric

`ProvenanceMemoryStore` owns the canonical lifecycle. Every record is bound to an owner, scope, purpose, consent grant, creation time, optional TTL, and source citations. Contradictions supersede an active record in the same owner/scope transaction. Export filters deleted, superseded, expired, wrong-purpose, and wrong-actor records.

Each accepted write or deletion creates one stable outbox action per MindRoom, OpenClaw, and Hermes target. `MemoryPropagator` claims actions durably, invokes targets independently, records content-free receipts, sanitizes failures, and quarantines an interrupted in-flight attempt instead of replaying an uncertain mutation.

## Runtime storage

- MindRoom and OpenClaw use private, current-user-owned native memory directories. Each portable record is an atomic mode-`0600` Markdown file whose filename is a hash of the memory identity. Deletion removes that exact file idempotently.
- Hermes uses its native holographic SQLite fact store in `$HERMES_HOME/memory_store.db`, category `mindroom_provenance`. A stable hashed marker provides idempotent update/delete identity without exposing the source identity in filenames or logs. The holographic store avoids the 2,200-character total budget of Hermes' curated prompt-memory file. Set Hermes `memory.provider` to `holographic` when these facts must participate in automatic model retrieval; propagation and deletion remain valid native-store mutations independently of that runtime retrieval setting.

The Hermes adapter sends bounded NDJSON over stdin to an explicit virtual-environment Python and worker path. It uses no shell, passes no memory content in process arguments, requires an exact idempotency key and response schema, scans candidate content with Hermes' native memory guard, and accepts only a receipt bound to the action.

## Operation and verification

`scripts/provenance_memory_drain.py` drains a configured production ledger. All seven absolute paths are mandatory environment settings; runtime roots must already exist and pass the handler safety checks.

`scripts/provenance_memory_demo.py` is a reversible live check. It writes a cited and consent-bound 3,840-character record to the installed MindRoom, OpenClaw, and Hermes stores, verifies all three native representations, propagates the tombstone to all three, and verifies zero record residue. Its `finally` cleanup independently retries each exact deletion so an assertion failure cannot strand demonstration content.

Run it from the repository environment:

```bash
PYTHONPATH=src .venv/bin/python scripts/provenance_memory_demo.py
```

Successful output is content-free and reports three upserts, three deletions, and zero cleanup residue.
