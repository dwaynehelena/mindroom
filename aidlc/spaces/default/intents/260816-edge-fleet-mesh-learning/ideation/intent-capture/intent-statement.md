# Intent Statement — Edge Fleet / Mesh / Learning Activation

## Problem Statement

MindRoom carries capability surface that is written and documented but never
reaches a runtime consumer. The work exists as engineering deliverables; nothing
exercises it in a live path. [Q1]

The underlying problem is the gap between what the codebase claims — its docs,
docstrings, and positioning copy — and what actually runs. Closing that gap means
reducing "documented but inert" surface area rather than adding new capability. [Q1]

Four specific paths are inert today, and the initiative treats them as one
problem rather than four: [Q3]

- Edge Fleet production activation for real worker enrollments. [Q3]
- The AI-DLC agent team's runtime access to Edge Fleet queue/lease and Mesh Gateway routing. [Q3]
- The OpenClaw gateway enrollment handshake against `mesh/enrollment.py`'s extension point. [Q3]
- The governed learning loop's live promotion path into `openclaw_root`. [Q3]

## Target Customer

The customer is internal: this is platform and infrastructure work for the
MindRoom team itself. No external customer or partner is a beneficiary of this
initiative as scoped. [Q2]

## Success Metrics

Success is measured as a single activation milestone — all four outcomes
together, not independently shippable increments. [Q3]

| # | Measurable outcome | Source |
|---|--------------------|--------|
| SM1 | Edge Fleet accepts real `openclaw`- and `hermes`-runtime worker enrollments in production, with the security approval gate honored | [Q3] |
| SM2 | The AI-DLC agent team can queue and lease work through Edge Fleet, or route through Mesh Gateway, at runtime — not merely author the code | [Q3] |
| SM3 | A real OpenClaw gateway completes the enrollment handshake against `mesh/enrollment.py`'s currently-inert extension point | [Q3] |
| SM4 | The governed learning loop promotes a skill originating from a live agent run — not `scripts/learning_stable_demo.py` — into `openclaw_root` | [Q3] |
| SM5 | Every live agent participates in the learning loop: the one agent currently excluded is set to `learning: true` alongside the agents already carrying that flag | [Q9] |

SM5 is paired with wiring the runtime promotion pipeline (capture → review →
promote → install into `openclaw_root`/`hermes_root`), which is the actual gap
behind the learning-loop item; the `learning:` flag itself already exists as
intended on the other agents. [Q9]

> **Correction (recorded during Feasibility & Constraints).** SM5 originally
> named `analyst` as the excluded agent, following the premise of the question
> that produced this answer. A direct reading of the configuration found the
> excluded agent is the one named for the OpenClaw runtime, and the counts are
> sixteen participating and one excluded rather than seventeen of eighteen. SM5
> is restated above without the incorrect name; the corrected identity and counts
> are recorded in `../../feasibility/feasibility-assessment.md` and
> `../../feasibility/raid-log.md` (I-3).

## Initiative Trigger

The trigger is market pressure and competitive positioning — matching or
differentiating from OpenClaw and Hermes Agent. [Q4]

The activation gate for Edge Fleet production is a single explicit sign-off from
the named approver. No additional precondition — security review checklist,
staging soak period, or rollback rehearsal — applies before
`MINDROOM_EDGE_FLEET_ENABLED=true` ships to production; the security-architecture
document's threat model is otherwise treated as sufficient. [Q12]

## Initial Scope Signal

**Workflow-selected scope**: `edge-fleet-mesh-learning-activation` — a composed
26-of-33-stage plan covering full inception, construction, and operation, with
market research, team formation, mockups, user stories, and practices discovery
excluded. [scope]

**User-confirmed product boundary**: the workflow-selected scope above is
confirmed correct as composed — neither narrowed nor widened. [Q8]

### Runtime counterparts in scope

Both external runtimes named in Edge Fleet's `WorkerRuntime` literal have real
local installations, so neither is a placeholder for this initiative: [Q10] [Q11]

