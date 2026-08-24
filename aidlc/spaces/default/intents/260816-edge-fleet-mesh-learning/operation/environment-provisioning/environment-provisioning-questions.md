# Environment provisioning questions — edge-fleet-mesh-learning

Standing approvals from `@dwayne:localhost` are in force. Answers are taken from NFR6, Construction skip of infrastructure-design, and `cd-config`. No new human Q&A turn.

## Q1 — Environments provisioned per Infra Design?

**Question:** Are all environments provisioned per Infra Design?

**Answer:** There is no Infra Design (`infrastructure-specification` absent). The equivalent environment is already present: local launchd MindRoom + `{storage_root}/edge_fleet.db`. No additional env was created. AWS environments were **not** provisioned.

## Q2 — VPCs, subnets, security groups, NACLs?

**Question:** Are VPCs, subnets, security groups, NACLs correct?

**Answer:** Not applicable. No VPC was created. Network boundary for this intent is loopback HTTP for workers (`docs/edge-fleet.md`) plus existing Tailscale for reach (inventoried, not mutated).

## Q3 — Secrets in Secrets Manager / Parameter Store?

**Question:** Are secrets in Secrets Manager / Parameter Store correctly injected?

**Answer:** Neither service is used. Fleet secrets are local env keys resolved by `RuntimePaths` from process env then config-adjacent `.env`. 4.2 inspected that file read-only and did not inject or rotate keys.

## Q4 — Cross-account / cross-VPC connectivity?

**Question:** Is cross-account / cross-VPC connectivity validated?

**Answer:** Not applicable. Single local user, single process, existing SQLite. Tailscale is an already-installed local daemon; 4.2 did not change ACLs.

## Assumptions & Open Questions

None that block 4.2. Live process restart to pick up workspace C6 health belongs to 4.3 deployment-execution if that stage requires a mount.

## Positions

None.
