# Initiative Brief — Edge Fleet / Mesh / Learning Activation

One-page handoff from Ideation to Inception.
The current boundary is `scope-document.md`.
The `intent-statement.md` and `stakeholder-map.md` remain the historical record where they differ.

## Intent

MindRoom carries capability that is written and documented but never reaches a live consumer.
Closing that gap means activating paths that already exist, not adding new product surface.

The original intent named four outcomes as one milestone.
Feasibility found they are not equally reachable.
Scope relaxed the milestone: they remain one initiative and may ship as sequenced increments.

## Market validation

No `competitive-analysis` was produced.
Market research was skipped by the composed plan and reconfirmed as skip at this gate. [Q4]
The customer is internal.
The trigger is competitive positioning against the two external runtimes already installed locally, not a market-sizing case.

## Feasibility and risk

From `feasibility-assessment.md` and the RAID log:

- Edge Fleet activation is feasible once known defects are repaired.
- Live learning-loop promotion is feasible; both ends of the pipeline currently lack live callers.
- The OpenClaw handshake is unknown until the local install is inspected.
- "The AI-DLC agent team can queue and lease work at runtime" is not assessable as stated and was dropped from scope.

Critical risks, now prerequisites of activation sign-off: [Q2] [Q8]

- Administrative revocation cannot succeed in a real deployment.
- The documented Tailscale check is not enforced.

High items accepted with recorded treatments: leftover demo key, unused allowlist setting, dropped SM2, and handshake-hidden mesh-wiring scope.

The `constraint-register.md` still binds: local install only, Tailscale-only reach, existing authentication, no deadline, rollback is a flag flip plus written cleanup.

## Scope boundary

From `scope-document.md` and `intent-backlog.md`:

| Priority | In this initiative |
|----------|--------------------|
| Must | Activation hygiene; a real worker enrolls, takes a job, and finishes it; live learning-loop promotion; real activation-gate status; written rollback procedure |
| Should | OpenClaw handshake if inspection finds a usable surface; enabling the excluded agent after examining the self-referential path |
| Won't | Hosted platform; public exposure; new authentication; a third worker runtime; AI-DLC runtime access; general-purpose mesh routing; turning development-tooling agents into chat agents |

First increment that may request activation sign-off: hygiene, then the live-worker path. [Q8]
Hygiene alone is not enough.

No `wireframes` exist.
Rough mockups were skipped and reconfirmed as skip. [Q5]

## Team plan

No `team-assessment` exists.
Team formation was skipped.
Delivery is a single operator plus this workflow, with approval at each gate. [Q3] [Q6]

## Go / no-go

**Go.** [Q1] [Q7]

Proceed to Inception, starting at Reverse Engineering.
That inspection decides whether the handshake stays a Should Have or is deferred.

## Assumptions & Open Questions

- **Assumption** — the locally installed OpenClaw and Hermes are inspectable enough for Reverse Engineering to size the handshake.
- **Open question** — how large live mesh wiring is if the handshake stays in.
- **Open question** — whether enabling the excluded agent creates a self-referential learning path that should stay off.
