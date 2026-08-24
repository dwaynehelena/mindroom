# Requirements — Edge Fleet / Mesh / Learning Activation

## Sources

- `intent-statement.md` — original problem and SM1–SM5
- `scope-document.md` — current in/out boundary and sequenced increments
- `business-overview.md`, `architecture.md`, and `code-structure.md` in the MindRoom knowledge base
- `requirements-analysis-questions.md` — confirmed answers Q1–Q11
- No `team-practices` artifact; practices discovery was skipped

## Intent Analysis

The operator of the local MindRoom install should be able to use capability that already exists as code and documentation.
Today those paths are default-off, fail-closed, or unwired.
This initiative closes that gap in sequenced increments, not as one four-way milestone.

Value is a live path: a real worker enrolls, takes a job, and finishes it, after containment works and the named sign-off is given.
A later increment promotes a skill that originated from a live agent reply.
The OpenClaw handshake remains a Should Have if the local install exposes a usable surface.

## Functional Requirements

### FR1 Containment hygiene

These must be true before activation sign-off is requested.

- **FR1.1** The signed-in operator on the local install shall revoke an enrolled worker without needing a separate permission claim. [Q1]
- **FR1.2** After a successful revoke, that worker shall be refused on enroll, heartbeat, lease, and complete.
- **FR1.3** Every fleet enroll, heartbeat, lease, and complete shall confirm tailnet connectivity first and refuse the operation when the check fails. [Q2]
- **FR1.4** A failing Tailscale helper shall fail the operation, not log and continue.
- **FR1.5** Enrollment shall remain fail-closed unless an allowlist is configured and the worker identity is on it.
- **FR1.6** The unused allowlist parameter on the HTTP layer shall be removed, and tests shall assert the store effect rather than the parameter’s presence. [Q10]
- **FR1.7** The leftover demo administrative key shall be rotated and removed from the repository.

### FR2 Live worker increment

This is the first increment that counts.

- **FR2.1** A documented operator command shall start a worker against the local install. [Q4]
- **FR2.2** An OpenClaw-runtime worker started that way shall enroll, take a job, and finish it. [Q3]
- **FR2.3** A Hermes-runtime worker started that way shall enroll, take a job, and finish it in the same increment. [Q3]
- **FR2.4** The increment is not done if only one of the two admitted runtimes has completed that path.
- **FR2.5** MindRoom shall not be required to start or supervise the worker process.

### FR3 Live learning-loop promotion

- **FR3.1** After a successful visible agent reply, the runtime shall record the evidence the capture step requires and offer a promotion candidate. [Q6]
- **FR3.2** A candidate shall not be created from a demo script alone.
- **FR3.3** Canary promotion shall install to a side location in both the OpenClaw root and the Hermes root. [Q7]
- **FR3.4** Stable promotion shall require a named human reviewer and reason, and shall install into both runtime roots. [Q7]
- **FR3.5** Agno preference-learning for the agent named for the OpenClaw runtime shall stay off. [Q8]
- **FR3.6** FR3.5 is the examination of the self-referential path: that agent is not turned on by this initiative.

### FR4 OpenClaw enrollment handshake

Should Have. Implement only if local-install inspection finds a usable surface. [Q5]

- **FR4.1** Reverse Engineering of the MindRoom extension point is not sufficient; the locally installed OpenClaw must be inspected.
- **FR4.2** If that inspection finds a usable surface, a real OpenClaw gateway shall complete the enrollment handshake against the existing extension point.
- **FR4.3** If that inspection finds no usable surface, the handshake shall be deferred and FR4.2 shall not be implemented.
- **FR4.4** If the handshake stays in, the unread mesh enrollment switch and configuration shall become the start path for that increment. [Q9]
- **FR4.5** If the handshake is deferred or dropped, that unread switch and configuration shall be removed. [Q9]

### FR5 Activation status

