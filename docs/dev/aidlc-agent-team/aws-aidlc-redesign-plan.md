# AI-DLC Agent Team Redesign: AWS AI-DLC Infrastructure Integration Plan

**Status:** DRAFT — produced for human review. No infrastructure changes executed.
**Author:** AI-DLC Architect (`@mindroom_aidlc_architect:localhost`)
**Date:** 2026-08-03 UTC
**Approval required:** Human review and explicit approval before any Phase execution.

---

## 1. Executive Summary

This plan restructures the existing AI-DLC agent team (14 lifecycle specialists + 1 coordinating team) to operate around **AWS AI-DLC** infrastructure. The current reference configuration at `docs/dev/aidlc-agent-team/reference-config.yaml` defines agent roles but treats AWS as a single platform-engineering concern. This redesign maps every agent onto specific AWS AI-DLC capabilities, introduces a **Tailscale pre-flight directive** as a standing guardrail, and defines the configuration, tool-access, and deployment-target changes needed to promote the team from reference to live operation.

No infrastructure is provisioned, no live configuration is modified, and no external services are contacted in the production of this document.

---

## 2. Current State Assessment

### 2.1 Existing Reference Configuration

| Agent | Current Role | AWS AI-DLC Mapping (proposed) |
|---|---|---|
| `aidlc_product` | Product manager / business analyst | §3.1 |
| `aidlc_design` | UX designer | §3.2 |
| `aidlc_delivery` | Engineering delivery manager | §3.3 |
| `aidlc_architect` | Solutions architect | §3.4 |
| `aidlc_aws_platform` | AWS platform engineer (generic) | §3.5 — significantly expanded |
| `aidlc_compliance` | Compliance advisor | §3.6 |
| `aidlc_devsecops` | DevSecOps advisor | §3.7 |
| `aidlc_developer` | Software developer | §3.8 |
| `aidlc_quality` | Quality engineer | §3.9 |
| `aidlc_pipeline_deploy` | Release engineer | §3.10 |
| `aidlc_operations` | SRE / operations | §3.11 |
| `aidlc_product_lead` | Independent product reviewer | §3.12 |
| `aidlc_architecture_reviewer` | Independent architecture reviewer | §3.13 |
| `aidlc_composer` | Adaptive workflow composer | §3.14 |

### 2.2 Gaps Identified

1. **No Tailscale integration** — no agent, tool, or instruction references Tailscale. The standing directive ("all agents must consult Tailscale before any operations") has no enforcement mechanism.
2. **`aidlc_aws_platform` is too broad** — it covers IaC, provisioning, cost optimization, and infrastructure design as a single agent. AWS AI-DLC spans SageMaker Studio/Notebooks, Training Jobs, Processing Jobs, Endpoints, Model Registry, Pipelines, Feature Store, and cost governance — this needs decomposition or at minimum structured internal responsibilities.
3. **No AWS-specific tool definitions** — agents have no `tools:` arrays in the reference config. None can actually interact with AWS APIs, Tailscale, or CI/CD systems.
4. **No deployment-target specification** — the team has `rooms: [aidlc]` but no indication of where artifacts deploy (SageMaker endpoints, ECS services, Lambda functions, S3 buckets, etc.).
5. **No model assignment for AWS workloads** — all agents use `model: default`. AWS-heavy agents (developer, pipeline_deploy, operations) may need different models or context windows for large IaC/CloudFormation/SageMaker Pipeline definitions.
6. **No skills or knowledge bases** — agents have no AWS documentation, Well-Architected Framework, or Tailscale ACL reference material loaded.

---

## 3. Agent-to-AWS AI-DLC Capability Mapping

### 3.1 `aidlc_product` — Product → AWS AI-DLC Use-Case Scoping

**AWS AI-DLC focus:** Maps business requirements to SageMaker capabilities (Studio, JumpStart, Canvas, Model Registry). Determines whether a use case needs real-time endpoints, batch transform, or async inference. Defines model approval workflow gates in SageMaker Model Registry.

**Config changes:**
- Add instruction: "Classify each use case against SageMaker inference patterns (real-time, serverless, async, batch) and document the rationale."
- Add instruction: "Consult Tailscale ACL status before defining any data-source requirement that implies cross-network access."
- Add skill: `aws-well-architected-machine-learning-lens`

