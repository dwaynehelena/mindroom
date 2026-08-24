# Constraint Register — Edge Fleet / Mesh / Learning Activation

Constraints are non-negotiable boundaries on this work. Each row records where the
constraint comes from and what it forecloses. Sources are the confirmed answers in
`feasibility-questions.md`, the outcomes recorded in `intent-statement.md`, and
direct observation of the current codebase during the feasibility scan.

## Technical Constraints

| ID | Constraint | Source | What it forecloses |
|----|-----------|--------|--------------------|
| TC-1 | Activation targets the local single-user install only; the hosted platform is out of scope for this initiative | Q1 | No multi-tenant, per-instance, or Kubernetes deployment work; no hosted rollout planning |
| TC-2 | Worker reachability is Tailscale-only; no public network exposure | Q2 | No public endpoint, no internet-facing listener, no external DNS or certificate work |
| TC-3 | The existing enrollment-token and per-request attestation machinery is the authentication model; nothing new is introduced | Q3 | No new credential type, no alternative worker identity scheme, no bypass path for convenience |
| TC-4 | The worker runtime identifier admits exactly two values, and the mesh layer narrows that to one | Codebase scan | No third runtime can be introduced without changing four independent enforcement points, including database constraints |
| TC-5 | Enrollment admission is fail-closed against an allowlist; with no allowlist configured, every enrollment is denied | Codebase scan | Activation cannot be achieved by flag alone — allowlist configuration is mandatory, not optional |
| TC-6 | Four conditions gate the feature: the flag, the presence of an enrollment key, its validity, and its minimum length. All are evaluated once at startup | Codebase scan | No runtime toggling; any change to activation state requires a restart |
| TC-7 | Both external runtimes must be reached through an adapter that contains version and vendor change | Q10 | No direct calls into the third-party surface scattered through the codebase |
| TC-8 | The mesh message transport defaults to an in-memory queue when no real client is supplied | Codebase scan | Any outcome depending on real mesh delivery requires wiring a real transport first; the demo path proves nothing about live behaviour |

## Process Constraints

| ID | Constraint | Source | What it forecloses |
|----|-----------|--------|--------------------|
| PC-1 | Promotion is automated through the canary stage; the stable install into a runtime root requires human approval | Q16 | No fully unattended path from a live agent run to an installed skill |
| PC-2 | The existing test suite must remain green; no new coverage percentage floor applies | Q7, Q17 | No coverage gate blocking merge; equally, no licence to break existing tests |
| PC-3 | Rollback is a flag flip plus a documented cleanup procedure covering enrolled workers, leases, and persisted records | Q11 | Activation cannot ship without that written procedure; the flag alone is not accepted as rollback |
| PC-4 | Edge Fleet production activation requires explicit sign-off from the named approver | `intent-statement.md` | No activation on the strength of a passing test suite or a completed design review |
| PC-5 | The real activation gate status must be established by this work; neither existing status document is authoritative | Q14 | No stage may cite either document as evidence that activation is or is not approved |
| PC-6 | Trunk-based development with squash merges to `main` | Org practices | No long-lived branches for this initiative |

## Organizational Constraints

| ID | Constraint | Source | What it forecloses |
|----|-----------|--------|--------------------|
| OC-1 | No delivery deadline; correctness is preferred over speed | Q5 | No justification for cutting scope or rigour on schedule grounds |
| OC-2 | No change freeze, no competing priority, and no approval chain beyond the recorded sign-off | Q6 | No external scheduling dependency to plan around |
| OC-3 | A single person is both approver and scope decision-maker | `intent-statement.md` | No committee review, no separate security stakeholder to route through |
| OC-4 | High tolerance for disruption to the local development runtime | Q9 | Does not extend to the activation itself — PC-3 and PC-4 still bind |

## Regulatory Constraints

| ID | Constraint | Source | What it forecloses |
|----|-----------|--------|--------------------|
| RC-1 | No regulatory framework applies: internal system, no regulated data, no external data subjects, no audit obligation | Q4 | No compliance control matrix, no privacy impact assessment, no data residency analysis, no retention policy work |

RC-1 is the whole of the regulatory picture and is deliberately short. It was
tested rather than assumed: worker enrollment moves work across a machine
boundary and the learning loop captures agent behaviour and writes it into
external runtimes, both of which would ordinarily raise data-handling questions.
Neither does here, because the system is single-user, internal, and processes no
personal data belonging to anyone else.

**Conservative note**: RC-1 holds only while TC-1 holds. If activation ever
targets the hosted platform, the regulatory picture must be reassessed from
scratch rather than inherited from this register.

## Assumptions & Open Questions

- **Assumption** — the constraint set is complete for the local-install target.
  It was derived from the confirmed answers and a single codebase scan, not from
  an exhaustive audit of every configuration surface.
