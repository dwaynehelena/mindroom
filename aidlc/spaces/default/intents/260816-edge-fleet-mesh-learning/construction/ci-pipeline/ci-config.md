# CI config — edge-fleet-mesh-learning

## Purpose

Wire the U1–U4 commands from code-summary / unit-test-instructions and the quality run in `construction/build-and-test/` (`build-and-test-summary`, `build-test-results`) into a merge-blocking GitHub Actions job. Existing repo CI remains; it is not adequate by itself for this intent.

## Existing CI (workspace profile)

| Workflow | Role | Adequate for this intent? |
|----------|------|---------------------------|
| `.github/workflows/pytest.yml` | `uv sync --all-extras` then `uv run pytest -m "not requires_matrix"` on `main` PR/push | **No** — quality recorded full-suite collect errors in optional extras; this job is not the U1–U4 gate |
| `.github/workflows/tach.yml` | `uv run tach check --dependencies --interfaces` | Orthogonal module-boundary check |
| `.github/workflows/security-scan.yml` | pip-audit / trufflehog / gitleaks, non-blocking (`\|\| true` / `--exit-code 0`) | Advisory only |
| `.github/workflows/build-mindroom.yml` | Docker images to GHCR on PR / CalVer dispatch | Product images; not the intent test gate |
| `.github/workflows/release.yml` | PyPI wheel + macOS app on dispatch | Release, not CI |
| `.github/workflows/calver-auto-release.yml` | Tag on `main` | Release trigger |

Infrastructure-design was **skipped** (local install, existing SQLite). No new CodeBuild/CodePipeline.

## Intent pipeline (added)

| File | Role |
|------|------|
| `.github/workflows/edge-fleet-mesh-learning.yml` | Merge-blocking job `U1–U4 intent suite` |
| `scripts/testing/run_edge_fleet_mesh_learning_ci.sh` | Same commands as `construction/build-and-test/test-results.md` |

### Triggers

- `push` to `main`
- `pull_request` targeting `main`
- `workflow_dispatch`

### Environment

- Runner: `ubuntu-latest` (onnxruntime Linux wheels exist; this host’s `macosx_15_0_x86_64` does not)
- Python: 3.13 via `uv python install`
- Install: `uv sync --locked --group dev` — **not** `--all-extras` (optional extras are the NFR5 collect-error set)
- Interpreter in CI: `uv run python`
- Interpreter on this checkout: `.venv/bin/python` (`uv run` blocked locally)

### Job steps

1. Checkout
2. Install uv + Python 3.13
3. `uv sync --locked --group dev`
4. Import check + docs existence + U1∪U2 listed ∪ U3 listed ∪ U3 FR3.3/FR3.4 extras ∪ U4 listed
5. `git diff --exit-code` (tests must not dirty the tree)

### Commands enforced (from build-test-results)

Build / import (from `build-instructions.md`):

```sh
"$PYTHON" -c "from mindroom.api.edge_fleet import create_edge_fleet_router; from mindroom.handshake_inspect import inspect_openclaw; print('import-ok')"
test -f docs/edge-fleet-rollback.md
test -f docs/edge-fleet.md
```

Tests (deduplicated U1∪U2 + U3 listed + extras + U4):

```sh
"$PYTHON" -m pytest \
  tests/test_edge_fleet.py \
  tests/test_edge_node.py \
  tests/test_edge_tailscale.py \
  tests/test_p9_remediation.py \
  tests/api/test_edge_fleet_api.py \
  tests/api/test_edge_fleet_lifecycle.py \
  tests/api/test_edge_fleet_revocation_api.py \
  tests/api/test_edge_fleet_revocation_core.py \
  tests/api/test_edge_fleet_security.py \
  tests/api/test_edge_fleet_live.py \
  tests/api/test_api.py::test_health_check \
  tests/test_learning_promotion.py \
  tests/test_learning_capture.py \
  tests/test_learning_runtime.py \
  tests/test_learning_loop.py \
  tests/test_learning_publishers.py \
  tests/test_learning_stable_publishers.py \
  tests/test_learning_candidates.py \
  tests/test_handshake_inspect.py \
  --tb=short
```

Expected from quality: **136 passed, 3 skipped, 0 failed** (88+15+25+8; skips are the three P9 bind-default tests).

### Explicitly not in this pipeline

- Full `pytest` / `./run-tests.sh` / `uv sync --all-extras`
- Live `require_tailscale()` helper (quality-only; not a worker path)
- Live OpenClaw/Hermes enroll → lease → complete over Tailscale
- Coverage-percentage floor (NFR5)
- New artifact publish (GHCR/PyPI unchanged)

## Local validation

```sh
cd /Users/dwayne/mindroom
PYTHON=/Users/dwayne/mindroom/.venv/bin/python bash scripts/testing/run_edge_fleet_mesh_learning_ci.sh -p no:cacheprovider
```

Do not use `uv run` on `macosx_15_0_x86_64`.

## Artifact repositories

No new registry. Pass/fail + logs only. Product publish stays on CalVer → GHCR/PyPI.