# Bottleneck analysis - edge-fleet-mesh-learning (4.6)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** performance-validation (4.6)
**Envelope:** local-safety. Hypotheses from idle observe; **not** a profiler run under load.

## Purpose

Stage Step 5 names bottleneck analysis. Without a load run, this file records idle saturation signals from 4.4 `dashboards.md` Panel 2 and the 4.6 observe snapshot. It does not invent CloudWatch CPU/IO charts.

## Upstream (consumed; several absent by design)

`performance-design` and `scalability-design` are **absent**. `dashboards` present. No designed bottleneck list to validate.

## Idle snapshot (UTC 2026-08-20T20:51:50.926766+00:00)

| Surface | Observed | Bottleneck? |
|---------|----------|-------------|
| Process | pid 14783, launchd `state=running`, `runs=8` | No; process serving |
| Listen | `*:8765` uvicorn | Single process; not saturated this run |
| Health | HTTP 200, 42.16 ms one sample | Not a load bottleneck |
| Workers | none | Idle (A7); no worker-side bottleneck |
| SQLite | integrity ok, WAL, held by 14783, audit 0 | Store open; not contended this run |
| Tailscale | not exercised | Not measured |

## Hypotheses if an authorized load window runs later

1. Single uvicorn worker on the LaunchAgent process.
2. SQLite WAL serialization on `edge_fleet.db` for enroll/heartbeat/lease/complete.
3. Tailscale connectivity check on every node op (fail-closed; correctness first).
4. Allowlist / attestation CPU is not expected to dominate at single-user scale.

None of 1-4 were profiled. Do not treat them as measured findings.

## Explicitly not done

- `ab` / k6 / locust saturation sweep
- `sample` / `py-spy` against pid 14783 (would still be a live-process inspection under load intent; not required)
- AWS X-Ray traces