### 3.2 `aidlc_design` — Design → SageMaker Studio UX & Endpoint API Design

**AWS AI-DLC focus:** Designs the interaction surface for SageMaker Studio interfaces, endpoint API contracts, and human-in-the-loop approval UI. Ensures accessibility of any generated Studio extensions or custom apps.

**Config changes:**
- Add instruction: "When specifying UX for SageMaker Studio or Canvas integrations, document the endpoint API contract alongside the visual design."
- Add instruction: "Consult Tailscale before recommending any UX flow that requires direct user access to a VPC-only SageMaker Studio domain."

### 3.3 `aidlc_delivery` — Delivery → AWS AI-DLC Lifecycle Sequencing

**AWS AI-DLC focus:** Sequences the lifecycle phases against SageMaker Pipeline steps (data prep → training → validation → model registration → deployment). Manages mob composition for each pipeline stage.

**Config changes:**
- Add instruction: "Map lifecycle stages to SageMaker Pipeline step types and report the mapping in every delivery plan."
- Add instruction: "Before sequencing any stage that involves AWS resource access, verify Tailscale connectivity status and report it as a precondition."

### 3.4 `aidlc_architect` — Architect → AWS AI-DLC Architecture & NFRs

**AWS AI-DLC focus:** Owns the architecture decision records for SageMaker service selection, VPC configuration, endpoint auto-scaling, instance type selection, cost-performance tradeoffs, and multi-region considerations. Defines non-functional requirements (latency, throughput, cost per inference) against AWS service limits.

**Config changes:**
- Add instruction: "For every architecture proposal, document the SageMaker service selection, instance type rationale, VPC/networking topology, Tailscale subnet exposure, and NFR targets."
- Add instruction: "Consult Tailscale ACL policy before proposing any architecture that exposes SageMaker endpoints outside a private VPC."
- Add skill: `aws-well-architected-machine-learning-lens`
- Add tool: `tailscale_check` (pre-flight connectivity and ACL verification)

### 3.5 `aidlc_aws_platform` — AWS Platform → SageMaker Infrastructure Engineering (Expanded)

**AWS AI-DLC focus:** This agent's role is significantly expanded. It is the primary AWS infrastructure specialist and covers:

- **SageMaker Studio Domains & User Profiles** — domain creation, IAM execution roles, lifecycle configs, JupyterLab/CodeEditor app configurations.
- **SageMaker Training Jobs & Processing Jobs** — instance selection, distributed training configs, spot instance strategy, pipeline processing steps.
- **SageMaker Model Registry & Model Groups** — model package definitions, approval workflows, model card generation.
- **SageMaker Endpoints** — endpoint configuration, auto-scaling policies, blue/green deployment, shadow testing.
- **SageMaker Feature Store** — feature group definitions, online/offline storage, ingestion pipelines.
- **IaC** — CloudFormation / CDK / Terraform for all of the above.
- **Cost optimization** — Savings Plans, spot training, inference recommender, cost allocation tags.

**Config changes:**
- Expand role to: "AWS platform engineer responsible for SageMaker infrastructure design, IaC, Studio domain management, training/processing job configuration, model registry governance, endpoint lifecycle, Feature Store provisioning, and cost optimization."
- Add instructions:
  - "Produce IaC (CDK or Terraform) for all proposed SageMaker resources. Validate syntax locally."
  - "Always run `tailscale_check` before generating any provisioning plan that references VPC subnets, security groups, or Tailscale exit nodes."
  - "Include cost estimates using AWS pricing data for every SageMaker resource proposal."
- Add tools: `tailscale_check`, `file`, `shell`
- Add skill: `aws-sagemaker-infrastructure-reference`

### 3.6 `aidlc_compliance` — Compliance → AWS AI-DLC Data Governance & Model Audit

**AWS AI-DLC focus:** Ensures data classification maps to SageMaker data encryption (KMS), VPC endpoint policies, model card compliance, and Model Registry approval gate integrity. Verifies Tailscale ACL compliance for any cross-network data path.

**Config changes:**
- Add instruction: "For every data source, document KMS key policy, S3 bucket policy, VPC endpoint policy, and Tailscale ACL exposure."
- Add instruction: "Verify Tailscale ACL configuration before approving any data pipeline that crosses network boundaries."
- Add tool: `tailscale_check`

