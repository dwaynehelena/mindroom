# Memory — environment-provisioning (4.2)

## Interpretations

- 2026-08-20T19:58:22Z — Stage condition is CONDITIONAL ("Execute when AWS environments need provisioning or validation"). AWS is not needed (NFR6, skipped infrastructure-design). Coordinator dispatch still requires 4.2 to **execute** as local-SQLite inventory rather than leaving the stage blank. Skip AWS, do not skip the stage artefacts.
- 2026-08-20T19:58:22Z — `infrastructure-specification` is a required consume but the file is absent. Per brownfield guidance used in 4.1 and `deployment-execution` prose ("never invent the content of a missing artifact"), inventory the real local env and cite the absence.
- 2026-08-20T19:58:22Z — Standing approvals from @dwayne:localhost fill questions and skip Learn/HARD STOP gate (same as 4.1 / 3.7).
- 2026-08-20T19:58:22Z — Live `~/.mindroom/.env` already has `MINDROOM_EDGE_FLEET_ENABLED=true` from 2026-08-10. 4.2 workflow does **not** require a live write; documented local-activation path is operator edit + restart, which 4.1 already recorded. Do not write `.env`. Do not restart the host process.
- 2026-08-20T19:58:22Z — `aidlc-orchestrate.ts report` is conductor-owned. This agent writes stage artefacts, validation, and `aidlc-state.md` next-action only.

## Deviations

- 2026-08-20T19:58:22Z — Did not load AWS platform persona work product (no AWS). Did not call Secrets Manager, VPC, or IaC apply.
- 2026-08-20T19:58:22Z — Stage protocol Learn ritual and HARD STOP approval gate skipped under standing approvals.
- 2026-08-20T19:58:22Z — Did not treat pre-existing enable keys as a 4.2 production deploy.

## Tradeoffs

- 2026-08-20T19:58:22Z — Restart running MindRoom to obtain C6 `edge_fleet` health vs leave process untouched. Chose no restart: dispatch forbids live mutation unless the 4.2 workflow explicitly requires it; it does not. Health-without-fragment is recorded as observation for 4.3.
- 2026-08-20T19:58:22Z — Blank skip vs local inventory documents. Chose inventory so 4.3 has `environment-inventory` to consume.

## Open questions

- 2026-08-20T19:58:22Z — Whether 4.3 deployment-execution performs the documented local restart/mount. Not required to close 4.2.
- 2026-08-20T19:58:22Z — Why GET `/api/health` from pid 35649 omits `edge_fleet` while workspace `main.py` adds it. Likely process started before this workspace revision; confirm after restart in a later operations stage.

## Validation

- 2026-08-20T20:04:14Z — Dry-run `PYTHON=.venv/bin/python bash scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh` → workflow-yaml-ok, bash-n-ok, docs-ok, flag-off-ok, flag-false-ok, unmounted-routes-ok, handshake-surface-absent-ok, openclaw-learning-false-ok, env-example-ok, cd-dry-run-ok. Live `~/.mindroom/.env` was not written (mtime 2026-08-10T17:27:48Z). GET /api/health on pid 35649 had no edge_fleet fragment; store file was not open. AWS not provisioned.
