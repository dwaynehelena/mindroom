# Build instructions — edge-fleet-mesh-learning

## Purpose

Build and execute the quality/NFR gate for intent `260816-edge-fleet-mesh-learning` against constructed units U1–U4. Test strategy in `aidlc-state.md` is **Minimal**. NFR-requirements / NFR-design stages were skipped; NFR1–NFR6 in `inception/requirements-analysis/requirements.md` are the targets.

## Prerequisites

- Workspace: `/Users/dwayne/mindroom`
- Python interpreter: `.venv/bin/python` (construction U3/U4 path)
- Optional: `uv` on PATH (preferred in `run-tests.sh`, **not** usable on this host — see Troubleshooting)
- Optional: `tailscale` on PATH for live NFR1 / FR1.3 helper probe

## Dependency installation

```sh
cd /Users/dwayne/mindroom
# Prefer the already-synced construction venv. Do not `uv sync` on this host
# (onnxruntime 1.25.1 has no wheel for macosx_15_0_x86_64).
test -x .venv/bin/python
.venv/bin/python -c "import mindroom, pytest; print('ok')"
```

## Environment

- Local single-user install only (NFR6).
- Fleet tests stub Tailscale; they do not require a live tailnet.
- Live helper probe (quality decision): `tailscale status` may be run separately.
- Do not set `MINDROOM_EDGE_FLEET_ENABLED` for unit tests; fixtures mount a temp store.

## Build commands

This is an interpreted Python package (`pythonpath = src`). There is no compile/bundle step for U1–U4.

```sh
cd /Users/dwayne/mindroom
.venv/bin/python -c "from mindroom.api.edge_fleet import create_edge_fleet_router; from mindroom.handshake_inspect import inspect_openclaw; print('import-ok')"
```

## Build verification

- Import of fleet, learning, and handshake modules succeeds.
- `docs/edge-fleet-rollback.md` exists (FR6.2).
- `docs/edge-fleet.md` quotes the C6 health sentence.

## Troubleshooting

| Symptom | Cause | Action |
|---------|-------|--------|
| `uv run pytest` fails with `onnxruntime==1.25.1` wheel error | Host is `macosx_15_0_x86_64`; wheel matrix has no matching tag | Use `.venv/bin/python -m pytest …` (construction path) |
| `tests/test_learning_runtime.py` collection ImportError `ApprovalDecision` | Leftover import from deleted product type | Quality applied a local test stub; re-run that file |
| Full-suite collect errors in Gmail/OAuth/livekit/crawl4ai | Optional extras not installed in `.venv` | Out of intent scope; do not treat as U1–U4 regression |