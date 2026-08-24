# Deployment pipeline questions — edge-fleet-mesh-learning

Standing approvals from `@dwayne:localhost` are in force. Answers are taken from NFR6, Construction skip of infrastructure-design, `ci-config`, `quality-gates`, and the existing `cicd-pipeline`. No new human Q&A turn.

## Q1 — Deployment strategy

**Question:** What deployment strategy (blue/green, canary, rolling)?

**Answer:** Feature-flag activation on the local MindRoom process. Not blue/green, not canary traffic shifting, not rolling Kubernetes. Those need multi-instance infrastructure that this intent does not have (`infrastructure-specification` absent). Safety is fail-closed defaults + flag-off rollback.

## Q2 — Environment promotion gates

**Question:** What environment promotion gates (dev → staging → prod)?

**Answer:** There is no hosted staging/prod for this intent (NFR6). Promotion is: CI candidate (`cicd-pipeline` green per `quality-gates`) → merge `main` → optional **manual** local mount. Default production-shaped local install stays unmounted.

## Q3 — Approval workflows for production

**Question:** What approval workflows for production?

**Answer:** Ideation recorded Dwayne's explicit sign-off as the sole precondition for `MINDROOM_EDGE_FLEET_ENABLED=true`. Standing approvals close this stage's gate. The CD path still requires a local env edit; it does not auto-enable the flag and does not mutate live Tailscale/OpenClaw.

## Q4 — Rollback procedure

**Question:** What rollback procedure?

**Answer:** FR6.1 flag-off + FR6.2 written cleanup in `docs/edge-fleet-rollback.md`, restated in this stage's `rollback-runbook.md`.

## Q5 — Feature flag strategy

**Question:** What feature flag strategy (CloudWatch Evidently, AppConfig)?

**Answer:** Neither. Local env flags: `MINDROOM_EDGE_FLEET_ENABLED`, enrollment key, node allowlist, optional store path. CloudWatch Evidently / AppConfig would violate NFR6.

## Assumptions & Open Questions

None.

## Positions

None.
