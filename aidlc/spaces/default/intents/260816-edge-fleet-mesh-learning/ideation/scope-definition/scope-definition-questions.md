# Scope Definition — Questions

## Sources

- [scope] Workflow-selected scope: `edge-fleet-mesh-learning-activation`.
- Intent statement (`ideation/intent-capture/intent-statement.md`): four outcomes treated as one activation milestone (SM1–SM5).
- Feasibility assessment (`ideation/feasibility/feasibility-assessment.md`): Edge Fleet activation and learning-loop promotion are feasible; AI-DLC runtime access is not assessable as stated; the OpenClaw handshake is unknown until the local install is inspected.
- Constraint register (`ideation/feasibility/constraint-register.md`): local single-user install only, Tailscale-only reachability, existing authentication, no deadline, rollback is a flag flip plus documented cleanup.

These questions set the in/out boundary and the first cut of priority.
They do not re-open decisions already recorded in the constraint register: the target is the local install, workers reach it over Tailscale, authentication is the existing enrollment machinery, there is no delivery deadline, and rollback is a flag flip plus a written cleanup procedure.

## Q1. The intent statement treats all four outcomes as one milestone. Feasibility found they are not equally reachable. How should that stance change?

The intent statement says success is "a single activation milestone — all four outcomes together, not independently shippable increments."
The feasibility assessment found Edge Fleet activation and learning-loop promotion are feasible with known defects, the OpenClaw handshake cannot be judged until the local install is inspected, and "the AI-DLC agent team can queue and lease work at runtime" names an actor that does not exist as a runtime entity.

- A. Keep the single milestone — nothing ships as done until all four outcomes land
- B. Relax it — the four remain one initiative, but they may ship as sequenced increments
- C. Split the initiative — keep only the feasible items in this workflow; park the rest
- D. Not yet defined
- X. Other (please specify)

[Answer]: B. Relax it — the four remain one initiative, but they may ship as sequenced increments
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q2. What is the minimum increment that still delivers the value of this initiative?

The problem the intent statement named is documented-but-inert capability: the work exists, nothing live uses it.
A minimum increment should close some of that gap in a way you would accept as real progress, not just more documentation.

- A. A real worker enrolls, takes a job, and finishes it on the local install
- B. Edge Fleet can be turned on safely: revocation works, the Tailscale boundary is enforced, enrollment is actually possible, and the leftover demo key is gone — even if no live worker has enrolled yet
- C. Any one of the four outcomes becoming live, whichever is ready first
- D. Nothing smaller than the original four-outcome milestone counts
- X. Other (please specify)

[Answer]: A. A real worker enrolls, takes a job, and finishes it on the local install
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q3. What should happen to "the AI-DLC agent team can queue and lease work at runtime"?

Feasibility flagged this as a blocking concern, not a missing wire.
The agents that run this workflow are development-tooling definitions.
They are not MindRoom chat-runtime agents, do not appear in MindRoom's agent configuration, and have no runtime identity that could enroll or lease work.
The outcome cannot be designed until it is restated, dropped, or explicitly deferred.

- A. Restate it now: the development tooling gets a client path into Edge Fleet (queue and lease from the workflow tools, not from chat agents)
- B. Restate it now: the AI-DLC agents become real MindRoom runtime agents that can enroll and take work
- C. Keep the outcome in scope, but defer the restatement to Requirements Analysis
- D. Drop it from this initiative
- X. Other (please specify)

[Answer]: D. Drop it from this initiative
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q4. Where does the OpenClaw enrollment handshake sit in this release?

The extension point exists and is deliberately inert.
There is no protocol, message schema, endpoint, or client — only prose.
Feasibility also found that reaching this outcome means wiring the mesh coordination layer into the running system, not only writing a handshake, because the mesh has no runtime caller and its default transport is an in-memory queue.

- A. Must Have — stay in this release; design it after Reverse Engineering inspects the local install
- B. Should Have — in this release if inspection finds a usable surface; otherwise defer
- C. Could Have — inspect and record the surface; implement only if it is cheap
- D. Won't Have this time — inspect only, do not implement
- X. Other (please specify)

[Answer]: B. Should Have — in this release if inspection finds a usable surface; otherwise defer
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q5. How should the two learning-loop outcomes be prioritized against each other?

SM4 is a live promotion: a skill that originated from a real agent run is installed through the existing canary-then-human-stable path.
SM5 is turning on learning for the one agent that is currently excluded — the agent named for the OpenClaw runtime.
Feasibility noted that enabling that agent may create a self-referential path (the excluded agent is named for the runtime the promotion pipeline installs into).

