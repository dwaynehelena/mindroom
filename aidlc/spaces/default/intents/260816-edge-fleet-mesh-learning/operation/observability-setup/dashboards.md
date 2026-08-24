# Dashboards — edge-fleet-mesh-learning (4.4)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** observability-setup (4.4)
**Envelope:** local / read-only / config-only. No CloudWatch dashboard. No live host restart.

## Purpose

Give the single local operator one place to **see** fleet activation (NFR4, FR5.1 / C6). Canonical 4.4 CloudWatch dashboard JSON is **not** created: NFR6 is local-install only, Construction skipped `nfr-design` and `infrastructure-design`, and `infrastructure-specification` is absent.

## Upstream (consumed; several absent by design)

| Artifact | Path / status | How this dashboard uses it |
|----------|---------------|----------------------------|
| `performance-design` | **absent** — `construction/nfr-design/` skipped | No latency/throughput panels. Quality already recorded no quantitative performance NFR. |
| `security-design` | **absent** — same skip | Security visibility is fail-closed audit events + allowlist/tailnet/revoke, not AWS WAF widgets. |
| `reliability-design` | **absent** — same skip | Reliability widget = process `status=healthy` + SQLite integrity + launchd running. |
| `monitoring-design` | **absent** — same skip | This file **is** the local monitoring layout derived from NFR4 + C6. |
| `infrastructure-specification` | **absent** — `construction/infrastructure-design/` skipped | No AWS account, no CW namespace. |
| `deployment-execution` | `operation/deployment-execution/` | Live pid 14783 already exposes C6; 4.4 observes, does not remount. |

## Operator dashboard (local, not CloudWatch)

Layout for one operator on this machine. Panels are **commands and JSON fields**, not widgets pushed to AWS.

### Panel 1 — Activation (golden: errors + traffic demand)

Source of truth: `GET http://127.0.0.1:8765/api/health` field `edge_fleet` (`docs/edge-fleet.md`).

| Field | Meaning |
|-------|---------|
| `status` (top-level) | Process health (`healthy`) |
| `edge_fleet.enabled` | Mounted (`true`) vs unmounted (`false`) |
| `edge_fleet.healthy_nodes` | Count of nodes seen within health max-age |
| `edge_fleet.status` | **Quote this sentence** (FR5.1) |
| `edge_fleet.error` | Present only on probe failure |

Sentences to quote (do not invent others):

- Unmounted: `Edge fleet is unmounted; production activation is not live.`
- Mounted: `Edge fleet is mounted with {n} healthy node(s).`
- Mounted error: `Edge fleet is mounted but health is unavailable.`

### Panel 2 — Process / saturation

- launchd `gui/501/chat.mindroom.local`: `state`, `pid`, `runs`, `active count`
- Listen: `lsof -nP -iTCP:8765 -sTCP:LISTEN`
- Store open: pid holds `{storage_root}/edge_fleet.db` (+ wal/shm)
- Workers: `pgrep -lf mindroom.edge_node` (expected empty unless an operator started one)

### Panel 3 — Existing product dashboard (not fleet SLI)

`docs/dashboard.md`: web UI at `http://localhost:8765` (stats cards, agents, rooms). That UI is **config**, not the C6 activation sentence. Do not treat agent counts as fleet golden signals.

### Panel 4 — Log tail (NFR4)

- Current process file: `{storage_root}/logs/mindroom_<start>.log`
- LaunchAgent: `/Users/dwayne/Library/Logs/mindroom/stderr.log` and `stdout.log`
- Look for: `Edge fleet enabled`, `Edge fleet routes mounted`, `Edge fleet database opened`, `Edge fleet audit event`

## This-run snapshot (observe only)

UTC `2026-08-20T20:33:05.320728+00:00`. Pid **14783** (unchanged from 4.3). HTTP **200**.

```json
{
  "status": "healthy",
  "edge_fleet": {
    "enabled": true,
    "healthy_nodes": 0,
    "status": "Edge fleet is mounted with 0 healthy nodes."
  }
}
```

Quoted `edge_fleet.status`: `Edge fleet is mounted with 0 healthy nodes.`

Workers: `no-edge-node-workers`. Zero healthy nodes is **mounted-idle**, not an alarm (see `alarms.md`).

Evidence: `operation/observability-setup/evidence-2026-08-20-local-safety/health.json`.

## CloudWatch skip (required by envelope)

Canonical stage prose would `put-dashboard` in CloudWatch. **Not executed.**

- `did-not-run: aws cloudwatch put-dashboard` (`evidence-2026-08-20-local-safety/cloud-skip.txt`)
- `which aws` = not-found (not invoked)
- No dashboard ARN, no widget JSON uploaded

## Explicitly not done

- Live host restart / `launchctl kickstart`
- Live `~/.mindroom/.env` write
- Starting OpenClaw/Hermes workers
- AWS / kubectl / remote Actions