### 3.7 `aidlc_devsecops` — DevSecOps → AWS AI-DLC Security & Threat Modelling

**AWS AI-DLC focus:** Threat models SageMaker service-to-service communication, Studio user isolation, endpoint IAM roles, training job network isolation, and Tailscale tunnel security. Defines SAST/DAST scanning for SageMaker processing containers and custom inference images.

**Config changes:**
- Add instruction: "Threat-model every SageMaker resource before it enters the pipeline. Document attack surfaces: Studio domain access, endpoint exposure, training job IAM, ECR image provenance, Tailscale tunnel attack surface."
- Add instruction: "Run `tailscale_check` before any security assessment to verify current ACL state and node exposure."
- Add tools: `tailscale_check`, `shell`

### 3.8 `aidlc_developer` — Developer → SageMaker Pipeline & Inference Code

**AWS AI-DLC focus:** Implements SageMaker Pipeline step definitions (processing, training, evaluation, registration), custom inference handler code, container build definitions (Dockerfiles for custom SageMaker images), and Feature Store ingestion scripts.

**Config changes:**
- Add instruction: "Implement SageMaker Pipeline step code in the smallest verifiable unit. Prefer managed images over custom containers unless a dependency requires otherwise."
- Add instruction: "Run `tailscale_check` before any code that accesses AWS APIs, SageMaker Studio, or S3 data sources."
- Add tools: `file`, `shell`, `tailscale_check`

### 3.9 `aidlc_quality` — Quality → SageMaker Model Validation & Endpoint Testing

**AWS AI-DLC focus:** Designs test strategy for model quality metrics (F1, RMSE, bias), endpoint load testing, SageMaker Pipeline evaluation step thresholds, and Model Registry approval criteria. Validates processing job outputs and training job metrics.

**Config changes:**
- Add instruction: "Define quality gates as SageMaker Pipeline condition steps with explicit metric thresholds and model approval criteria."
- Add instruction: "Run `tailscale_check` before any validation that requires endpoint invocation or data access."
- Add tools: `shell`, `tailscale_check`

### 3.10 `aidlc_pipeline_deploy` — Pipeline Deploy → SageMaker CI/CD & Model Deployment

**AWS AI-DLC focus:** Designs CI/CD for SageMaker Pipelines (CodePipeline/CodeBuild integration with SageMaker), model deployment strategies (blue/green, canary, shadow), Model Registry promotion, and rollback procedures for endpoints.

**Config changes:**
- Add instruction: "Design CodePipeline integration with SageMaker Pipelines. Map each pipeline stage to a CodePipeline stage."
- Add instruction: "Document endpoint deployment strategy (blue/green, canary, shadow) with rollback runbook for every release."
- Add instruction: "Run `tailscale_check` before preparing any release artifact that references AWS deployment targets."
- Add tools: `shell`, `tailscale_check`

### 3.11 `aidlc_operations` — Operations → SageMaker Monitoring & SRE

**AWS AI-DLC focus:** Configures SageMaker Model Monitor (data quality, model quality, bias drift, feature attribution drift), CloudWatch alarms for endpoints, SLO definitions for inference latency/availability, and incident response runbooks. Feeds operational findings back to product.

**Config changes:**
- Add instruction: "Define SageMaker Model Monitoring baselines and CloudWatch alarm thresholds for every deployed endpoint."
- Add instruction: "Run `tailscale_check` before any operational access to live SageMaker endpoints, Studio domains, or CloudWatch dashboards."
- Add tools: `shell`, `tailscale_check`

### 3.12 `aidlc_product_lead` — Product Lead Reviewer → AI-DLC Product Quality Gate

**AWS AI-DLC focus:** Reviews product artifacts for completeness against SageMaker capability constraints (e.g., does the product spec correctly identify real-time vs. batch inference?), and verifies that data governance requirements map to AWS services.

**Config changes:**
- Add instruction: "Verify that product artifacts correctly reference SageMaker service capabilities and do not imply capabilities AWS does not provide."
- No additional tools — advisory role, no direct AWS access.

### 3.13 `aidlc_architecture_reviewer` — Architecture Reviewer → AI-DLC Architecture Quality Gate

