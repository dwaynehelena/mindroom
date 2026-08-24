# Requirements Analysis — Questions

## Sources

- [scope] Workflow-selected scope: `edge-fleet-mesh-learning-activation`.
- `intent-statement.md` in `intent-capture/`
- `scope-document.md` in `scope-definition/`
- `business-overview.md`, `architecture.md`, and `code-structure.md` in `aidlc/spaces/default/codekb/mindroom/`
- No `team-practices` artifact — practices discovery was skipped

These questions turn the approved scope and the reverse-engineering facts into testable requirements.
They do not reopen local-only targeting, Tailscale-only reach, existing enrollment authentication, the dropped AI-DLC runtime outcome, the absence of a deadline, or the test floor.

## Q1. How should administrative revocation become reachable?

The store can revoke a worker.
The real login path never supplies the permission the HTTP check looks for, so every live revoke is refused.
Scope already made working revocation a prerequisite of sign-off.

- A. On the local install, the signed-in operator may revoke without a separate permission claim
- B. The real login path must start carrying an explicit revoke permission, and only that permission may revoke
- C. Revoke stays on a dedicated administrative credential, separate from the dashboard login
- X. Other (please specify)

[Answer]: A. On the local install, the signed-in operator may revoke without a separate permission claim
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q2. When the Tailscale check fails, what should a fleet operation do?

The helper exists.
Nothing on the production fleet path calls it.
The helper that is supposed to raise does not raise.

- A. Fail closed — refuse enroll, heartbeat, lease, and complete until tailnet connectivity is confirmed
- B. Fail closed on enroll only — later operations may proceed once the worker is already admitted
- C. Log the failure and continue — the check is observational, not a gate
- X. Other (please specify)

[Answer]: A. Fail closed — refuse enroll, heartbeat, lease, and complete until tailnet connectivity is confirmed
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q3. For the first live-worker increment, which runtimes must enroll, take a job, and finish it?

The fleet admits exactly two runtimes.
The success metric says “a real worker of each admitted runtime.”

- A. Both OpenClaw and Hermes in the same increment
- B. One runtime first (specify which); the other follows as a later increment
- C. Either runtime is enough to count the increment done
- X. Other (please specify)

[Answer]: A. Both OpenClaw and Hermes in the same increment
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q4. Who starts the worker process that enrolls?

Nothing in the running MindRoom process today starts a worker client.
A live increment needs an operator-visible way to get a worker onto the fleet.

- A. A documented operator command starts the worker against the local install
- B. MindRoom itself starts and supervises the worker when the fleet is enabled
- C. An already-running local OpenClaw or Hermes process is pointed at the fleet; MindRoom does not start it
- X. Other (please specify)

[Answer]: A. A documented operator command starts the worker against the local install
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q5. After the scan, is the OpenClaw handshake still a Should Have in this release?

Reverse Engineering found an inert extension point, no protocol, no bound client, and no runtime start of the mesh.
Default mesh transport is an in-memory queue unless a real client is injected.

- A. Still Should Have — inspect the local OpenClaw install and implement the handshake if a usable surface exists
- B. Inspect and record the surface only; do not implement the handshake in this release
- C. Drop it from this release — the scan showed there is no usable surface to build against
- X. Other (please specify)

[Answer]: A. Still Should Have — inspect the local OpenClaw install and implement the handshake if a usable surface exists
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q6. Where must a live learning-loop candidate come from?

The promotion ledger exists.
Nothing in a live agent turn writes the evidence the capture step requires.
Scope says the skill must originate from a live agent run, not a demo script.

- A. After a successful visible agent reply — the runtime must record that evidence and offer a candidate
- B. Only when the operator explicitly asks to capture a skill from a named completed run
- C. From any successful tool-using agent run, even if no visible reply was delivered
- X. Other (please specify)

[Answer]: A. After a successful visible agent reply — the runtime must record that evidence and offer a candidate
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q7. For a governed promotion, which runtime roots must receive the install?

The current publisher refuses to stabilize unless both runtime adapters are present.

- A. Both OpenClaw and Hermes roots for canary and for stable
- B. One root is enough for this release (specify which); the other adapter may stay unwired
- C. Canary may land in one root; stable still requires both
- X. Other (please specify)

[Answer]: A. Both OpenClaw and Hermes roots for canary and for stable
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q8. What should happen to the agent that currently has learning turned off?

That agent is named for the OpenClaw runtime.
Its flag is Agno preference-learning, which is a different system from the promotion loop.
Scope left this as a Should Have after the self-referential path is examined.

- A. Keep it off — do not turn on Agno learning for that agent in this initiative
- B. Turn it on only after a written guard against a self-referential promote-into-itself loop
- C. Turning the flag on is out of this initiative; only the promotion pipeline is in
- X. Other (please specify)

[Answer]: A. Keep it off — do not turn on Agno learning for that agent in this initiative
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q9. The mesh enrollment switch and config exist and nothing reads them. What should this initiative do?

- A. Remove them — they are leftover surface
- B. Wire them as the start path if the handshake stays in; remove them if the handshake is deferred or dropped
- C. Leave them unused and document that they do nothing
- X. Other (please specify)

[Answer]: B. Wire them as the start path if the handshake stays in; remove them if the handshake is deferred or dropped
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q10. The HTTP layer accepts an allowlist setting it never uses. What should this initiative do?

Enforcement already lives in the store, fail-closed when no allowlist is configured.

- A. Remove the unused HTTP-layer parameter and make tests assert the store effect
- B. Make the HTTP layer the enforcement point, and stop trusting the store-only check
- C. Keep both, but make the HTTP layer reject a value that disagrees with the store
- X. Other (please specify)

[Answer]: A. Remove the unused HTTP-layer parameter and make tests assert the store effect
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Q11. After this work establishes the real activation status, which statement should remain?

Two documents currently disagree about whether Edge Fleet activation is already approved.

- A. Activation stays gated until the named sign-off after hygiene and a live worker
- B. Treat architecture review as already obtained, and only the remaining implementation gates are open
- C. Replace both statements with a single status this work writes, and do not keep either old sentence
- X. Other (please specify)

[Answer]: C. Replace both statements with a single status this work writes, and do not keep either old sentence
**Mode:** chat
**Recorded:** 2026-08-16T17:07:05Z

## Consolidated Summary Confirmation

- On the local install, the signed-in operator may revoke without a separate permission claim
- Fail closed on enroll, heartbeat, lease, and complete until tailnet connectivity is confirmed
- Both OpenClaw and Hermes must enroll, take a job, and finish it in the first live-worker increment
- A documented operator command starts the worker against the local install
- Handshake remains Should Have: inspect the local OpenClaw install and implement only if a usable surface exists
- A live learning candidate comes after a successful visible agent reply
- Canary and stable promotion must land in both OpenClaw and Hermes roots
- Keep Agno learning off for the excluded agent
- Wire the unread mesh switch if the handshake stays in; remove it if the handshake is deferred or dropped
- Remove the unused HTTP-layer allowlist parameter and test the store effect
- Replace both old activation-status sentences with one status this work writes

Does this all look correct before I generate the requirements artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
