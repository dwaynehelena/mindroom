# Smoke test results — edge-fleet-mesh-learning (4.3)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Scope:** local dry-run + read-only live smoke. Not live host restart. Not live worker-over-Tailscale. Not full `tests/` (quality minor m2). Not remote Actions.

## Upstream gates already green (not re-run as full suite)

- `build-test-results`: intent suite **136 passed / 3 skipped** (ci-pipeline 3.7 / quality PASS-WITH-MINORS). Not re-collected this run.
- CD dry-run (constructor isolation, no live write), this run:

```
PYTHON=/Users/dwayne/mindroom/.venv/bin/python \
  bash scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh
# python: /Users/dwayne/mindroom/.venv/bin/python
# workflow-yaml-ok bash-n-ok docs-ok flag-off-ok flag-false-ok
# unmounted-routes-ok handshake-surface-absent-ok openclaw-learning-false-ok
# env-example-ok cd-dry-run-ok cd-dry-run-complete
# EXIT:0
```

Evidence: `operation/deployment-execution/evidence-2026-08-20-local-safety/cd-dry-run.log`

## Live smoke (observe existing pid 14783; no restart)

No enrollment token issued. No job queued. No `python -m mindroom.edge_node`. UTC `2026-08-20T20:27:12Z`.

| Method | Path | HTTP | Body excerpt |
|--------|------|------|--------------|
| GET | `/api/health` | 200 | `{"status":"healthy",...,"edge_fleet":{"enabled":true,"healthy_nodes":0,"status":"Edge fleet is mounted with 0 healthy nodes."}}` |
| GET | `/api/edge-fleet/enroll` | 404 | `{"detail":"Not found"}` |
| POST | `/api/edge-fleet/enroll` | 422 | `{"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]}` |
| GET | `/api/edge-fleet/lease` | 404 | `{"detail":"Not found"}` |
| POST | `/api/edge-fleet/lease` | 422 | missing `X-Edge-Node-ID` / `X-Edge-Timestamp` / `X-Edge-Nonce` headers |
| GET | `/api/edge-fleet-admin/enrollments` | 404 | `{"detail":"Not found"}` |
| POST | `/api/edge-fleet-admin/enrollments` | 401 | `{"detail":"Missing or invalid credentials"}` |
| GET | `/api/edge-fleet-admin/jobs` | 404 | `{"detail":"Not found"}` |
| POST | `/api/edge-fleet-admin/jobs` | 401 | `{"detail":"Missing or invalid credentials"}` |

Interpretation:

- Worker `POST /api/edge-fleet/enroll` and `POST /api/edge-fleet/lease` are **not** 404 (422 validation) → routers exist on the running process.
- Unauthenticated admin `POST /api/edge-fleet-admin/*` is 401, not 404.
- Handshake remains `surface_absent` (C8): dry-run `handshake-surface-absent-ok`. Do not set `MINDROOM_MESH_ENROLLMENT` (absent in live `.env`).
- U3 learning promotion has no new HTTP; OpenClaw `learning:` stays false (dry-run).
- Empty POST bodies were used only to prove route presence; no enroll/job payload was sent.

## SQLite (read-only)

- Path: `/Users/dwayne/.mindroom/mindroom_data/edge_fleet.db`
- integrity_check: `ok`
- journal_mode: `wal`
- open by pid 14783: `True`
- counts: `edge_node=10`, `edge_job=6`, `edge_enrollment_nonce=19`, `edge_request_nonce=24`, `edge_fleet_audit=0`

No migration job. `EdgeFleet.open()` on process start is the schema path (C2). Store was **not** deleted or remounted.

## Workers / Tailscale

- `pgrep -lf mindroom.edge_node` → `no-edge-node-workers`
- Tailscale status-only: self `macbook-pro-3` True `['100.80.168.13', 'fd7a:115c:a1e0::b839:a80e']` — not mutated

## Result

- Smoke: **PASS** (read-only)
- CD dry-run: **PASS** exit 0
- Live restart: **SKIPPED**
- Workers started: **no**
- Destructive cleanup: **no**