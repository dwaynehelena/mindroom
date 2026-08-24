# Automated remediation - edge-fleet-mesh-learning (4.5)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** incident-response (4.5)
**Envelope:** no Lambda, no EventBridge, no Step Functions, no auto-restart.

## Purpose

State what is **already** automatic in product versus what canonical 4.5 would wire in AWS. Consumes `alarms.md` (no SNS actions) and absent `reliability-design` / `infrastructure-specification`.

## Already automatic (do not add more in 4.5)

| Control | Behavior | NFR |
|---------|----------|-----|
| Missing / false enable flag | routers unmounted; new enroll/lease cannot succeed | NFR1 / FR6.1 |
| Missing or short enrollment key | unmount | NFR1 |
| Allowlist missing or node not listed | enroll denied (`edge node is not on the enrollment allowlist`) | NFR1 / FR1.5 |
| Tailnet check fail | refuse enroll/heartbeat/lease/complete (`tailnet.refused`); does not log-and-continue | NFR1 / FR1.3 / FR1.4 |
| Revoked node | subsequent ops refused | FR1.2 |
| No new credential type | HMAC enrollment + Ed25519 attestation unchanged | NFR3 |

These are **fail-closed circuit breakers**, not restart loops. They are the correct remediation for FM4/FM5.

## Explicitly not automated (would mutate live runtime)

| Pattern from generic guide | This intent |
|----------------------------|-------------|
| CloudWatch alarm -> Lambda restart ECS | No ECS. `launchctl kickstart` would recycle pid 14783 - **forbidden** this envelope |
| Auto Scaling on queue depth | No cluster. Do not start workers to drain `edge_job` |
| EventBridge -> Step Functions | No AWS |
| SSM Automation document as alarm action | No SSM (`which aws` not-found, not invoked) |
| Max 3 restarts in 10 minutes | Not implemented; would be a live restart loop |

## Operator-gated only (authorized later window)

- RB-REVOKE (`DELETE /api/edge-fleet-admin/nodes/{node_id}`)
- RB-ROLLBACK (`.env` flag-off + restart + store delete)
- Worker stop (operator PIDs)

None of these ran this dispatch.

## Skip evidence

`evidence-2026-08-20-local-safety/cloud-skip.txt`:

- `did-not-run: aws ssm create-document`
- `did-not-run: aws ssm start-automation-execution`
- `did-not-run: launchctl kickstart`
- `did-not-run: python -m mindroom.edge_node`

## This-run

No automated remediation fired. Health stayed HTTP 200 on pid 14783. Idle A7 is not a trigger.