| Runtime | Local install | Role in this initiative | Source |
|---------|---------------|-------------------------|--------|
| `hermes` | Installed locally | Full integration target — Edge Fleet must accept a real `hermes`-runtime worker that enrolls, leases, and completes work against the local install | [Q10] [Q13] |
| `openclaw` | Installed locally | Enrollment handshake target; the specific integration surface (CLI command surface vs a local gateway service endpoint) is resolved during Reverse Engineering by inspecting the local install alongside `mesh/enrollment.py`'s existing extension points | [Q11] [Q14] |

## Assumptions & Open Questions

None.

## Review

**Reviewer:** aidlc-product-lead-agent
**Verdict:** NOT-READY

**Findings:**

- **[Critical]** `stakeholder-map.md`, "Key Stakeholders and Interests" table, self-contradicts. Row 1 states "Dwayne | Sole stakeholder for all four activation items" `[Q5]`, which is a direct, defensible restatement of Q5's confirmed answer ("Dwayne is the sole approver/stakeholder for all four items"). Row 2 then adds "MindRoom team (internal) | Beneficiary of the activated platform capability... | Whole initiative" `[Q2]`, listing a second stakeholder in the same table row 1 just declared to be "sole." Q2 answers a different question — "who is the customer" (internal only) — and never uses the word "stakeholder" or asserts the MindRoom team has interests, authority, or a stake distinct from Dwayne's. Promoting a customer-identity answer into a second stakeholder-table row directly contradicts the adjacent "sole stakeholder" claim and stretches Q2 beyond what it supports, in violation of the stage's grounding contract rule 4 ("Never turn an unselected option into an exclusion or requirement") and rule 2 (claim must be entailed by its tagged source). The table needs reconciling — either drop "sole" from row 1 or drop/re-scope the MindRoom-team row with a source that actually names it as a stakeholder.
- **[High]** `intent-statement.md` and `stakeholder-map.md` are saturated with implementation-level detail — file paths (`mesh/enrollment.py`), code identifiers (`WorkerRuntime` literal, `openclaw_root`, `hermes_root`), config flags (`learning: true`, `config.yaml`), env vars (`MINDROOM_EDGE_FLEET_ENABLED=true`), and script names (`scripts/learning_stable_demo.py`) — appearing in the Problem Statement, Success Metrics table, and Initial Scope Signal / Runtime counterparts table. This directly contradicts the applicable ideation-phase guardrail (`aidlc/spaces/default/memory/phases/ideation.md` § Scope Discipline: "No implementation details (architecture, tech stack, code) in ideation artifacts") and § Output Quality ("All ideation artifacts must be readable by non-technical stakeholders... Avoid jargon unless defined in a glossary" — no glossary is present). These identifiers are individually sourced to confirmed `[Q<n>]` answers so the grounding contract is satisfied, but the phase-level scope-discipline rule is not; this level of code detail belongs in reverse-engineering or requirements-analysis, not intent capture.
- **[Medium]** `intent-statement.md` § Initiative Trigger conflates two different things under one heading defined by the stage contract as "Why now": the actual trigger (market pressure / competitive positioning, `[Q4]`) and, in the second paragraph, Edge Fleet's production activation gate condition (`[Q12]` — "Dwayne's sign-off is the only precondition"). The activation-gate content is correctly sourced but answers a different question (what conditions gate production release) than the section it's filed under (why is this initiative happening now). This misfiling makes the artifact harder for a downstream stage or reviewer to locate scope/risk content.
- **[Low]** `intent-capture-questions.md` § Sources registers only `[scope]`, no `[desc]` entry. This is defensible per `aidlc-state.md`, whose `## Project Information` → `**Project**` field still holds the literal `[Project description]` placeholder — so no verbatim initial description was ever captured to source. However, Q1's question stem references "the system integration map you shared," implying a real initial description existed somewhere (session context or `$ARGUMENTS`) that was never registered as `[desc]`. If such a description exists, its framing (e.g., "four things... built but not wired together") is currently unsourced background feeding into the Problem Statement rather than a traceable `[desc]` claim — worth confirming this wasn't lost upstream rather than genuinely absent.
