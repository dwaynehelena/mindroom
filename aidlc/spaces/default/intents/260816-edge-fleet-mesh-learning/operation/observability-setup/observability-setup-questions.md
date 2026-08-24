# Observability setup questions — edge-fleet-mesh-learning

Standing approvals from `@dwayne:localhost` are in force. Answers are taken from NFR4, NFR6, skipped `nfr-design` / `infrastructure-design`, `cd-config`, `environment-inventory`, and `deployment-execution` (4.3 local-safety). No new human Q&A turn. This dispatch uses the 4.1–4.3 live-safety envelope: no live host restart, no CloudWatch, no AWS/k8s monitoring.

## Q1 — Golden signals

**Question:** What are the golden signals to track (latency, traffic, errors, saturation)?

**Answer:** Map the four golden signals onto **local** surfaces that already exist. There is no quantitative latency/throughput NFR (`nfr-validation-matrix.md` performance section). CloudWatch metrics are out of scope (NFR6).

| Signal | Local SLI surface |
|--------|-------------------|
| Latency | Not a numeric NFR. Operator quote of `GET /api/health` round-trip and `last_sync_time` freshness. No p99 target. |
| Traffic | Count of fleet audit events in process logs (`Edge fleet audit event`) plus SQLite `edge_job` status counts. Live workers = 0 this run. |
| Errors | Health `edge_fleet.error` / status sentence `Edge fleet is mounted but health is unavailable.`; log events `tailnet.refused`, allowlist deny string `edge node is not on the enrollment allowlist`, `Edge fleet health probe failed`. |
| Saturation | `edge_fleet.healthy_nodes` vs operator expectation; SQLite file open by pid; launchd `state=running`. Zero healthy nodes with no workers is the expected mounted-idle state, not saturation. |

## Q2 — SLOs / SLIs

**Question:** What SLOs/SLIs are defined?

**Answer:** Fail-closed (NFR1) is the SLO analogue, not multi-nines availability (`nfr-validation-matrix.md`). Operator-visible SLIs are NFR4: mounted vs unmounted, allowlist deny, tailnet fail, revoke success. No 99.9% / error-budget CloudWatch SLO is defined or created.

## Q3 — Dashboard layouts

**Question:** What dashboard layouts does the team need?

**Answer:** Not a CloudWatch dashboard. Operator layout is: (1) quote `GET http://127.0.0.1:8765/api/health` `edge_fleet.status` (C6 / FR5.1 / `docs/edge-fleet.md`); (2) existing MindRoom web dashboard at `http://localhost:8765` (`docs/dashboard.md`) for config, not fleet golden signals; (3) launchd stderr/stdout plus `{storage_root}/logs/mindroom_*.log`. No `aws cloudwatch put-dashboard`.

## Q4 — Log retention and aggregation

**Question:** What log retention and aggregation rules apply?

**Answer:** Local files only. Process logs: `{storage_root}/logs/mindroom_<timestamp>.log` via `setup_logging` (`src/mindroom/logging_config.py`). LaunchAgent: `/Users/dwayne/Library/Logs/mindroom/{stdout,stderr}.log`. Durable revoke evidence: SQLite `edge_fleet_audit` (`node.revoked`). No CloudWatch Logs Insights, no 30/90-day AWS retention, no S3/Glacier archive. Do not rotate or truncate live logs in this stage.

## Q5 — Distributed tracing

**Question:** What distributed tracing instrumentation is needed?

**Answer:** None for this intent. Single local process (NFR6). Correlation is structlog fields (`event`, `service=edge-fleet`, `node_id`) plus Matrix `correlation_id` already in host logs. AWS X-Ray is skipped. Do not add a new tracer.

## Assumptions & Open Questions

None that block 4.4 under the local-safety envelope. Live worker-over-Tailscale (would produce heartbeat/lease/complete audit traffic) remains a remaining live gap, not required to close 4.4. CloudWatch/X-Ray remain skipped.

## Positions

None.
