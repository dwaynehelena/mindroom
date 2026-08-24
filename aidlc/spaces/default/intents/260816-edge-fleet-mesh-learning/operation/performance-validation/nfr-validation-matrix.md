# NFR validation matrix - performance-validation (4.6)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** performance-validation (4.6)
**Envelope:** local-safety. This is the Operation 4.6 matrix (target vs actual under load). Construction quality already produced `construction/build-and-test/nfr-validation-matrix.md`; this file does not redo construction.

## Purpose

Compare NFR **performance/scalability** targets to actuals. Construction skipped `nfr-requirements` and `nfr-design`. There are **no** quantitative latency/throughput NFRs. Fail-closed (NFR1) remains the SLO analogue per 4.4 `slo-config.md`.

## Upstream (consumed; several absent by design)

| Artifact | Status | Coverage |
|----------|--------|----------|
| `performance-requirements` | **absent** `construction/nfr-requirements/` | Cited; stand-in `inception/requirements-analysis/requirements.md` NFR1-NFR6 |
| `scalability-requirements` | **absent** | Cited; NFR6 is the scale constraint (local single-user) |
| `performance-design` | **absent** `construction/nfr-design/` | Cited; no p99 design |
| `scalability-design` | **absent** | Cited; no ASG/HPA |
| `dashboards` | present `operation/observability-setup/dashboards.md` | Panel 1 C6 used as observe surface |

## Target vs actual

| ID | Target | Actual (this run) | Status | Evidence |
|----|--------|-------------------|--------|----------|
| PERF-LAT | None (quality: no quantitative latency NFR; do not invent REST p99 < 200 ms from the operations guide) | Single GET `/api/health` 42.16 ms wall; **not** a percentile | N/A — no target | `evidence-2026-08-20-local-safety/health.headers` |
| PERF-TPUT | None (no RPS / concurrent-user NFR) | Load generation skipped | N/A — no target | `load-skip.txt` |
| PERF-ERR | None under load | Single GET HTTP 200; error rate under load not measured | N/A — no target | `health.json` |
| SCALE-LOCAL | NFR6 local single-user install only | No cloud perf infra; no workers; pid 14783 local uvicorn | **PASS** | `observe.log`, `load-skip.txt` |
| NFR1 fail-closed analogue | Fail-closed is SLO analogue, not multi-nines | Idle mounted C6; no load that would bypass fail-closed | **PASS (analogue, not load)** | 4.4 `slo-config.md`; construction matrix |
| NFR4 observe | Operator can quote health | Quoted `Edge fleet is mounted with 0 healthy nodes.` | **PASS** | `health.json` |
| LIVE-SAFETY | Do not load-test live host, restart, start workers, write `.env`, AWS/k8s | All skipped | **PASS (envelope)** | `load-skip.txt`, `pid.txt` |

Construction NFR1-NFR6 rows are **not re-executed**. See `construction/build-and-test/nfr-validation-matrix.md`.

## Performance / load (copied constraint, not a new claim)

From construction matrix:

> No quantitative latency/throughput NFRs exist. Load tests (k6/locust) are **not required** at Minimal strategy for this local-install intent.

4.6 executed the documented skip, not a synthetic k6 run.

## Reliability snapshot

- Fail-closed remains the SLO analogue (NFR1).
- Auto-scaling: not applicable (`auto-scaling-validation.md`).
- Capacity: one process / one operator (`capacity-planning.md`).
