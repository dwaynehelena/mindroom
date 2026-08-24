# CD config — edge-fleet-mesh-learning

**Date:** 2026-08-20  
**Intent:** 260816-edge-fleet-mesh-learning  
**Stage:** deployment-pipeline (4.1)  
**NFR6:** local single-user install only. No hosted / Kubernetes / GHCR promotion for this intent.

## Purpose

Define the continuous-delivery path for constructed units U1–U4. Passing Construction CI (`ci-config`, `quality-gates`, `cicd-pipeline`) makes a commit a **release candidate for local activation**, not an auto-deploy. This stage does **not** claim a production deploy, live worker-over-Tailscale, or full `tests/` verification.

## Upstream inputs

| Artifact | Path / status | How this CD path uses it |
|----------|---------------|--------------------------|
| `ci-config` | `construction/ci-pipeline/ci-config.md` | Merge-blocking job, interpreter split (`uv` in CI / `.venv/bin/python` locally), explicit non-goals |
| `quality-gates` | `construction/ci-pipeline/quality-gates.md` | 136 passed / 3 skipped / 0 failed is the promotion gate into local activation |
| `cicd-pipeline` | `.github/workflows/edge-fleet-mesh-learning.yml` + `scripts/testing/run_edge_fleet_mesh_learning_ci.sh` | Existing GitHub Actions workflow; no second cloud pipeline |
| `infrastructure-specification` | **absent** — Construction `infrastructure-design` was skipped (local install, existing SQLite) | Do not invent AWS/CodePipeline. Target is the operator's local MindRoom process + `{storage_root}/edge_fleet.db` |

Brownfield product CD (unchanged, out of this intent): `build-mindroom.yml` → GHCR, `release.yml` → PyPI, `calver-auto-release.yml` tags. Those publish the product; they do **not** mount the fleet.

## Pipeline as code

There is no new GitHub Actions deploy job and no CodePipeline. Delivery is:

1. **Build/test (already exists):** `cicd-pipeline` job `U1–U4 intent suite` on `push`/`pull_request` to `main` and `workflow_dispatch`.
2. **Merge to trunk:** squash-merge to `main` (org Way of Working).
3. **Local activation (this stage):** operator sets env flags on the local install and restarts MindRoom. Default remains **unmounted**.
4. **Rollback:** FR6.1 flag-off + FR6.2 written cleanup (`docs/edge-fleet-rollback.md`).

Validation of this CD path is a **dry-run** (`scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh`). It must not start a production process, write the live `.env`, mutate Tailscale, or start OpenClaw/Hermes workers.

## Artifact repositories

None added. CI produces pass/fail + logs only (`ci-config.md`). Local activation uses the interpreted package on `pythonpath = src` (or the already-synced `.venv`).

## Feature flags (not CloudWatch Evidently / AppConfig)

NFR6 forbids a cloud flag service. Flags are local env, resolved by `RuntimePaths` (process env, then config-adjacent `.env`):

| Flag / env | Default | Mount / admit effect |
|------------|---------|----------------------|
| `MINDROOM_EDGE_FLEET_ENABLED` | false / unset | Missing or false → fleet `None`, routers unmounted (FR6.1) |
| `MINDROOM_EDGE_FLEET_ENROLLMENT_KEY` | unset | Missing, invalid Base64, or decoded length < 32 → unmounted |
| `MINDROOM_EDGE_FLEET_NODE_ALLOWLIST` | unset → `None` | `None` denies every enroll (FR1.5) |
| `MINDROOM_EDGE_FLEET_PATH` | `{storage_root}/edge_fleet.db` | SQLite store (C2) |
| OpenClaw agent `learning:` | `false` in `config.yaml` | FR3.5 — Agno learning stays off |
| `MINDROOM_MESH_ENROLLMENT` | **removed** | C8 `surface_absent`; do not reintroduce |

## Environment promotion matrix

| Environment | What it is | How you enter | Gate | Auto? |
|-------------|------------|---------------|------|-------|
| **Unmounted local** (default) | Local MindRoom with fleet routers absent | Missing/false flag or short/missing key | Always the ship default | Yes (code default) |
| **CI candidate** | Commit that passed U1–U4 intent suite | `edge-fleet-mesh-learning.yml` green | `quality-gates.md` 136/3/0 | Yes on PR/push |
| **Mounted local** (activation) | Same process, routers mounted, allowlist set | Operator writes env + restart | CI candidate **and** explicit local enable | **No** — manual |
| **Hosted / k8s / GHCR** | Product images and cluster charts | Existing product CD | Out of scope (NFR6). Charts have no `MINDROOM_EDGE_FLEET_*` keys | Do not use for this intent |

Branch-protection “required check” for the intent workflow remains **repo-admin** work (`quality-gates.md`). This stage documents it; it does not flip GitHub rulesets.

## Approval workflow for production activation

Standing human approval from `@dwayne:localhost` is the sole production-activation sign-off recorded in ideation (intent-capture Q5/Q12). This CD path still does **not** auto-set `MINDROOM_EDGE_FLEET_ENABLED=true`. The operator must edit the local env and restart. Destructive live Tailscale/OpenClaw mutation is out of this stage.

## Residual minors carried forward (do not block 4.1)

From `construction/build-and-test/build-and-test-summary.md`:

- m1 NFR2 residual API/CLI/orchestrator bind `0.0.0.0` — not a new fleet listener; mounted local should keep workers on loopback (`docs/edge-fleet.md`).
- m2 NFR5 full `tests/` not green — not a CD gate.
- m3 HTTP 422 deprecation; m4 asyncio mark on sync test.
- Live worker-over-Tailscale **not** run. Remote GitHub Actions **not** run.

## Mapping to units

| Unit | What CD delivers |
|------|------------------|
| U1 fleet-hygiene | Flag-off default, revoke/allowlist/tailnet fail-closed, rollback runbook |
| U2 live-fleet | Health sentence + operator start commands after **manual** mount |
| U3 learning-promotion | In-process capture/promotion; no new HTTP; OpenClaw `learning: false` |
| U4 handshake | Inspect-only `surface_absent`; no mesh deploy |