- A. Both are Must Have, and they ship together
- B. Live promotion is Must Have; enabling the excluded agent is Should Have, after the self-referential path is examined
- C. Enabling the excluded agent is Must Have; live promotion can follow
- D. Both are Should Have — the first increment does not depend on them
- X. Other (please specify)

[Answer]: B. Live promotion is Must Have; enabling the excluded agent is Should Have, after the self-referential path is examined
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q6. How should the work be sequenced?

There is no deadline (feasibility Q5).
Sequencing is therefore about what to prove first, not about a date.
The RAID log's two Critical items are that administrative revocation cannot succeed in a real deployment, and that the documented Tailscale check is not enforced anywhere in the fleet code.

- A. Risk-first — repair revocation and the Tailscale check before any activation is signed off
- B. Value-first — turn the capability on as soon as enrollment works; repair containment afterwards
- C. Dependency-first — configure admission (allowlist and enrollment key), then fleet activation, then handshake, then learning
- D. Parallel tracks — the four outcomes proceed independently
- X. Other (please specify)

[Answer]: A. Risk-first — repair revocation and the Tailscale check before any activation is signed off
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q7. Are the two Critical fleet defects prerequisites of the security sign-off, or work inside it?

The activation gate is a single explicit sign-off from the named approver.
Revocation is the primary way to eject a misbehaving worker once enrolled.
The chosen network posture is Tailscale-only, and that boundary is currently unenforced.
The RAID log left open whether these must be fixed before sign-off can reasonably be given.

- A. Both are prerequisites — do not give the activation sign-off until revocation works and the Tailscale check is real
- B. They are work items inside the Edge Fleet outcome — sign-off covers them together when that increment ships
- C. Revocation is a prerequisite; the Tailscale check can ship with or after activation
- D. Neither is a prerequisite — document the residual risk and sign off anyway
- X. Other (please specify)

[Answer]: A. Both are prerequisites — do not give the activation sign-off until revocation works and the Tailscale check is real
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q8. If the OpenClaw handshake stays in scope, how much mesh work comes with it?

Reaching a real handshake requires the mesh coordination layer to run as part of the live system, not only as a demo or stress-test script.
That is hidden scope roughly the size of the named handshake work.

- A. Mesh runtime wiring is in scope as part of the handshake outcome
- B. Handshake only if it can be done without making the mesh a first-class runtime service
- C. Mesh wiring is its own Should Have, sized separately from the handshake
- D. Decide after Reverse Engineering sizes the gap
- X. Other (please specify)

[Answer]: D. Decide after Reverse Engineering sizes the gap
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q9. Which of the remaining hygiene items belong in this initiative? (select all that apply)

These are already-observed problems, not new ideas.
The committed demo key was already confirmed in scope during feasibility.
This question is about the leftovers.

- A. The unused allowlist setting on the request-handling layer — remove it or wire it, and make the test assert effect rather than presence
- B. Correct the two documents that disagree about whether Edge Fleet activation is already approved
- C. Either wire or remove the mesh enrollment flag and configuration section that nothing reads
- D. None of these — record them and leave them for later
- X. Other (please specify)

[Answer]: A, B, C
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q10. What is explicitly out of this initiative, beyond the constraint register?

The constraint register already excludes the hosted platform, public network exposure, a new authentication scheme, and any third worker runtime.
This question is whether the Won't Have list needs to grow.

- A. That list is complete — do not add further exclusions
- B. Also exclude general-purpose mesh routing between MindRoom instances; only the enrollment handshake (if in scope) remains
- C. Also exclude making development-tooling agents into chat-runtime agents
- D. Both B and C
- X. Other (please specify)

[Answer]: D. Both B and C
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Q11. How should the first-cut proto-units be sliced?

Units Generation will refine this.
A first cut now keeps the backlog from treating four unequally feasible outcomes as four equal work items.

- A. One proto-unit per original outcome (four items)
- B. One proto-unit for fleet defects and activation, one for mesh and handshake, one for the learning loop, and one for whatever SM2 becomes
- C. One proto-unit for activation hygiene (revocation, Tailscale check, allowlist, demo key), then one proto-unit per remaining in-scope outcome
- D. Leave slicing entirely to Units Generation
- X. Other (please specify)

[Answer]: C. One proto-unit for activation hygiene (revocation, Tailscale check, allowlist, demo key), then one proto-unit per remaining in-scope outcome
**Mode:** chat
**Recorded:** 2026-08-16T10:12:55Z

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
