# Alarms — edge-fleet-mesh-learning (4.4)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** observability-setup (4.4)
**Envelope:** local operator checks. No SNS, no CloudWatch alarms, no pages.

## Purpose

Define **symptom** checks the operator can run on this host. Canonical CloudWatch alarms with SNS routing contradict NFR6 and the skipped `infrastructure-specification`.

## Upstream

Consumes `monitoring-design` / `reliability-design` / `security-design` / `performance-design` / `infrastructure-specification` as **absent** Construction NFR/infra design (skipped). Live symptoms come from `deployment-execution` health + NFR4 in `inception/requirements-analysis/requirements.md`.

## Severity (local single-operator)

There is no on-call rotation. Tiers are operator attention, not SNS.

| Tier | Meaning | Action |
|------|---------|--------|
| P1 | Fleet should be mounted but health is down or probe failed | Quote health; do **not** kickstart in this envelope; follow `docs/edge-fleet-rollback.md` only in an authorized later window |
| P2 | Fail-closed denied an op the operator expected to succeed | Read audit log + SQLite; do not widen allowlist in 4.4 |
| P3 | Idle / expected empty | No action |

## Alarm definitions (local checks, not CloudWatch)

### A1 — Process not healthy (P1)

- **Symptom:** `GET /api/health` not HTTP 200 or top-level `status` != `healthy`
- **This run:** HTTP 200, `status=healthy` — **not firing**

### A2 — C6 fragment missing when flags claim mount (P1)

- **Symptom:** live `.env` has enable keys (inventoried 4.2/4.3, not printed) but health JSON lacks `edge_fleet` (the 4.2 pid-35649 observation)
- **This run:** fragment **present** on pid 14783 — **not firing**
- **Note:** 4.3 skipped kickstart; 4.4 does not restart to “fix” anything

### A3 — Mounted but health unavailable (P1)

- **Symptom:** `edge_fleet.status` quotes `Edge fleet is mounted but health is unavailable.` or log `Edge fleet health probe failed`
- **This run:** mounted sentence with `healthy_nodes=0`, no `error` key — **not firing**

### A4 — Allowlist deny (P2 when unexpected)

- **Symptom:** enroll fails with `edge node is not on the enrollment allowlist` (`src/mindroom/edge_fleet.py`)
- **This run:** no enroll attempted — **not firing** (no live worker)

### A5 — Tailnet refused (P2 when unexpected)

- **Symptom:** log event `tailnet.refused` (`src/mindroom/api/edge_fleet.py` `_audit_log`)
- **This run:** no enroll/heartbeat/lease/complete — **not firing**

### A6 — Revoke succeeded (informational; NFR4)

- **Symptom / evidence:** SQLite `edge_fleet_audit.event = node.revoked` and/or log `admin.node_revoked`
- **This run:** `edge_fleet_audit` count **0** — no revoke this run (table empty is not an alarm)

### A7 — Zero healthy nodes (P3 idle)

- **Symptom:** `healthy_nodes=0` **and** `pgrep mindroom.edge_node` empty
- **This run:** both true. **Expected mounted-idle.** Do **not** page. Do **not** start workers (envelope).

### A8 — Unmounted in production window (P1 only if operator intended mount)

- **Symptom:** status sentence `Edge fleet is unmounted; production activation is not live.`
- **This run:** not this sentence — **not firing**

## What is not an alarm

- `healthy_nodes=0` without workers
- Empty `edge_fleet_audit` (no revoke yet)
- Historical jobs `completed=6` from before this intent’s 4.4 window (`edge_fleet.db` mtime 2026-08-10)
- Product dashboard agent counts (`docs/dashboard.md`)

## CloudWatch / SNS skip

Canonical stage prose would create metric alarms + SNS + escalation.

- `did-not-run: aws cloudwatch put-metric-alarm`
- No SNS topic, no Lambda remediation, no OpsCenter item
- `which aws` not-found, not invoked (`evidence-2026-08-20-local-safety/cloud-skip.txt`)

## Explicitly not done

- No live restart to clear a missing-fragment alarm (fragment is already present)
- No worker start to make `healthy_nodes>0`
- No `.env` write
