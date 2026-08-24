# Incident-response questions - edge-fleet-mesh-learning

Standing approvals from `@dwayne:localhost` are in force. Answers are taken from NFR1-NFR6, skipped `nfr-design` / `infrastructure-design`, `operation/observability-setup/` (4.4), `operation/deployment-pipeline/rollback-runbook.md`, `docs/edge-fleet-rollback.md`, and this-run observe-only snapshot. No new human Q&A turn. This dispatch uses the 4.1-4.4 live-safety envelope: no live host restart, no SSM/Incident Manager, no AWS Backup, no paging a live on-call system.

## Q1 - Most likely failure modes

**Question:** What are the most likely failure modes?

**Answer:** Local fleet failure modes already named in 4.4 `alarms.md` A1-A8 and `anomaly-config.md` D1-D8, plus FR6 rollback. Not multi-AZ outages (`infrastructure-specification` absent).

| ID | Mode | Detection (4.4) | Tabletop response |
|----|------|-----------------|-------------------|
| FM1 | Process not healthy / C6 missing | A1, A2; dashboards Panel 1 | RB-DETECT; do **not** kickstart in this envelope |
| FM2 | Mounted but health unavailable | A3; status sentence `Edge fleet is mounted but health is unavailable.` | RB-DETECT; inspect SQLite; no restart |
| FM3 | Unexpected unmount | A8 / D3 | RB-DETECT; do not re-enable flags |
| FM4 | Allowlist deny the operator expected to succeed | A4; log `edge node is not on the enrollment allowlist` | Fail-closed is correct (NFR1); do not widen allowlist |
| FM5 | Tailnet refused | A5; `tailnet.refused` | Fail-closed is correct (FR1.3/FR1.4); do not bypass |
| FM6 | Unexpected enroll / lease / complete | logs `Edge fleet audit event` | RB-REVOKE (authorized later window) then FR6 rollback |
| FM7 | Enrollment key leak / allowlist too wide | operator suspicion | FR6.1 flag-off (`docs/edge-fleet-rollback.md`) - **not executed this run** |
| FM8 | Store integrity / WAL stuck | sqlite `integrity_check` | RB-DETECT; do not `rm` while pid holds the file |
| FM9 | Idle zero nodes mistaken for outage | A7 | **Not an incident.** Expected mounted-idle |

This-run: FM9 baseline. A1-A6, A8 not firing. No live worker.

## Q2 - Escalation paths and on-call rotations

**Question:** What are the escalation paths and on-call rotations?

**Answer:** There is no on-call rotation, no SNS, no Incident Manager, no PagerDuty. NFR6 is a local single-user install. Primary responder is the signed-in operator `@dwayne:localhost`. Secondary / VP Engineering rows from the generic operations guide do **not** apply. Escalation is: operator -> stop (do not auto-remediate) -> optional later authorized window for flag-off + restart. See `escalation-matrix.md`.

## Q3 - Automated remediation

**Question:** What automated remediation is possible?

**Answer:** None that this stage may enable. Canonical SSM Automation (ECS restart, RDS failover, Lambda redeploy, Secrets Manager rotate) contradicts NFR6 and the skipped `infrastructure-specification`. Circuit-breaker analogue already in product: fail-closed (NFR1) refuses enroll/heartbeat/lease/complete. Auto-restart via `launchctl kickstart` is **forbidden** in this envelope (would recycle pid 14783). No CloudWatch->Lambda restart loop. See `automated-remediation.md`.

## Q4 - Communication procedures

**Question:** What are the communication procedures during incidents?

**Answer:** Single operator. No status page, no customer comms, no Slack `#incident-*` channel required. Record in the intent artefacts (`operation/incident-response/`) and quote C6 `edge_fleet.status` from `GET /api/health` (`docs/edge-fleet.md`). External templates from the generic guide are out of scope. Matrix Lobby thread `$dmYc1X2PeNmUkIzAX8vh8EanGyk2c1h6nrA-9YSpcGc` is the standing-approval channel, not an incident bridge.

## Q5 - RTO / RPO targets

**Question:** What are the RTO/RPO targets?

**Answer:** Not multi-nines. Fail-closed is the SLO analogue (`construction/build-and-test/nfr-validation-matrix.md`; 4.4 `slo-config.md`). Approximate local targets:

| Surface | Analogue | How |
|---------|----------|-----|
| RTO (stop new enroll/lease) | one env edit + process restart (FR6.1) | `docs/edge-fleet-rollback.md` - **not executed this run** |
| RPO (fleet store) | last on-disk SQLite + WAL; optional copy under `~/.mindroom/backups/20260817T231200Z-pre-upgrade-2026.8.79/` | no AWS Backup; do not restore this run |
| Error budget | none | flag-off, not burn-rate |

No quarterly game-day was run. Tabletop only.

## Upstream (consumed; several absent by design)

Standing answers cite 4.4 `dashboards.md` and `alarms.md`. `reliability-design`, `security-design`, and `infrastructure-specification` are **absent** (`construction/nfr-design/` and `construction/infrastructure-design/` skipped). Reliability analogue = fail-closed (NFR1 / `nfr-validation-matrix.md` / 4.4 `slo-config.md`). Security analogue = allowlist deny, tailnet refuse, revoke (NFR1-NFR3). No AWS Incident Manager namespace.

## Assumptions & Open Questions

None that block 4.5 under the local-safety envelope. Live worker-over-Tailscale, live revoke, live flag-off+restart, and AWS Incident Manager remain remaining live gaps, not required to close 4.5.

## Positions

None.
