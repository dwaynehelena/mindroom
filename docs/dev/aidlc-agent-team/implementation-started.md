# AI-DLC Agent-Team Upgrade: First Implementation Milestone

Status: **implemented and structurally verified; executable test pending; not promoted** (2026-07-25 UTC)

## Milestone

A non-live, schema-valid reference configuration now defines one central coordinating AI-DLC team and fourteen lifecycle specialists. The team contract follows the agent-guide pattern of explicit roles, centralized delegation, independent reviewers, bounded review iteration, and human-controlled consequential actions.

The reference is deliberately isolated under `docs/dev/aidlc-agent-team/`; it is not included by either repository `config.yaml` or the runtime `.mindroom/config.yaml`. Therefore this milestone does not provision Matrix users, reload MindRoom, deploy software, modify live configuration, delete data, or make external changes.

## Preserved control boundaries

- The coordinator owns delegation; members do not delegate to each other.
- Ordinary work selects a small relevant mob rather than invoking every specialist.
- Product and architecture reviewers are independent and cannot replace human approval.
- Deployment, live-configuration changes, destructive actions, infrastructure provisioning, and external changes require explicit human approval.
- Workflow changes proposed by the composer require approval before application.

## Stage grid for this approved implementation slice

| Stage | Decision | Evidence / rationale |
|---|---|---|
| Inception and scope | EXECUTE (inherited) | The user states implementation and scope were human-approved. |
| Requirements and design | EXECUTE (inherited) | Approved role/team boundaries are encoded as a testable reference contract. |
| Implementation | EXECUTE | `reference-config.yaml` created. |
| Local validation | EXECUTE | Contract tests parse the YAML through MindRoom's `Config` model and assert membership/control boundaries. |
| Security and compliance review | EXECUTE (bounded) | Approval restrictions are asserted; deeper independent review remains appropriate before promotion. |
| Deployment / live config promotion | SKIP | Explicitly prohibited in this turn and requires a separate approval gate. |
| Operations validation | SKIP | No runtime was changed, so a live smoke test would be misleading and would cross the promotion gate. |

## Promotion boundary

Promotion means reviewing a proposed merge of the reference definitions into the intended runtime config, then explicitly approving that live-config change and subsequent controlled reload. Promotion is not part of this milestone.