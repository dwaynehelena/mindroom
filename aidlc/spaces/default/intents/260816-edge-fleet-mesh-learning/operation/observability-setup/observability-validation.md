# Observability validation — observability-setup (4.4)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Interpreter:** `/Users/dwayne/mindroom/.venv/bin/python`
**Host:** Darwin `MacBook-Pro-3.local`
**Envelope:** local-safety (same as 4.1–4.3)

## Verdict

**PASS** — local observability is inventoried and documented against NFR4/C6; CloudWatch/X-Ray/SNS are correctly skipped; live host was not restarted; live `.env` was not written; no workers started.

## Upstream contract

| Consume | Status | Coverage in this stage |
|---------|--------|------------------------|
| `performance-design` | **absent** (`construction/nfr-design/` missing) | Cited; no latency SLO invented (`slo-config.md`) |
| `security-design` | **absent** | Cited; allowlist/tailnet/revoke queries (`log-queries.md`, `alarms.md`) |
| `reliability-design` | **absent** | Cited; fail-closed SLO analogue from `nfr-validation-matrix.md` |
| `monitoring-design` | **absent** | Cited; this directory is the local monitoring design |
| `infrastructure-specification` | **absent** (`construction/infrastructure-design/` missing) | Cited; no AWS namespace |
| `deployment-execution` | present `operation/deployment-execution/` | Pid 14783 / C6 observation reused, not re-executed as a deploy |

## Commands actually run (read-only)

```sh
launchctl print gui/501/chat.mindroom.local   # excerpt only; not kickstart
lsof -nP -iTCP:8765 -sTCP:LISTEN
ps -p 14783 -o pid,lstart,etime,command
pgrep -lf mindroom.edge_node                  # none
# GET http://127.0.0.1:8765/api/health        # urllib, HTTP 200
sqlite3 URI mode=ro ... PRAGMA integrity_check
rg -n "Edge fleet enabled|Edge fleet routes mounted|Edge fleet database opened" \
  /Users/dwayne/.mindroom/mindroom_data/logs/mindroom_20260820_202141.log
which aws; which kubectl                      # not-found; binaries not invoked
```

Not run:

```sh
launchctl kickstart -k gui/501/chat.mindroom.local
aws cloudwatch put-dashboard ...
aws cloudwatch put-metric-alarm ...
aws logs put-query-definition ...
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
| CloudWatch | skipped | `aws` not-found, not invoked | PASS (envelope) |
| Mount logs | enabled / routes mounted / db opened | 3 lines at 20:21:42Z | PASS |

Health JSON (observe):

```json
{"status":"healthy","last_sync_time":"2026-08-20T20:32:45.111022+00:00","e2ee":{"decrypt_failures":0,"key_requests_sent":0,"notices_sent":0},"edge_fleet":{"enabled":true,"healthy_nodes":0,"status":"Edge fleet is mounted with 0 healthy nodes."}}
```

## Artefacts produced

Required by stage `produces`:

- `dashboards.md`
- `alarms.md`
- `slo-config.md`
- `log-queries.md`
- `tracing-config.md`
- `anomaly-config.md`
- `observability-setup-questions.md`

Plus `memory.md`, `observability-validation.md`, `evidence-2026-08-20-local-safety/`.

Each of `dashboards.md` … `anomaly-config.md` has ≥2 H2 headings (required-sections floor).

## Remaining live gaps (do not block 4.4)

- Live `launchctl kickstart` still skipped (would recycle host)
- Live worker-over-Tailscale not run (heartbeat/lease/complete audit still empty)
- `edge_fleet_audit` still 0 (no revoke this window)
- Remote GitHub Actions not run
- Full `tests/` not re-collected (quality minor m2)
- No CloudWatch/X-Ray (NFR6)

## Next stage

**4.5 incident-response** — `aidlc-operations-agent`. Do not start 4.5 in this dispatch.
