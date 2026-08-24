# Anomaly config — edge-fleet-mesh-learning (4.4)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** observability-setup (4.4)
**Envelope:** static local checks. No CloudWatch Anomaly Detection, no ML band.

## Purpose

Canonical 4.4 would enable CloudWatch anomaly detection on latency and error rate. There is no CW metric stream (`infrastructure-specification` absent). Anomalies here are **deviations from the documented idle-mounted baseline** on this host.

## Baseline (this-run, not a 2-week CW model)

Observed 2026-08-20T20:33:05Z on pid 14783 (same process as 4.3):

| Signal | Baseline |
|--------|----------|
| HTTP health | 200, `status=healthy` |
| C6 | `enabled=true`, `healthy_nodes=0`, sentence `Edge fleet is mounted with 0 healthy nodes.` |
| Workers | none |
| SQLite | integrity `ok`, journal `wal`, `edge_fleet_audit=0`, jobs `completed=6` (historical, mtime 2026-08-10) |
| `.env` mtime | `2026-08-10T17:27:48.696565+00:00` |
| launchd | `state=running`, `runs=8`, pid 14783 |

Zero healthy nodes **is the baseline**, not an anomaly, until an operator starts a worker.

## Static deviation rules (operator, not ML)

| ID | Deviation from baseline | Severity |
|----|-------------------------|----------|
| D1 | Health HTTP not 200 or `status` not `healthy` | P1 |
| D2 | `edge_fleet` key disappears while process still this pid | P1 (regression vs 4.3 C6) |
| D3 | Status sentence becomes the unmounted sentence without an intended flag-off | P1 |
| D4 | Status sentence becomes `Edge fleet is mounted but health is unavailable.` | P1 |
| D5 | `healthy_nodes` > 0 **without** a worker the operator started | P2 investigate |
| D6 | `edge_fleet_audit` gains `node.revoked` without operator revoke | P2 |
| D7 | `.env` mtime changes | P1 config drift (4.4 itself must not cause this) |
| D8 | pid changes | Process recycled (kickstart). Out of envelope; record, do not perform |

## CloudWatch Anomaly Detection skip

- No detection band, no 2-sigma model, no canary
- `did-not-run: aws cloudwatch put-metric-alarm` (anomaly detector)
- No CloudWatch Synthetics canary (would be a live scheduled probe fleet; not added)

Health GET in this stage is **operator observe**, same as 4.3, not a new synthetic monitor service.

## This-run result

No D1–D8 deviations versus the 4.3 local-safety snapshot except log file growth (expected). Pid **stayed 14783**. `.env` mtime **unchanged**.

Evidence: `evidence-2026-08-20-local-safety/observe.log`, `pid.txt`, `health.json`.