**AWS AI-DLC focus:** Reviews architecture decisions for SageMaker service selection correctness, VPC isolation completeness, Tailscale exposure minimization, cost-performance alignment, and NFR feasibility.

**Config changes:**
- Add instruction: "Review every architecture proposal for SageMaker service-selection correctness, VPC isolation, Tailscale ACL exposure, IAM least-privilege, and cost-performance alignment."
- No additional tools — advisory role, no direct AWS access.

### 3.14 `aidlc_composer` — Composer → AWS AI-DLC Workflow Grid

**AWS AI-DLC focus:** Composes the EXECUTE/SKIP lifecycle grid with awareness of which SageMaker Pipeline steps are relevant. Skips unnecessary stages (e.g., skip Feature Store if using JumpStart pre-trained models).

**Config changes:**
- Add instruction: "Map the EXECUTE/SKIP grid to SageMaker Pipeline step types. Justify every skip against AWS AI-DLC capability requirements."
- No additional tools — orchestration role only.

---

## 4. Tailscale Pre-Flight Directive (Standing Guardrail)

### 4.1 Requirement

> All agents must consult Tailscale before any operations.

### 4.2 Enforcement Mechanism

A new tool `tailscale_check` is introduced. Agents with AWS access tools (`shell`, `file` for IaC, etc.) must include `tailscale_check` in their tool list and carry an instruction to invoke it before any operation that could touch AWS resources or network paths.

**`tailscale_check` tool specification (conceptual):**

| Field | Value |
|---|---|
| Name | `tailscale_check` |
| Purpose | Verify Tailscale connectivity, ACL status, and node reachability before performing operations |
| Inputs | `target` (optional: hostname or subnet to verify), `operation_type` (e.g., "provisioning", "deployment", "data_access", "monitoring") |
| Outputs | JSON: `{ "connected": bool, "acl_allows": bool, "node_visible": bool, "warnings": [str], "checked_at": ISO8601 }` |
| Failure behaviour | If `connected` is false or `acl_allows` is false, the agent must abort the operation and report the blocker. |

### 4.3 Instruction Pattern (applied to all operational agents)

```yaml
- "BEFORE any operation that touches AWS resources, SageMaker, S3, VPCs, or network paths, invoke `tailscale_check` with the target and operation_type. If the check fails (connected=false or acl_allows=false), abort the operation and report the blocker. Do not proceed on a failed check."
```

### 4.4 Implementation Note

The `tailscale_check` tool needs to be registered as a MindRoom tool plugin. This requires:
1. A Python tool plugin under `mindroom/src/` or `mindroom/plugins/` that wraps the Tailscale CLI (`tailscale status`, `tailscale ping`) or Tailscale API.
2. The plugin must be listed in `config.yaml` under `plugins:`.
3. Each operational agent must reference it in their `tools:` array.

**This tool does not exist yet and must be built in Phase 1.**

---

## 5. Implementation Plan — Phased

### Phase 0: Pre-Flight Validation (No changes, human review only)

**Objective:** Validate this plan and obtain human approval.

| Step | Action | Deliverable |
|---|---|---|
| 0.1 | Human reviews this document | Approval/rejection with feedback |
| 0.2 | Architecture Reviewer agent reviews the plan (if available in reference) | READY/NOT-READY with findings |
| 0.3 | Address review findings | Finalized plan |

**Gate:** Explicit human approval to proceed to Phase 1.

---

### Phase 1: Tool & Skill Infrastructure (No live config changes)

**Objective:** Build the tools and skills that AI-DLC agents need before they can be configured.

| Step | Action | Deliverable |
|---|---|---|
| 1.1 | Build `tailscale_check` tool plugin | Python plugin registered and unit-tested locally |
| 1.2 | Create `aws-well-architected-machine-learning-lens` skill | Markdown knowledge base file with AWS WA ML Lens checklist |
| 1.3 | Create `aws-sagemaker-infrastructure-reference` skill | Markdown reference for SageMaker service catalog, instance types, IaC patterns |
| 1.4 | Create `aws-ai-dlc-deployment-targets` reference doc | Document defining all deployment targets (see §6) |
| 1.5 | Validate all tools/skills locally | Tests pass, no live config touched |

**Gate:** Human approval to proceed to Phase 2.

---

### Phase 2: Reference Configuration Update (Non-live)

