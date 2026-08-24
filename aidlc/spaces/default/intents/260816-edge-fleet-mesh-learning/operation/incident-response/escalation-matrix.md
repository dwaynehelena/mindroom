# Escalation matrix - edge-fleet-mesh-learning (4.5)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** incident-response (4.5)
**Envelope:** local single-operator. No on-call rotation. No SNS. No PagerDuty. No Incident Manager contacts.

## Purpose

Record **who acts** at each local severity. The generic operations-agent matrix (on-call engineer -> team lead -> VP Engineering at 30/60 minutes) is **not** instantiated: NFR6 is a local single-user install, `infrastructure-specification` is absent, and 4.4 `alarms.md` already states there is no on-call rotation and no pages.

Consumes `alarms` (P1/P2/P3) and `dashboards` (who looks at Panel 1). `reliability-design` / `security-design` absent; NFR1 fail-closed is the automatic containment, not a human escalate-to-SRE path.

## Matrix (actual)

| Local sev | 4.4 alarms | Primary | After 15 min | After 60 min | Page? |
|-----------|------------|---------|--------------|--------------|-------|
| L1 | A1, A2, A3, A8 | `@dwayne:localhost` | still the operator; do **not** kickstart | consider authorized RB-ROLLBACK window | **No** |
| L2 | A4, A5, D5, D6 | `@dwayne:localhost` | read logs/SQLite (`log-queries.md`) | revoke or flag-off only if authorized | **No** |
| L3 | A6 info, A7 idle | `@dwayne:localhost` | none | none | **No** |
| L4 | docs drift | `@dwayne:localhost` | next sitting | - | **No** |

There is no secondary on-call. Unreachable-primary (10 minute) handover does not apply.

## What must not be created

- SNS topic subscription as alarm action (4.4 already skipped `put-metric-alarm`)
- Incident Manager contacts / engagement / voice channel
- Slack `#incident-*` requirement
- Customer status-page SLA (20 min / 1 hour from the generic guide)

Skip evidence: `evidence-2026-08-20-local-safety/cloud-skip.txt` (`did-not-run: aws sns publish`, `did-not-run: aws ssm-incidents start-incident`).

## Communication path

1. Operator quotes C6 from dashboards Panel 1.
2. Operator writes timeline under `operation/incident-response/`.
3. Standing human approvals live in Lobby thread `$dmYc1X2PeNmUkIzAX8vh8EanGyk2c1h6nrA-9YSpcGc` - not a page-out.

## This-run

No escalation. A7 idle. Operator did not page, did not SNS, did not start-incident.
