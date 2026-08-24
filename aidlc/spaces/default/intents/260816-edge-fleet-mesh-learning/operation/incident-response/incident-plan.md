# Incident plan - edge-fleet-mesh-learning (4.5)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** incident-response (4.5)
**Envelope:** local tabletop. No AWS Incident Manager response plan. No live incident declared.

## Purpose

Define how this local install declares, runs, and closes a fleet incident. Canonical 4.5 integration with AWS Incident Manager is **skipped** (NFR6; `infrastructure-specification` absent). Consumes 4.4 `dashboards.md` / `alarms.md`. `reliability-design` and `security-design` are **absent** (`construction/nfr-design/` skipped); NFR1 fail-closed and NFR4 operator visibility from `inception/requirements-analysis/requirements.md` are the reliability/security plan.

## Severity (local, mapped from 4.4 alarms)

Generic SEV1-SEV4 (complete outage / 10% error rate / many users) does not apply: one operator, no paying users, no public fleet listener (NFR2, with the known 0.0.0.0 bind minor).

| Local sev | 4.4 tier | Meaning | Declare when |
|-----------|----------|---------|--------------|
| L1 | P1 | Fleet should be mounted but health is down, C6 missing, probe failed, or unexpected unmount | A1, A2, A3, A8 / D1-D4 |
| L2 | P2 | Fail-closed denied an op the operator expected, or unexpected node / revoke / `healthy_nodes>0` without a started worker | A4, A5, D5, D6 |
| L3 | P3 | Idle / expected empty / cosmetic | A6 informational, A7 idle |
| L4 | - | Docs/config drift noticed in artefacts only | next sitting |

This-run: **no incident**. L3/A7 idle confirmed by RB-DETECT.

## Detection

Source of truth: `GET /api/health` `edge_fleet` (C6 / FR5.1 / `docs/edge-fleet.md` / dashboards Panel 1). Secondary: process logs and SQLite `edge_fleet_audit` (`log-queries.md`). There is no CloudWatch alarm action, no SNS, no Incident Manager `start-incident`.

## Declaration (tabletop)

1. Run RB-DETECT.
2. If A7 only: **do not declare**. Record idle.
3. If A1-A3/A8: declare L1 in `operation/incident-response/` notes; do **not** auto-restart.
4. If A4/A5 unexpected: declare L2; fail-closed is the mitigation already in product (NFR1). Do not widen allowlist or bypass tailnet.
5. If unexpected worker traffic or key leak: declare L1/L2 and follow RB-ROLLBACK in an **authorized** window (out of this envelope).

## Roles

| Role | Who |
|------|-----|
| Incident commander | `@dwayne:localhost` (also the only responder) |
| Investigator | same |
| Communicator | same; write artefacts, quote C6 |
| Customer liaison | none (NFR6) |

No weekly on-call rotation. No secondary. See `escalation-matrix.md`.

## Communication

Internal: append timeline to this directory (or a later incident note). Structured update fields: **Status** (investigating / identified / mitigating / resolved), **Impact** (local fleet only), **Next step**, **ETA**. External/status-page: none. Do not speculate root cause in any shared channel until confirmed from health/logs/SQLite.

Standing Matrix thread `$dmYc1X2PeNmUkIzAX8vh8EanGyk2c1h6nrA-9YSpcGc` is **not** an incident bridge.

## Mitigation order (authorized window only)

1. Stop workers (RB-WORKER-STOP) if any.
2. Revoke specific node (RB-REVOKE) if identity is known and fleet should stay mounted.
3. Flag-off + restart + store cleanup (RB-ROLLBACK / `docs/edge-fleet-rollback.md`) if containment requires unmount.
4. Never: kickstart as first action; never: `rm` store while pid holds it; never: AWS failover.

This envelope stops after step 0 (detect). Steps 1-3 skipped with evidence.

## Resolution criteria

- L1 health: HTTP 200, `status=healthy`, C6 present, quoted sentence matches intent (mounted vs unmounted).
- L2 fail-closed: deny/refuse strings present; allowlist not widened.
- Rollback: unmounted sentence quoted; enroll/lease 404.
- Idle: A7 recorded, not "fixed" by starting a worker.

## Post-incident (blameless)

For a declared L1/L2, within 48 hours write a note covering timeline, impact (local only), root cause, contributing factors, what went well, what to improve, action items. Ask "what" and "how", not "who". **No postmortem this run** - no incident declared.

## AWS Incident Manager skip

- `did-not-run: aws ssm-incidents create-response-plan`
- `did-not-run: aws ssm-incidents start-incident`
- No replication set, no contacts, no escalation plan ARN

## This-run tabletop timeline

| UTC | Action | Result |
|-----|--------|--------|
| 2026-08-20T20:40:08Z | RB-DETECT collector | pid 14783, HTTP 200, C6 mounted 0 nodes |
| 2026-08-20T20:41:16Z | Persist evidence | `evidence-2026-08-20-local-safety/` |
| 2026-08-20T20:41:16Z | Declare? | **No.** A7 idle |
| 2026-08-20T20:41:16Z | RB-ROLLBACK / RB-REVOKE / DR restore | **Skipped** (envelope) |
