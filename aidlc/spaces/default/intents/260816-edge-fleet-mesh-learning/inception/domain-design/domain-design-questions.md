# Domain Design — Questions

## Sources

- `requirements.md` — FR1–FR6, NFR1–NFR6
- `architecture.md` and `component-inventory.md` in the MindRoom knowledge base
- No `stories` or `team-practices` artifacts

These questions decide which existing building blocks own the new behaviour.
They do not reopen the approved requirements.

The knowledge base already names Edge Fleet, Edge Fleet HTTP API, Dashboard Authentication, Tailscale Connectivity Check, Agent Mesh, Mesh Enrollment Coordinator, Governed Learning, Learning Capture, Flight Recorder, and Shared Runtime.

## Q1. Should this initiative add a new component, or only change existing ones?

- A. Only change existing components — no new bounded building block
- B. Add one new component for the operator worker-start command; leave the rest as existing
- C. Add more than one new component (specify)
- X. Other (please specify)

[Answer]: A. Only change existing components — no new bounded building block
**Mode:** guided
**Recorded:** 2026-08-16T18:03:00Z

## Q2. Which component owns “the signed-in operator may revoke”?

- A. Edge Fleet HTTP API — drop the unused permission check on the local install
- B. Dashboard Authentication — start emitting a revoke permission for the signed-in user
- C. Edge Fleet store — revoke is a store operation with no HTTP permission concept
- X. Other (please specify)

[Answer]: A. Edge Fleet HTTP API — drop the unused permission check on the local install
**Mode:** guided
**Recorded:** 2026-08-16T18:03:00Z

## Q3. Which component owns the fail-closed Tailscale check on enroll, heartbeat, lease, and complete?

- A. Edge Fleet store — every mutate path checks before it proceeds
- B. Edge Fleet HTTP API — the router checks, then calls the store
- C. Tailscale Connectivity Check stays a helper; Shared Runtime calls it before mounting or serving fleet routes
- X. Other (please specify)

[Answer]: B. Edge Fleet HTTP API — the router checks, then calls the store
**Mode:** guided
**Recorded:** 2026-08-16T18:03:00Z

## Q4. Which component owns the documented operator command that starts a worker?

- A. Edge Node Client — document and ship a command that already uses that client
- B. A new thin CLI wrapper around Edge Node Client (only if Q1 is B)
- C. Shared Runtime / orchestrator starts the worker — rejected by FR2.5; do not pick unless requirements change
- X. Other (please specify)

[Answer]: A. Edge Node Client — document and ship a command that already uses that client
**Mode:** guided
**Recorded:** 2026-08-16T18:03:00Z

## Q5. Which component records evidence after a successful visible agent reply so a learning candidate can be offered?

- A. Flight Recorder — become a real caller from the existing visible-delivery path, then Learning Capture reads it
- B. Learning Capture — hook the visible-delivery path directly and skip the unused Flight Recorder
- C. Shared Runtime — a new adapter in the turn pipeline writes a proposal row itself
- X. Other (please specify)

[Answer]: A. Flight Recorder — become a real caller from the existing visible-delivery path, then Learning Capture reads it
**Mode:** guided
**Recorded:** 2026-08-16T18:04:00Z

## Q6. If the handshake stays in, which component becomes the live start path?

- A. Shared Runtime / API mount starts Mesh Gateway when the existing mesh enrollment switch is on
- B. Mesh Gateway stays demo-only; a documented operator command starts it
- C. Decide after the local OpenClaw inspection, not in this catalogue
- X. Other (please specify)

[Answer]: A. Shared Runtime / API mount starts Mesh Gateway when the existing mesh enrollment switch is on
**Mode:** guided
**Recorded:** 2026-08-16T18:04:00Z

## Q7. Who owns the single activation-status statement that replaces the two disagreeing documents?

- A. This is documentation owned outside the runtime components — no new software component
- B. Shared Runtime health/status payload is the source of truth; docs quote it
- X. Other (please specify)

[Answer]: B. Shared Runtime health/status payload is the source of truth; docs quote it
**Mode:** guided
**Recorded:** 2026-08-16T18:04:00Z

## Consolidated Summary Confirmation

- Only change existing components — no new bounded building block
- Edge Fleet HTTP API owns operator revoke (drop the unused permission check)
- Edge Fleet HTTP API owns the fail-closed Tailscale check, then calls the store
- Edge Node Client owns the documented worker-start command
- Flight Recorder becomes a real caller after a visible reply; Learning Capture reads it
- If the handshake stays in, Shared Runtime / API mount starts Mesh Gateway when the mesh enrollment switch is on
- Shared Runtime health/status is the activation-status source of truth; docs quote it

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
