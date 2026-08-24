# Reverse Engineering — Questions

## Sources

- [scope] Workflow-selected scope: `edge-fleet-mesh-learning-activation`.
- `aidlc/spaces/default/intents/intents.json` records `repos: ["aidlc-workflows"]` for this intent.
- `scope-document.md` and `initiative-brief.md` name Edge Fleet, mesh handshake, and the learning loop — all in the MindRoom workspace, not in the workflow framework.

## Q1. Which repository should Reverse Engineering scan?

This intent was recorded as touching only `aidlc-workflows` (the AI-DLC workflow framework copied under this workspace).
There is no existing knowledge store for that repo, or for `mindroom`.
The activation work lives in the MindRoom workspace root: worker enrollment, mesh coordination, and the learning loop.
Scanning the recorded repo would build a knowledge base of the workflow framework and send every later stage at the wrong code.

- A. Scan the MindRoom workspace root — that is where the fleet, mesh, and learning-loop code lives
- B. Scan only `aidlc-workflows`, as recorded at intent birth
- C. Scan both
- X. Other (please specify)

[Answer]: A. Scan the MindRoom workspace root — that is where the fleet, mesh, and learning-loop code lives
**Mode:** guided
**Recorded:** 2026-08-16T12:59:00Z
