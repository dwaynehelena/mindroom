# Tracing config — edge-fleet-mesh-learning (4.4)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** observability-setup (4.4)
**Envelope:** no X-Ray, no new tracer SDK, no process restart to inject middleware.

## Purpose

Canonical 4.4 would enable AWS X-Ray on API Gateway / Lambda / ECS. This intent has none of those (`infrastructure-specification` absent, NFR6 local install). Tracing for the fleet is **correlation fields already in process logs**, not distributed traces.

## Upstream

`performance-design` / `infrastructure-specification` absent. Deployed application is local launchd `chat.mindroom.local` pid 14783 (`deployment-execution`).

## What exists (do not add more in 4.4)

| Mechanism | Where | Fleet use |
|-----------|-------|-----------|
| structlog context | `src/mindroom/logging_config.py` | `timestamp`, logger name, level; redaction of credentials |
| Fleet audit extras | `src/mindroom/api/edge_fleet.py` `_audit_log` | `event`, `service=edge-fleet`, `node_id`, `detail` |
| Health fragment | `GET /api/health` `edge_fleet` | C6 activation sentence; not a trace |
| Host Matrix correlation | existing `correlation_id` / `session_id` on agent logs | Host AI-DLC traffic, not worker hops |
| SQLite | `edge_fleet_audit`, `edge_job` | Durable revoke / job state |

There is **one** fleet process. Workers (`python -m mindroom.edge_node`) are operator-started and were **not** started. No cross-service hop to instrument.

## X-Ray / OpenTelemetry skip

Would require live runtime mutation (dependency, env, restart). **Not performed.**

- `did-not-run: aws xray`
- Did not set `AWS_XRAY_SDK` / OTEL exporter env
- Did not `launchctl kickstart` to load a tracer
- Did not write `~/.mindroom/.env`

Evidence: `evidence-2026-08-20-local-safety/cloud-skip.txt`, `observe.log` (`did-not-run: launchctl kickstart`, pid stayed 14783).

## Remaining live gap (not 4.4 work)

A later authorized worker-over-Tailscale run could correlate `node_id` across enroll → heartbeat → lease → complete using the existing `_audit_log` events. That still would not be X-Ray.
