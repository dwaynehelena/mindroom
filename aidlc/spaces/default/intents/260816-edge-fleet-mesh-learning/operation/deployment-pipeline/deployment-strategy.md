# Deployment strategy — edge-fleet-mesh-learning

**Date:** 2026-08-20  
**Intent:** 260816-edge-fleet-mesh-learning  
**Strategy class:** feature-flag activation on a local process (not blue/green, not canary traffic, not rolling k8s).

## Why not blue/green, canary, or rolling

Those strategies assume multiple networked instances behind a load balancer. `infrastructure-specification` does not exist: Construction skipped `infrastructure-design` because NFR6 is a local single-user install with existing SQLite. Org memory “deploy on merge to staging” applies to hosted product tracks, not this intent.

The analogous safety net is **fail-closed defaults** (NFR1) plus **flag-off rollback** (FR6.1 / FR6.2).

## Strategy

**Trunk + flag.** Every commit that passes `quality-gates` / `ci-config` / `cicd-pipeline` is a local-activation candidate. The running local process stays unmounted until the operator sets:

1. `MINDROOM_EDGE_FLEET_ENABLED=true`
2. `MINDROOM_EDGE_FLEET_ENROLLMENT_KEY` (URL-safe Base64, decoded ≥ 32 bytes)
3. `MINDROOM_EDGE_FLEET_NODE_ALLOWLIST` to the node ids that may enroll

Then restart MindRoom. Confirm with `GET /api/health` `edge_fleet.status` (C6 / FR5.1).

Workers are **not** started by MindRoom. After mount, the operator may run the documented `python -m mindroom.edge_node` commands from `docs/edge-fleet.md`. That live worker-over-Tailscale path is **authorized later, not in 4.1**. 4.1 only documents and dry-runs the path.

## Activation sequence (U1–U4)

### U1 — hygiene first

- Default unmounted (missing flag / short key).
- Allowlist `None` denies enroll even if mounted.
- Tailnet fail-closed on enroll/heartbeat/lease/complete.
- Revoke is signed-in operator DELETE, 204/404.

### U2 — live fleet after mount

- `POST /api/edge-fleet-admin/enrollments` (C3)
- `POST /api/edge-fleet-admin/jobs` (C4)
- Operator-started OpenClaw or Hermes `EdgeNodeClient` (C5)
- Health sentence is the source of truth (C6)

Bind workers to loopback (`http://127.0.0.1:8765`). Do not treat residual CLI `--api-host 0.0.0.0` as a new fleet listener (NFR2 minor m1).

### U3 — learning promotion (no deploy unit)

Capture/promotion already runs in-process after a successful visible reply. Canary/stable publishers write to runtime roots; they are not a second environment. Keep OpenClaw-named agent `learning: false`.

### U4 — handshake not deployed

C8 closed `surface_absent`. Do not start MeshGateway. Do not set `MINDROOM_MESH_ENROLLMENT`.

## Environment promotion path

```
PR / push to main
        │
        ▼
GitHub Actions cicd-pipeline
  .github/workflows/edge-fleet-mesh-learning.yml
  (uv sync --locked --group dev; intent pytest)
        │ pass = 136 passed, 3 skipped, 0 failed
        ▼
main = local-activation candidate
        │
        │  operator (manual)
        ▼
unmounted local  ──enable flags + restart──►  mounted local
        ▲                                          │
        └──────── flag-off + written cleanup ──────┘
```

Local validation of the candidate (this host cannot `uv run` because onnxruntime has no `macosx_15_0_x86_64` wheel):

```sh
PYTHON=/Users/dwayne/mindroom/.venv/bin/python \
  bash scripts/testing/run_edge_fleet_mesh_learning_ci.sh -p no:cacheprovider
```

CD dry-run (no live mount):

```sh
PYTHON=/Users/dwayne/mindroom/.venv/bin/python \
  bash scripts/testing/run_edge_fleet_mesh_learning_cd_dry_run.sh
```

## What 4.1 will not do

- Set `MINDROOM_EDGE_FLEET_ENABLED=true` on the live `.env`.
- Start OpenClaw/Hermes workers or mutate Tailscale ACLs.
- Dispatch remote GitHub Actions.
- Publish a new GHCR/PyPI artifact.
- Provision AWS (that would be 4.2 **if** infrastructure existed; it does not).
