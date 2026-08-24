# Memory — ci-pipeline (3.7)

## Interpretations

- 2026-08-20T16:00:00Z — Stage condition is CONDITIONAL (skip if CI exists and is adequate). Existing `.github/workflows/pytest.yml` runs `uv run pytest -m "not requires_matrix"` after `uv sync --all-extras`. That is **not** adequate for this intent: it does not enforce the U1–U4 commands recorded in `construction/build-and-test/test-results.md`, and quality explicitly did not verify the full suite. Stage therefore **executes**, it does not skip.
- 2026-08-20T16:00:00Z — Standing approvals from @dwayne:localhost are in force. Fill `ci-pipeline-questions.md` from the workspace profile (GitHub Actions, trunk-based `main`, GHCR/PyPI already present, local-install NFR6). Do not wait for a human Q&A turn.
- 2026-08-20T16:00:00Z — `aidlc-orchestrate.ts report` is conductor-owned. This agent writes stage artifacts, the workflow YAML, the phase-check, and `aidlc-state.md` next-action only.
- 2026-08-20T16:00:00Z — Per-unit `construction/*/code-generation/traceability.json` files are absent. Build-and-test already substituted `construction/build-and-test/cross-unit-traceability.md` with verdict PASS and no unresolved findings. Do not invent those JSON files and do not restart code-generation.
- 2026-08-20T16:00:00Z — GitHub Actions `ubuntu-latest` has onnxruntime Linux wheels. Local `macosx_15_0_x86_64` does not. CI uses `uv`; local validation uses `.venv/bin/python`.
- 2026-08-20T16:00:00Z — Live Tailscale helper and live worker-over-Tailscale are **not** CI jobs. Quality ran the helper only; C5 remains in-process HTTP.

## Deviations

- 2026-08-20T16:00:00Z — Stage protocol Learn ritual asks “Anything to add for next time?” before the gate. Standing approvals override waiting on that human question.
- 2026-08-20T16:00:00Z — Stage protocol HARD STOP at the approval gate. Standing approvals close the gate without presenting Approve / Request Changes.
- 2026-08-20T16:00:00Z — Did not change `.github/workflows/pytest.yml` or install optional extras to make full `tests/` collect green. That is NFR5 residual minor m2, out of U1–U4, and not a minimal pipeline fix.

## Tradeoffs

- 2026-08-20T16:00:00Z — Dedicated intent workflow vs only documenting existing pytest.yml. Chose a dedicated workflow so the merge gate matches the recorded U1–U4 commands even when extras collect is red.
- 2026-08-20T16:00:00Z — Always-on PR/push to `main` vs path filters. Chose always-on because fleet/learning/handshake touch shared files (`api/main.py`, `post_response_effects.py`) and the suite is small.
- 2026-08-20T16:00:00Z — `uv sync --group dev` (no `--all-extras`) for the intent job so CI does not depend on optional extras that quality could not collect locally.

## Open questions

- 2026-08-20T16:00:00Z — Whether Operation should add a live worker enroll/lease/complete over Tailscale. Not required to close ci-pipeline.
- 2026-08-20T16:00:00Z — Whether branch protection will mark `edge-fleet-mesh-learning` required. Documented in quality-gates.md; setting GitHub rulesets is Operation / repo admin, not this stage.

## Validation

- 2026-08-20T16:30:00Z — Local `PYTHON=.venv/bin/python bash scripts/testing/run_edge_fleet_mesh_learning_ci.sh -p no:cacheprovider` → 136 passed, 3 skipped, 2 warnings in 27.93s. Workflow YAML parses. Phase check PASS.