- **FR5.1** This work shall write a single activation-status statement that replaces the two disagreeing sentences now in the portfolio register and the security-architecture document. [Q11]
- **FR5.2** Neither old sentence shall remain as an authoritative claim.

### FR6 Rollback

- **FR6.1** Turning the fleet capability off shall stop new enrollments and new leases.
- **FR6.2** A written procedure shall tell the operator how to clean up enrolled workers, leases, and persisted records after the capability is turned off.

## Non-Functional Requirements

- **NFR1** Fleet operations shall be fail-closed: missing flag, missing or short enrollment key, missing allowlist, failed tailnet check, or revoked worker shall produce no successful enroll, heartbeat, lease, or complete.
- **NFR2** No fleet or handshake listener shall be exposed on a public network.
- **NFR3** Worker identity shall continue to use the existing enrollment-token and per-request attestation machinery; this initiative shall not add a new credential type.
- **NFR4** An operator shall be able to tell, from logs or health, whether the fleet is mounted, whether enroll was denied by allowlist, whether a tailnet check failed, and whether a revoke succeeded.
- **NFR5** The existing test suite shall remain green; no new coverage-percentage floor applies.
- **NFR6** Activation targets the local single-user install only.

## Constraints

- Local install only; hosted platform is out of scope. (`scope-document.md`, constraint register)
- Tailscale-only reach; no public exposure.
- Existing authentication; no new worker-identity scheme.
- Two admitted worker runtimes only.
- No delivery deadline; correctness over speed.
- Single operator; AI-only delivery; human at the gates.
- Rollback is a flag flip plus written cleanup, not the flag alone.
- Trunk-based development with squash merges to `main`.

## Assumptions

- The locally installed OpenClaw and Hermes can be started by an operator command and pointed at the local fleet. [assumption]
- The local OpenClaw install may still expose a handshake surface that this repository does not describe. [assumption]
- The signed-in local operator is the only person who will revoke workers. [assumption]

## Out of Scope

- The hosted multi-tenant platform
- Public network exposure
- A new authentication or worker-identity scheme
- A third worker runtime
- AI-DLC agents queuing or leasing work at runtime
- Turning development-tooling agents into chat-runtime agents
- General-purpose mesh routing between MindRoom instances
- MindRoom starting or supervising worker processes [Q4]
- Turning on Agno learning for the excluded agent [Q8]
- A new coverage-percentage gate

## Open Questions

- Whether the local OpenClaw install exposes a usable handshake surface (decides FR4.2 vs FR4.3).
- How large live mesh wiring is if FR4.2 proceeds.
- Exact wording of the single activation-status statement in FR5.1.

## Assumptions & Open Questions

The assumptions and open questions above are the complete set.
None.

## Traceability

| ID | Origin |
|----|--------|
| FR1 | `scope-document.md` Must Have hygiene; Q1, Q2, Q10 |
| FR2 | `scope-document.md` SM1; Q3, Q4 |
| FR3 | `scope-document.md` SM4; Q6, Q7, Q8 |
| FR4 | `scope-document.md` SM3 Should Have; Q5, Q9; `architecture.md` inert extension point |
| FR5 | `scope-document.md` status correction; Q11 |
| FR6 | constraint register rollback |
| NFR1–NFR6 | constraint register and `code-structure.md` fail-closed gates |

## Review

**Verdict:** NOT-READY
**Reviewer:** aidlc-product-lead-agent
**Date:** 2026-08-16T17:10:21Z
**Iteration:** 1

### Findings

