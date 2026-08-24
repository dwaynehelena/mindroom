# Performance validation - edge-fleet-mesh-learning (4.6)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Interpreter:** `/Users/dwayne/mindroom/.venv/bin/python`
**Host:** Darwin `MacBook-Pro-3.local`
**Envelope:** local-safety (same as 4.1-4.5)

## Verdict

**PASS** - local performance-validation is inventoried and documented against NFR6 / construction Performance-load (no quantitative latency NFR); k6/locust/ab against the live host are correctly skipped; CloudWatch/X-Ray skipped; live host was not restarted; live `.env` was not written; no workers started. One observe GET `/api/health` HTTP 200 C6 quoted (not a load test).

## Upstream contract

| Consume | Status | Coverage in this stage |
|---------|--------|------------------------|
| `performance-requirements` | **absent** (`construction/nfr-requirements/` missing) | Cited; stand-in construction `nfr-validation-matrix.md` Performance / load |
| `scalability-requirements` | **absent** | Cited; NFR6 local single-user |
| `performance-design` | **absent** (`construction/nfr-design/` missing) | Cited; no p99 invented (`load-test-plan.md`, `nfr-validation-matrix.md`) |
| `scalability-design` | **absent** | Cited; auto-scaling skip (`auto-scaling-validation.md`) |
| `dashboards` | present `operation/observability-setup/dashboards.md` | Panel 1 C6 observe; Panel 2 saturation (`load-test-plan.md`, `test-results.md`) |

Evidence of absence: `evidence-2026-08-20-local-safety/upstream-absent.txt`.

## Commands actually run (read-only)

```sh
launchctl print gui/501/chat.mindroom.local   # excerpt only; not kickstart
lsof -nP -iTCP:8765 -sTCP:LISTEN
ps -p 14783 -o pid,lstart,etime,command
pgrep -lf mindroom.edge_node                  # none (rc 1)
# GET http://127.0.0.1:8765/api/health        # urllib, HTTP 200, 42.16 ms one sample
sqlite3 URI mode=ro ... PRAGMA integrity_check
command -v k6 locust wrk vegeta hey ab aws kubectl gh
```

Not run:

```sh
launchctl kickstart -k gui/501/chat.mindroom.local
k6 run ...
locust -f ...
ab -n 1000 -c 10 http://127.0.0.1:8765/api/health
wrk -t ... http://127.0.0.1:8765/api/health
aws cloudwatch get-metric-statistics ...
aws xray ...
python -m mindroom.edge_node ...
gh workflow run ...
```

## Results

| Check | Expected | Observed | Result |
|-------|----------|----------|--------|
| HTTP health | 200 | 200 | PASS |
| C6 fragment | present | present | PASS |
| Quoted sentence | mounted with 0 healthy nodes | exact | PASS |
| Pid | 14783 unchanged | 14783, launchd runs=8 | PASS |
| Workers | none | `no-edge-node-workers` | PASS |
| SQLite integrity | ok | ok (wal) | PASS |
| `.env` mtime | 2026-08-10T17:27:48.696565+00:00 | same | PASS |
| Kickstart | skipped | skipped | PASS (envelope) |
| k6 / locust / wrk / vegeta / hey | skipped | not-found, not invoked | PASS (envelope) |
| ab against live host | skipped | `/usr/sbin/ab` present, not invoked | PASS (envelope) |
| CloudWatch / X-Ray | skipped | `aws` not-found, not invoked | PASS (envelope) |
| Invented p99/RPS target | none | none | PASS |

Health JSON (observe, 2026-08-20T20:51:50Z):

```json
{"status":"healthy","last_sync_time":"2026-08-20T20:51:30.462924+00:00","e2ee":{"decrypt_failures":0,"key_requests_sent":0,"notices_sent":0},"edge_fleet":{"enabled":true,"healthy_nodes":0,"status":"Edge fleet is mounted with 0 healthy nodes."}}
```

## Artefacts produced

Required by stage `produces`:

- `load-test-plan.md`
- `test-results.md` (load-test-results)
- `nfr-validation-matrix.md`
- `performance-validation-questions.md`

Plus `bottleneck-analysis.md`, `auto-scaling-validation.md`, `capacity-planning.md`, `memory.md`, `performance-validation.md`, `evidence-2026-08-20-local-safety/`.

Each of `load-test-plan.md`, `test-results.md`, `nfr-validation-matrix.md`, `performance-validation-questions.md` has >=2 H2 headings (required-sections floor). Upstream names `performance-requirements`, `scalability-requirements`, `performance-design`, `scalability-design`, `dashboards` appear in those files.

## Remaining live gaps (do not block 4.6)

- Live `launchctl kickstart` still skipped (would recycle host)
- Live k6/locust/ab load against pid 14783 not run
- Live worker-over-Tailscale not run (heartbeat/lease/complete traffic still empty)
- `edge_fleet_audit` still 0 (no revoke this window)
- Remote GitHub Actions not run
- Full `tests/` not re-collected (quality minor m2)
- No CloudWatch/X-Ray (NFR6)
- No quantitative latency/throughput NFR exists to close later without a new requirements change

## Next stage

**4.7 feedback-optimization** is in **Stages to Skip**. Do not start 4.7. Operation phase local-safety envelope is complete through 4.6. `aidlc-orchestrate.ts report` is conductor-owned; this agent does not advance the engine.
