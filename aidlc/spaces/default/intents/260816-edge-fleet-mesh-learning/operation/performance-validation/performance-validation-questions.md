# Performance-validation questions - edge-fleet-mesh-learning

Standing approvals from `@dwayne:localhost` are in force. Answers are taken from NFR1-NFR6, skipped `nfr-requirements` / `nfr-design`, `construction/build-and-test/nfr-validation-matrix.md`, 4.4 `dashboards.md` / `slo-config.md`, and this-run observe-only snapshot. No new human Q&A turn. This dispatch uses the 4.1-4.5 live-safety envelope: no live host restart, no load-test of pid 14783, no workers, no AWS/k8s perf infra.

## Q1 - Expected traffic patterns

**Question:** What are the expected traffic patterns (steady state, peak, burst)?

**Answer:** Steady state is **mounted-idle**: `GET /api/health` C6 `enabled=true`, `healthy_nodes=0`, no `mindroom.edge_node` workers (4.4 dashboards Panel 1 / alarms A7; 4.5 FM9). Peak is one local operator. Burst is unspecified. There is no production-like multi-user pattern. Worker enroll/heartbeat/lease/complete over Tailscale is a remaining live gap, not generated here.

## Q2 - Target latency percentiles

**Question:** What are the target latency percentiles (p50, p95, p99)?

**Answer:** None. `performance-requirements` and `performance-design` are absent. Quality matrix: no quantitative latency NFR. 4.4 `slo-config.md` explicitly lists p99 200 ms as **not** an SLO. Guide table REST p99 < 200 ms is not adopted. One observe GET measured 42.16 ms wall; that is not a percentile and is not a target.

## Q3 - Throughput that must be sustained

**Question:** What throughput must the system sustain?

**Answer:** None specified. NFR6 is local single-user. `scalability-requirements` absent. Do not invent RPS. k6/locust/ab against live `:8765` would be the measurement method and is **forbidden** in this envelope (`load-skip.txt`). `/usr/sbin/ab` exists and was not invoked.

## Q4 - Likely bottlenecks

**Question:** Where are the likely bottlenecks?

**Answer:** Hypotheses only (not load-measured): single uvicorn pid 14783 on `:8765`; SQLite WAL `edge_fleet.db`; Tailscale for node ops; fail-closed allowlist/tailnet (correctness, not a defect). `dashboards.md` Panel 2 is the saturation surface (launchd, lsof, store open, worker pgrep). CloudWatch saturation widgets are out of scope (NFR6).

## Upstream (consumed; several absent by design)

Standing answers cite 4.4 `dashboards.md`. `performance-requirements`, `scalability-requirements`, `performance-design`, and `scalability-design` are **absent** (`construction/nfr-requirements/` and `construction/nfr-design/` skipped). Performance stand-in = construction `nfr-validation-matrix.md` Performance / load section. Scalability stand-in = NFR6.

## Assumptions & Open Questions

None that block 4.6 under the local-safety envelope. Authorized later-window k6/ab against a **non-live** or explicitly approved host, and live worker-over-Tailscale, remain remaining live gaps, not required to close 4.6.

## Positions

None.