**Objective:** Update the reference configuration at `docs/dev/aidlc-agent-team/reference-config.yaml` with the full AWS AI-DLC mapping, Tailscale directives, tool assignments, model assignments, and skills.

| Step | Action | Deliverable |
|---|---|---|
| 2.1 | Update all 14 agent definitions per §3 mapping | Updated `reference-config.yaml` |
| 2.2 | Add `tailscale_check` to all operational agents' `tools:` arrays | Updated `reference-config.yaml` |
| 2.3 | Add skills references to relevant agents | Updated `reference-config.yaml` |
| 2.4 | Update team instructions to include Tailscale pre-flight mandate | Updated `reference-config.yaml` |
| 2.5 | Add model assignments for AWS-heavy agents (see §7) | Updated `reference-config.yaml` |
| 2.6 | Run schema validation against MindRoom's `Config` model | Validation passes |
| 2.7 | Architecture Reviewer reviews updated reference | READY/NOT-READY |

**Gate:** Human approval to proceed to Phase 3.

---

### Phase 3: Live Configuration Promotion (Controlled)

**Objective:** Merge the validated reference configuration into the runtime `config.yaml` and reload MindRoom.

| Step | Action | Deliverable |
|---|---|---|
| 3.1 | Create a backup of current `config.yaml` | Backup file |
| 3.2 | Merge AI-DLC agents, team, tools, skills, and model definitions into `config.yaml` | Updated `config.yaml` |
| 3.3 | Ensure `aidlc` room exists or is created | Room configuration |
| 3.4 | Reload MindRoom (controlled, human-observed) | MindRoom restart with new agents loaded |
| 3.5 | Smoke test: mention each AI-DLC agent in the `aidlc` room and verify response | Smoke test log |
| 3.6 | Verify `tailscale_check` tool is available to operational agents | Tool availability confirmation |

**Gate:** Human approval that smoke tests pass. Rollback to backup if any test fails.

---

### Phase 4: AWS Environment Baseline (Infrastructure — separate approval)

**Objective:** Establish the AWS-side baseline that the agents will operate against. **This phase requires a completely separate human approval and is out of scope for this document's execution.**

| Step | Action | Deliverable |
|---|---|---|
| 4.1 | Define AWS account structure and IAM roles for AI-DLC | Account/IAM plan |
| 4.2 | Provision Tailscale connectivity to AWS VPC(s) | Tailscale subnet router or exit node deployed |
| 4.3 | Provision SageMaker Studio domain with VPC-only mode | Studio domain IaC |
| 4.4 | Configure S3 buckets, KMS keys, and VPC endpoints | Storage/security IaC |
| 4.5 | Set up CodePipeline/CodeBuild for SageMaker Pipeline CI/CD | CI/CD IaC |
| 4.6 | Configure CloudWatch monitoring baseline | Monitoring IaC |

**Gate:** Completely separate approval. Not part of this plan's execution.

---

### Phase 5: Operational Validation

**Objective:** Validate that the live AI-DLC team can perform a minimal end-to-end lifecycle against the AWS baseline.

| Step | Action | Deliverable |
|---|---|---|
| 5.1 | Run a minimal SageMaker training job through the AI-DLC team (smallest possible example) | Training job ARN, logs |
| 5.2 | Deploy a test endpoint through the team (human-approved) | Endpoint ARN, test invocation |
| 5.3 | Validate SageMaker Model Monitor baseline creation | Monitoring schedule ARN |
| 5.4 | Validate Tailscale pre-flight enforcement (attempt operation with Tailscale down → should abort) | Enforcement evidence log |
| 5.5 | Feed operational findings back to product | Feedback artifact |

**Gate:** Human approval that operational validation is complete.

---

## 6. Deployment Targets

Each AI-DLC deployment target must be explicitly documented before agents can produce deployment plans.