| # | Severity | Location | Finding | Recommendation |
|---|---|---|---|---|
| 1 | Major | FR2.1–FR2.4 | The increment that “counts” is untestable as written. FR2.2 and FR2.3 require an OpenClaw-runtime worker and a Hermes-runtime worker to enroll, take a job, and finish it. No FR states how the operator issues an enrollment token, how a job for that runtime enters the queue, or what “finish” observably means (accepted attested complete vs. local process exit). Q3/Q4 only settled *which* runtimes and *who starts the worker*. Scope SM1 and `architecture.md` (admin issue → enroll → lease → complete) imply those steps, but they are not requirements. QA cannot write the live-path test without inventing the missing operator actions. | Add testable FRs for: signed-in operator issues an enrollment token; operator or documented command enqueues a job the named runtime can lease; “finish” means the fleet accepts the worker’s complete for that lease. Keep SM2 out. |
| 2 | Major | FR3.1 | “Offer a promotion candidate” is not a pass/fail criterion. Q6 confirmed: after a successful visible agent reply, the runtime records capture evidence and offers a candidate. FR3.1 repeats “offer” without an observable surface (Matrix notice, dashboard, stored proposal row, log-only) or stored state (e.g. a proposal at `proposed`). QA cannot tell a pass from a silent no-op. | Specify the operator-visible surface and the durable state after one successful visible reply. State that a demo-script-only run still must not suffice (already FR3.2). |
| 3 | Major | FR3.3, FR3.4 | The governed loop has no triggers and no sad paths. Scope SM4 requires automated canary then human-approved stable install. `architecture.md` has evaluate → review → canary both roots → stabilize both roots, plus reject and partial-failure rollback. FR3.3 does not say what starts canary or that it is automated and not the live skill root. FR3.4 does not say stable is refused without a successful dual-root canary plus non-blank reviewer and reason, or what happens on reject or one-root failure. QA can only check install locations on a happy path they cannot legally reach from FR3.1. | Add criteria: when canary runs; canary is a side location in both roots; stable requires prior dual-root canary plus named reviewer and reason; reject installs to neither live root; partial dual-root failure does not leave only one runtime stably installed (state the compensation). |
| 4 | Minor | FR1.2 | After revoke, enroll/heartbeat/lease/complete are refused, but the revoked worker’s current lease is unspecified. `code-structure.md` already requeues leased rows on revoke. Unstated, a held job can sit `leased` forever. Existing store behavior makes this low rework risk; QA would still miss it. | Require that a successful revoke requeues that worker’s leased jobs and that those jobs can be leased by a non-revoked worker. |
| 5 | Minor | FR6.1 | Turning the fleet off stops new enrollments and new leases only. Heartbeats, in-flight completes, and already-enrolled workers are unspecified. Rollback procedure (FR6.2) cannot be tested against a defined residual state. | State whether in-flight completes are still accepted and that no new enroll or lease succeeds after the flag is off. |
| 6 | Minor | FR2.1 | Singular “documented operator command” vs two admitted runtimes in FR2.2–FR2.4. A single command that only starts one runtime could be argued to satisfy FR2.1 while FR2.4 fails the increment. | Require a documented start path for each admitted runtime (OpenClaw and Hermes) against the local install. |
| 7 | Minor | NFR2 | “No fleet or handshake listener shall be exposed on a public network” has no observable bind/reject rule. The local MindRoom API already serves HTTP; FR1.3 already fail-closes without tailnet. QA cannot pass/fail “public network” without a listen address, interface, or non-tailnet refuse rule. | Tie NFR2 to a checkable rule (e.g. no new public bind; non-tailnet clients refused per FR1.3) rather than “public network.” |
| 8 | Minor | FR3.6; Assumptions & Open Questions | FR3.6 is a process note (“FR3.5 is the examination”), not a system behavior. The later “Assumptions & Open Questions / None” heading contradicts the populated Assumptions and Open Questions sections above it. Neither blocks implementation. | Drop FR3.6 as an FR (keep FR3.5 + out-of-scope). Remove the empty duplicate heading. |

### Summary

Q1–Q11 are reflected, SM2 stays out, SM5 stay-off matches Q8, and hygiene/fail-closed are testable. The live-worker increment and the learning-loop path still omit operator-visible steps and sad paths QA would have to invent, so engineering cannot start without coming back with questions.
