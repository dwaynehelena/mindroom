# Memory — deployment-pipeline (4.1)

## Interpretations

- 2026-08-20T19:42:46Z — Stage condition is CONDITIONAL (execute when CD needs creation or significant modification). Product GHCR/PyPI CD exists, but it does not activate the fleet. Intent therefore **executes** a local-activation CD path rather than skipping.
- 2026-08-20T19:42:46Z — `infrastructure-specification` / infrastructure-design are skipped. Per stage prose, inspect the real workspace (local SQLite, env flags, existing GitHub Actions) and do not invent AWS.
- 2026-08-20T19:42:46Z — Standing approvals from @dwayne:localhost fill `deployment-pipeline-questions.md` from NFR6 + ci-pipeline artifacts. Do not wait for a human Q&A turn or Learn ritual answer.
- 2026-08-20T19:42:46Z — `aidlc-orchestrate.ts report` is conductor-owned. This agent writes stage artifacts, the dry-run script, env/docs pointers, pipeline-validation, and `aidlc-state.md` next-action only.
- 2026-08-20T19:42:46Z — “Production deploy” for this intent means mounting the fleet on the local install. 4.1 documents and dry-runs that path; it does not set the live flag.

## Deviations

- 2026-08-20T19:42:46Z — Stage protocol Learn ritual and HARD STOP approval gate skipped under standing approvals (same as ci-pipeline 3.7).
- 2026-08-20T19:42:46Z — No CloudWatch Evidently, CodePipeline, or blue/green. Those would contradict NFR6 and the skipped infrastructure-design.
- 2026-08-20T19:42:46Z — Did not add a GitHub Actions deploy job that would auto-mount the fleet on merge.

## Tradeoffs

- 2026-08-20T19:42:46Z — Dedicated CD workflow vs documenting local activation. Chose documentation + dry-run script so merge cannot secretly enable the fleet.
- 2026-08-20T19:42:46Z — Commented `.env.example` keys vs writing the live `.env`. Chose example-only so 4.1 has no destructive side effect.

## Open questions

- 2026-08-20T19:42:46Z — Whether 4.2 environment-provisioning should skip (no AWS). Likely skip; coordinator decides after this stage.
- 2026-08-20T19:42:46Z — Whether a later operations stage runs live worker-over-Tailscale. Not required to close 4.1.
- 2026-08-20T19:42:46Z — Branch-protection required-check checkbox still repo-admin.

## Validation

- 2026-08-20T19:50:37Z — Dry-run `PYTHON=.venv/bin/python bash scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh` → workflow-yaml-ok, bash-n-ok, docs-ok, flag-off-ok, flag-false-ok, unmounted-routes-ok, handshake-surface-absent-ok, openclaw-learning-false-ok, env-example-ok, cd-dry-run-ok. First attempt imported `mindroom.api.main` before forcing the flag off and logged a composition-root mount against the live store path; checker now sets process env `MINDROOM_EDGE_FLEET_ENABLED=false` before that import. Live `~/.mindroom/.env` was not written.