| Target | AWS Service | Description | Tailscale Exposure |
|---|---|---|---|
| Training Jobs | SageMaker Training | Distributed or single-instance model training | VPC-private; Tailscale subnet router required |
| Processing Jobs | SageMaker Processing | Data preprocessing, feature engineering, model evaluation | VPC-private; Tailscale subnet router required |
| Real-time Endpoints | SageMaker Hosting | Synchronous inference endpoints with auto-scaling | VPC-private; Tailscale for management access only |
| Serverless Inference | SageMaker Serverless | On-demand inference without persistent endpoints | VPC-private; Tailscale for management access only |
| Async Inference | SageMaker Async | Queue-based async inference for large payloads | VPC-private; Tailscale for management access only |
| Batch Transform | SageMaker Batch Transform | Offline batch scoring | VPC-private; Tailscale subnet router required |
| Model Registry | SageMaker Model Registry | Versioned model packages with approval workflows | VPC-private; Tailscale for management access |
| Feature Store | SageMaker Feature Store | Online and offline feature storage | VPC-private; Tailscale subnet router required |
| Studio Domains | SageMaker Studio | Managed JupyterLab/CodeEditor development environments | VPC-private; Tailscale for developer access |
| CI/CD Pipelines | CodePipeline + CodeBuild + SageMaker Pipelines | End-to-end ML pipeline automation | Tailscale for deployment access |
| Model Monitoring | SageMaker Model Monitor + CloudWatch | Endpoint quality, bias, and drift monitoring | VPC-private; Tailscale for operations access |
| Artifact Storage | S3 + KMS | Model artifacts, training data, pipeline outputs | VPC endpoints; Tailscale subnet router required |
| Container Registry | ECR | Custom SageMaker training/inference images | VPC endpoints; Tailscale for push access |

---

## 7. Model Assignment Recommendations

Not all agents need the same model. AWS-heavy agents that process large IaC or SageMaker Pipeline definitions benefit from larger context windows.

| Agent | Current Model | Recommended Model | Rationale |
|---|---|---|---|
| `aidlc_product` | `default` | `sonnet` (or `default`) | Standard reasoning, moderate context |
| `aidlc_design` | `default` | `sonnet` | Standard reasoning |
| `aidlc_delivery` | `default` | `sonnet` | Standard reasoning, coordination |
| `aidlc_architect` | `default` | `opus` or `gpt56` | Deep reasoning for architecture decisions, NFR analysis |
| `aidlc_aws_platform` | `default` | `opus` or `gpt56` | Large IaC generation, complex SageMaker configs — needs large context |
| `aidlc_compliance` | `default` | `sonnet` | Standard reasoning, policy analysis |
| `aidlc_devsecops` | `default` | `sonnet` or `opus` | Threat modelling depth varies |
| `aidlc_developer` | `default` | `opus` or `gpt56` | Code generation, large file processing |
| `aidlc_quality` | `default` | `sonnet` | Standard reasoning, test generation |
| `aidlc_pipeline_deploy` | `default` | `sonnet` or `gpt56` | CI/CD pipeline definitions can be large |
| `aidlc_operations` | `default` | `sonnet` | Standard reasoning, runbook generation |
| `aidlc_product_lead` | `default` | `sonnet` | Review, standard reasoning |
| `aidlc_architecture_reviewer` | `default` | `opus` | Deep independent review |
| `aidlc_composer` | `default` | `sonnet` | Orchestration, standard reasoning |

**Note:** Final model selection depends on cost constraints and should be reviewed by the human approver. All models defined in the current `config.yaml` (sonnet, opus, gpt56, haiku, deepseek, etc.) are available.

---

## 8. Configuration Changes Summary

### 8.1 New Tools Required

| Tool | Type | Status | Phase |
|---|---|---|---|
| `tailscale_check` | Python plugin | Does not exist — must be built | Phase 1 |

### 8.2 New Skills Required

| Skill | Content | Phase |
|---|---|---|
| `aws-well-architected-machine-learning-lens` | ML Lens checklist, pillar alignment | Phase 1 |
| `aws-sagemaker-infrastructure-reference` | Service catalog, instance types, IaC patterns, endpoint configs | Phase 1 |

### 8.3 Agent Configuration Changes (Summary)

