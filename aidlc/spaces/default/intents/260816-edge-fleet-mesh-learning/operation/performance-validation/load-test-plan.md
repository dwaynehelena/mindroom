# Load test plan - edge-fleet-mesh-learning (4.6)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** performance-validation (4.6)
**Envelope:** local-only / documented / dry-run. No k6/locust/ab against the live host. No worker start. No cloud perf infra.

## Purpose

Record how this brownfield local install would be performance-validated if quantitative NFRs and a production-like load environment existed, then execute only the observe-only path allowed by the 4.1-4.5 live-safety envelope.

Canonical stage Step 4 would design a load test, execute it against production-like environments, and analyze CloudWatch/X-Ray. That would load-test live pid 14783 on :8765, start workers, or provision cloud perf infra. **Not executed.** Quality already recorded: no quantitative latency/throughput NFR; k6/locust not required at Minimal strategy.

## Upstream (consumed; several absent by design)

| Artifact | Path / status | How this plan uses it |
|----------|---------------|------------------------|
| `performance-requirements` | **absent** (`construction/nfr-requirements/` skipped) | Stand-in: `inception/requirements-analysis/requirements.md` NFR1-NFR6 and `construction/build-and-test/nfr-validation-matrix.md` Performance / load section. No p50/p95/p99 target exists. |
| `scalability-requirements` | **absent** (same skip) | Stand-in: NFR6 local single-user install. No concurrent-user / RPS growth model. |
| `performance-design` | **absent** (`construction/nfr-design/` skipped) | Cited; no latency SLO invented. 4.4 `slo-config.md` already refused p99 200 ms. |
| `scalability-design` | **absent** (same skip) | Cited; no auto-scaling groups, no replica count, no queue consumers. |
| `dashboards` | present `operation/observability-setup/dashboards.md` | Panel 1 C6 health quote is the only operator latency/traffic surface. Panel 2 process/saturation. Do not invent CloudWatch widgets. |

Evidence of absence: `evidence-2026-08-20-local-safety/upstream-absent.txt`.

## Traffic patterns (documented, not generated)

| Pattern | This install | Load-test implication |
|---------|--------------|------------------------|
| Steady state | Mounted-idle: `healthy_nodes=0`, no `mindroom.edge_node` workers (4.4 A7 / 4.5 FM9) | Idle is success, not a throughput sample |
| Peak | One signed-in operator (`@dwayne:localhost`) using local HTTP | No peak RPS NFR |
| Burst | None specified | Do not synthesize burst against live :8765 |

Product traffic that would exist in an authorized later window (not this run): enroll / heartbeat / lease / complete over Tailscale from OpenClaw/Hermes workers. Envelope forbids starting those workers.

## Scenarios (plan only)

| ID | Scenario | Tool | Against | This run |
|----|----------|------|---------|----------|
| LT-OBSERVE | Single `GET /api/health` (C6) | urllib | `http://127.0.0.1:8765/api/health` | **executed** (one request) |
| LT-K6 | Scripted HTTP load (health + fleet routes) | k6 | live host | **skipped** (would load-test live host; `k6` not-found) |
| LT-LOCUST | Concurrent users hitting enroll/lease | locust | live host + workers | **skipped** |
| LT-AB | ApacheBench `-n/-c` against :8765 | `/usr/sbin/ab` (present) | live host | **skipped** (binary exists; not invoked) |
| LT-WORKERS | Start edge_node workers for heartbeat/lease load | `python -m mindroom.edge_node` | Tailscale | **skipped** |
| LT-CW | CloudWatch/X-Ray analysis | aws | cloud | **skipped** (NFR6; `aws` not-found) |

## Latency / throughput targets

None. `construction/build-and-test/nfr-validation-matrix.md`:

> No quantitative latency/throughput NFRs exist. Load tests (k6/locust) are **not required** at Minimal strategy for this local-install intent.

Guide defaults from `.claude/knowledge/aidlc-operations-agent/nfr-performance-guide.md` (REST p99 < 200 ms, etc.) are **not adopted**. Adopting them would invent a performance-requirement the construction skip and quality matrix explicitly do not have.

## Bottlenecks (hypotheses only; not measured under load)

Likely local bottlenecks if an authorized load window ever runs: single uvicorn process on :8765; SQLite WAL `edge_fleet.db` held by pid 14783; Tailscale path for node ops; fail-closed allowlist/tailnet checks (correct, not a perf bug). Not measured this run.

## Auto-scaling validation

Not applicable. NFR6 local single-user. `scalability-design` absent. No ECS/ASG/HPA. See `auto-scaling-validation.md`.

## Capacity planning

Current baseline = one process, one operator, mounted-idle. 6/12-month growth model does not apply. See `capacity-planning.md`.

## Explicitly not done

- Live host restart / `launchctl kickstart`
- Live `~/.mindroom/.env` write
- Starting OpenClaw/Hermes workers
- `ab` / k6 / locust / wrk / vegeta / hey against :8765
- AWS / kubectl / remote Actions / cloud perf infra
