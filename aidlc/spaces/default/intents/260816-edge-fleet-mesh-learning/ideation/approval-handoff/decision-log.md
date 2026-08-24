# Decision Log — Ideation

Record of decisions taken during Ideation for this initiative.
Sources are the confirmed question files and the artifacts they produced.

## Intent Capture

| Decision | Choice | Source |
|----------|--------|--------|
| Problem | Documented-but-inert capability, treated as one problem rather than four isolated features | `intent-statement.md` |
| Customer | Internal only | Q2 |
| Original success | Four outcomes as one milestone, plus enabling the excluded learning agent | Q3, Q9 |
| Trigger | Competitive positioning | Q4 |
| Sole stakeholder and approver | Dwayne | Q5, Q6 |
| Communication | Workflow approval gates only | Q7 |
| Product boundary | The composed 26-stage plan, neither narrowed nor widened | Q8 |
| Hermes | Full local integration target | Q10, Q13 |
| OpenClaw | Local install is the handshake counterpart; surface resolved in Reverse Engineering | Q11, Q14 |
| Activation precondition | Explicit sign-off only; no extra checklist, soak, or rehearsal | Q12 |

Later correction: the excluded learning agent is the one named for the OpenClaw runtime, not `analyst`.
That correction is recorded in the feasibility artifacts and restated in `intent-statement.md`.

## Feasibility & Constraints

| Decision | Choice | Source |
|----------|--------|--------|
| "Production" | The local single-user install | Q1 |
| Worker reach | Tailscale only | Q2 |
| Authentication | Existing enrollment machinery; nothing new | Q3 |
| Regulatory | None | Q4 |
| Timeline | No deadline | Q5 |
| Organizational blockers | None | Q6 |
| Test floor | Existing suite must stay green; no new coverage percentage | Q7, Q17 |
| Learning promotion | Automated canary; human approval for stable install | Q16 |
| Risk appetite | High for the local development runtime | Q9 |
| External runtimes | Abstract behind an adapter | Q10 |
| Rollback | Flag flip plus written cleanup | Q11 |
| Activation-status documents | Neither is authoritative; this work establishes the real status | Q14 |
| Demo key | In scope: rotate and remove | Q15 |

Headline findings recorded in `feasibility-assessment.md`: Edge Fleet and learning promotion are feasible with defects; the handshake is unknown; AI-DLC runtime access is not assessable as stated.

## Scope Definition

| Decision | Choice | Source |
|----------|--------|--------|
| Milestone stance | One initiative, sequenced increments — supersedes the intent statement's all-or-nothing sentence | Q1 |
| First increment that counts | A real worker enrolls, takes a job, and finishes it | Q2 |
| AI-DLC runtime access | Dropped | Q3 |
| Handshake | Should Have if inspection finds a usable surface | Q4 |
| Learning pair | Live promotion is Must; excluded agent is Should, after the self-referential path is examined | Q5 |
| Sequencing | Risk-first | Q6 |
| Critical defects vs sign-off | Both are prerequisites of sign-off | Q7 |
| Mesh wiring | Size it in Reverse Engineering | Q8 |
| Hygiene leftovers | Unused allowlist, contradictory status docs, and unread mesh flag are in scope | Q9 |
| Extra Won't Haves | No general-purpose mesh routing; no turning development-tooling agents into chat agents | Q10 |
| Proto-unit slice | Hygiene first, then one proto-unit per remaining in-scope outcome | Q11 |

These decisions live in `scope-document.md` and `intent-backlog.md`.
The `constraint-register.md` was not reopened.

## Approval & Handoff

| Decision | Choice | Source |
|----------|--------|--------|
| Sole-stakeholder approval of the package | Yes | Q1 |
| Critical risks | Acknowledged; they block sign-off; High items accepted with recorded treatments | Q2 |
| Delivery resource | Single operator | Q3 |
| Skipped market research | Stay skipped | Q4 |
| Skipped mockups | Stay skipped | Q5 |
| Skipped team formation | AI-only delivery, human at the gates | Q6 |
| Go / no-go | Go to Inception | Q7 |
| First sign-off increment | Hygiene, then the live-worker path, then sign-off | Q8 |
| Stale stakeholder-map wording | Leave as historical record; scope document is current | Q9 |

## Assumptions & Open Questions

- Inherited and still open: local OpenClaw and Hermes inspectability; mesh-wiring size; excluded-agent self-reference.
- No new assumptions were added at this gate.