| Agent | Tools Added | Skills Added | Instructions Added | Model Changed |
|---|---|---|---|---|
| `aidlc_product` | — | `aws-well-architected-ml-lens` | 2 (AWS inference pattern, Tailscale) | Possibly → `sonnet` |
| `aidlc_design` | — | — | 2 (endpoint API contract, Tailscale) | — |
| `aidlc_delivery` | — | — | 2 (pipeline mapping, Tailscale) | — |
| `aidlc_architect` | `tailscale_check` | `aws-well-architected-ml-lens` | 2 (SageMaker NFRs, Tailscale ACL) | → `opus` or `gpt56` |
| `aidlc_aws_platform` | `tailscale_check`, `file`, `shell` | `aws-sagemaker-infra-reference` | 3 (IaC, cost, Tailscale) — role expanded | → `opus` or `gpt56` |
| `aidlc_compliance` | `tailscale_check` | — | 2 (KMS/VPC policy, Tailscale ACL) | — |
| `aidlc_devsecops` | `tailscale_check`, `shell` | — | 2 (threat model, Tailscale) | — |
| `aidlc_developer` | `tailscale_check`, `file`, `shell` | — | 2 (SageMaker Pipeline code, Tailscale) | → `opus` or `gpt56` |
| `aidlc_quality` | `tailscale_check`, `shell` | — | 2 (Pipeline condition gates, Tailscale) | — |
| `aidlc_pipeline_deploy` | `tailscale_check`, `shell` | — | 3 (CodePipeline, deployment strategy, Tailscale) | — |
| `aidlc_operations` | `tailscale_check`, `shell` | — | 2 (Model Monitor, Tailscale) | — |
| `aidlc_product_lead` | — | — | 1 (SageMaker capability verification) | — |
| `aidlc_architecture_reviewer` | — | — | 1 (AWS review scope) | → `opus` |
| `aidlc_composer` | — | — | 1 (pipeline step mapping) | — |

### 8.4 Team Configuration Changes

| Field | Current | Proposed |
|---|---|---|
| `instructions` | 7 instructions (delegation, mob, advisory, reviewers, iteration, approval, feedback loop) | 9 instructions — add Tailscale pre-flight mandate + AWS deployment target documentation requirement |
| `mode` | `coordinate` | `coordinate` (unchanged) |
| `rooms` | `[aidlc]` | `[aidlc]` (unchanged) |
| `agents` | 14 agents | 14 agents (unchanged — no new agents, no removals) |

---

## 9. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `tailscale_check` tool fails to enforce pre-flight | Medium | High | Build as a hard-gate tool that returns structured failure; agents instructed to abort on failure |
| Agent confuses advisory role with operational authority | Low | High | Instructions explicitly state "advisor" or "do not deploy/alter"; reviewers return READY/NOT-READY only |
| AWS costs incurred by agent-generated IaC | Medium | Medium | `aidlc_aws_platform` must include cost estimates; no IaC is executed without human approval |
| SageMaker service limits hit during testing | Low | Low | Phase 5 uses smallest instance types; `aidlc_aws_platform` documents limits |
| Model context window too small for large IaC | Medium | Medium | Model assignment recommendations in §7; `context_window` parameter available |
| Reference config promoted to live without validation | Low | Critical | Phased gates; Phase 2 includes schema validation; Phase 3 includes smoke tests |

---

## 10. Control Boundaries Preserved

The following boundaries from the existing reference configuration are preserved and strengthened:

1. **Human approval gate** — deployment, live-config changes, infrastructure provisioning, destructive actions, and external changes require explicit human approval.
2. **Coordinator-only delegation** — agents never delegate to each other; the team coordinator performs every delegation.
3. **Small mob execution** — ordinary work selects 3–5 relevant agents, not all 14.
4. **Advisory roles** — compliance and DevSecOps are advisory unless directly required.
5. **Independent reviewers** — product lead and architecture reviewer cannot replace the human gate; NOT-READY triggers at most 2 builder-review iterations.
6. **Tailscale pre-flight** — all operational agents must run `tailscale_check` before AWS operations; failure aborts.
7. **No autonomous infrastructure changes** — `aidlc_aws_platform` generates plans only; no provisioning without human approval.

---

## 11. Next Steps

1. **Human reviews this document** — provide approval, rejection, or modification requests.
2. If approved, Phase 1 begins: build `tailscale_check` plugin and skill knowledge bases.
3. Phase 2 updates the reference configuration.
4. Phase 3 promotes to live (with separate approval).
5. Phase 4 provisions AWS baseline (completely separate approval).
6. Phase 5 validates operationally.

---

*End of plan. No infrastructure was changed, no live configuration was modified, and no external services were contacted in the production of this document.*