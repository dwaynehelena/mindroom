# Stakeholder Map — Edge Fleet / Mesh / Learning Activation

## Key Stakeholders and Interests

| Stakeholder | Interest | Scope of interest | Source |
|-------------|----------|-------------------|--------|
| Dwayne | Sole stakeholder for all four activation items — Edge Fleet production activation, AI-DLC runtime access, the OpenClaw gateway handshake, and the governed learning loop | All four items | [Q5] |
| MindRoom team (internal) | Beneficiary of the activated platform capability; this is internal platform/infrastructure work with no external customer or partner beneficiary | Whole initiative | [Q2] |

No other stakeholder role is recorded. The security-architecture document's
"do not activate" hold on Edge Fleet production resolves to the same single
approver rather than to a separate security stakeholder. [Q5]

## Decision-Makers vs. Influencers

| Role | Who | Authority | Source |
|------|-----|-----------|--------|
| Security sign-off for Edge Fleet production activation | Dwayne | Explicit sign-off is the sole precondition for `MINDROOM_EDGE_FLEET_ENABLED=true` in production; no additional checklist, staging soak, or rollback rehearsal gates it | [Q5] [Q12] |
| Scope and priority across the four activation items | Dwayne | Same person as the security sign-off — scope and priority calls are not separated from it | [Q6] |

No influencer distinct from the decision-maker is recorded. [Q6]

## Communication Requirements

| Requirement | Cadence | Source |
|-------------|---------|--------|
| Report at the workflow's normal approval gates | Per approval gate | [Q7] |

No reporting cadence or notification obligation exists beyond those gates. In
particular, no separate advance notice is required before the Edge Fleet
production flag is flipped — the explicit sign-off itself carries that. [Q7] [Q12]

## Assumptions & Open Questions

None.
