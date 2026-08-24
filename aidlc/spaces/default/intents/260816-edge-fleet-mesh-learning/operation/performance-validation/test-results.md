# Load test results - edge-fleet-mesh-learning (4.6)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** performance-validation (4.6)
**Envelope:** local-safety. Results are observe-only plus skip-with-evidence. This file is the `load-test-results` artefact (`test-results.md` per stage outputs).

## Purpose

Record latency, throughput, and error-rate **actuals** against the plan. There is no quantitative NFR to pass or fail. Live load generation was not run.

## Upstream (consumed; several absent by design)

`performance-requirements`, `scalability-requirements`, `performance-design`, and `scalability-design` are **absent** (`construction/nfr-requirements/` and `construction/nfr-design/` skipped). Stand-in: `construction/build-and-test/nfr-validation-matrix.md` (no quantitative latency/throughput NFR) and 4.4 `dashboards.md` / `slo-config.md`. Dashboards present.

## What was executed

LT-OBSERVE only: one `urllib` `GET http://127.0.0.1:8765/api/health` at UTC `2026-08-20T20:51:50.926766+00:00`. Not a load test. Not a percentile.

| Metric | Observed |
|--------|----------|
| HTTP | 200 |
| Wall elapsed | 42.16 ms (single sample; **not** p50/p95/p99) |
| Top-level `status` | healthy |
| C6 `edge_fleet.enabled` | true |
| C6 `edge_fleet.healthy_nodes` | 0 |
| Quoted `edge_fleet.status` | `Edge fleet is mounted with 0 healthy nodes.` |
| Pid | 14783 unchanged; launchd `runs=8` |
| Workers | none (`no-edge-node-workers`, pgrep rc 1) |
| SQLite | integrity `ok`, journal `wal`, `edge_fleet_audit=0` |
| `.env` mtime | `2026-08-10T17:27:48.696565+00:00` (not written) |

Health JSON:

```json
{"status":"healthy","last_sync_time":"2026-08-20T20:51:30.462924+00:00","e2ee":{"decrypt_failures":0,"key_requests_sent":0,"notices_sent":0},"edge_fleet":{"enabled":true,"healthy_nodes":0,"status":"Edge fleet is mounted with 0 healthy nodes."}}
```

Evidence: `evidence-2026-08-20-local-safety/health.json`, `health.headers`, `observe.log`, `pid.txt`.

## What was skipped (with evidence)

| ID | Command class | Result |
|----|---------------|--------|
| LT-K6 | k6 scripted load | skipped; `which k6=not-found` |
| LT-LOCUST | locust concurrent users | skipped; `which locust=not-found` |
| LT-AB | `ab -n 1000 -c 10 http://127.0.0.1:8765/api/health` | skipped; `/usr/sbin/ab` present, **not invoked** |
| LT-WORKERS | `python -m mindroom.edge_node` | skipped |
| LT-CW | CloudWatch / X-Ray | skipped; `which aws=not-found` |

Skip evidence: `evidence-2026-08-20-local-safety/load-skip.txt`.

## Latency / throughput / errors under load

| Series | Target | Actual |
|--------|--------|--------|
| p50 / p95 / p99 latency | none defined | **not measured** (would require load against live host) |
| Requests per second | none defined | **not measured** |
| Error rate under load | none defined | **not measured**; single GET HTTP 200 |
| Worker enroll/heartbeat/lease/complete RPS | none defined | **not measured**; workers idle |

Do not treat 42.16 ms as a SLO actual.

## Bottleneck analysis (this run)

Not performed under load. Idle snapshot only: single uvicorn pid 14783 listening `*:8765`; SQLite WAL open; A7 idle. See `bottleneck-analysis.md`.

## Verdict on results

**PASS (local-safety)** — required execute path is documented skip of live load plus one observe GET. No invented percentiles. No live mutation.
