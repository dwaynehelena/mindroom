# Capacity planning - edge-fleet-mesh-learning (4.6)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** performance-validation (4.6)
**Envelope:** local-safety. Baseline from idle observe only.

## Purpose

Stage Step 5 names capacity planning recommendations. The operations guide capacity template (concurrent users, RPS, 6/12-month targets) does not apply: `scalability-requirements` absent, NFR6 local single-user.

## Upstream (consumed; several absent by design)

`scalability-requirements` / `scalability-design` **absent**. `performance-requirements` **absent**. `dashboards` present for current baseline signals.

## Current baseline (measured idle, not load)

| Dimension | Current | 6-month | 12-month | Scaling mechanism |
|-----------|---------|---------|----------|-------------------|
| Concurrent users | 1 local operator | not specified | not specified | none (NFR6) |
| Fleet workers | 0 running (`pgrep` empty) | not specified | not specified | operator-started processes, not HPA |
| Requests per second | not measured under load | n/a | n/a | do not load-test live host this envelope |
| SQLite `edge_fleet.db` | 45056 bytes, WAL, integrity ok | local disk | local disk | no RDS |
| Process | pid 14783 uvicorn `:8765` | same LaunchAgent | same | restart is remaining live gap, not capacity |

## Recommendations

1. Do **not** add CloudWatch-driven auto-scaling for this intent.
2. Keep fail-closed (NFR1) ahead of throughput.
3. If a later authorized window needs numbers, run k6/ab against a **non-live** or explicitly approved process — not pid 14783 under this envelope.
4. Worker capacity is operator-started OpenClaw/Hermes `edge_node` processes; MindRoom does not supervise them (`docs/edge-fleet.md`).

## Explicitly not done

- 6/12-month growth model
- Cost envelope at target scale
- Partitioning / read-replica design
