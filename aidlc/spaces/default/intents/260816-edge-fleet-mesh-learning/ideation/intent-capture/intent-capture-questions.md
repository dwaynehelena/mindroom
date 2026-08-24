# Intent Capture & Framing — Questions

## Sources

- [scope] Workflow-selected scope: `edge-fleet-mesh-learning-activation`.

## Q1. What business problem are we solving?

The system integration map you shared shows four things that are built but not wired together: the Edge Fleet (P9) production activation path, the AI-DLC team's runtime access to Edge Fleet/Mesh Gateway, the OpenClaw gateway network handshake, and the governed learning loop's live promotion path. What is the underlying problem this work needs to solve?

- A. Close the gap between what the codebase claims (docs, docstrings, positioning copy) and what actually runs — reduce "documented but inert" surface area
- B. Unlock a specific downstream capability that is currently blocked by one or more of these gaps being unwired (name it in Other)
- C. Prepare for a near-term event (audit, demo, customer commitment, security review) that requires these paths to be real
- D. General platform hardening — no single trigger, just closing known technical debt
- X. Other (please specify)

[Answer]: A. Close the gap between what the codebase claims (docs, docstrings, positioning copy) and what actually runs — reduce "documented but inert" surface area

## Q2. Who is the customer for this work — internal or external?

- A. Internal only — this is platform/infrastructure work for the MindRoom team itself
- B. External — a customer or partner needs the Edge Fleet, OpenClaw handshake, or governed learning to actually function
- C. Both — internal teams need it now, external exposure is a likely next step
- D. Not yet defined
- X. Other (please specify)

[Answer]: A. Internal only — this is platform/infrastructure work for the MindRoom team itself

## Q3. What does success look like? What metrics matter?

- A. Edge Fleet accepts real `openclaw`/`hermes`-runtime worker enrollments in production, with the security approval gate honored
- B. The AI-DLC agent team can actually queue/lease work through Edge Fleet or route through Mesh Gateway at runtime (not just author the code)
- C. A real OpenClaw gateway completes the enrollment handshake against `mesh/enrollment.py`'s currently-inert extension point
- D. The governed learning loop promotes a skill from a live agent run (not just `scripts/learning_stable_demo.py`) into `openclaw_root`
- E. All of the above, measured together as one activation milestone
- X. Other (please specify)

[Answer]: E. All of the above, measured together as one activation milestone

## Q4. What is the trigger for this initiative?

- A. Market pressure / competitive positioning (e.g. matching or differentiating from OpenClaw / Hermes Agent)
- B. Technical debt — these were built as engineering deliverables and never connected to their runtime consumers
- C. A specific opportunity or committed use case that needs one or more of these paths live
- D. Not yet defined
- X. Other (please specify)

[Answer]: A. Market pressure / competitive positioning (e.g. matching or differentiating from OpenClaw / Hermes Agent)

## Q5. Who are the key stakeholders, and what does each care about?

The security-architecture doc names an explicit approver for Edge Fleet production activation ("do not activate" pending sign-off).

- A. Dwayne is the sole approver/stakeholder for all four items
- B. Dwayne approves the security-sensitive activation (Edge Fleet, external handshake); other stakeholders exist for the AI-DLC wiring and learning-loop pieces (name them in Other)
- C. Not yet defined
- X. Other (please specify)

[Answer]: A. Dwayne is the sole approver/stakeholder for all four items

## Q6. Who decides scope or priority across the four items, and who influences that decision?

- A. Same person/group as Q5
- B. Different — scope/priority calls are made separately from the security sign-off (explain in Other)
- C. Not yet defined
- X. Other (please specify)

[Answer]: A. Same person/group as Q5

## Q7. Are there communication requirements or a reporting cadence for this work?

- A. None — proceed and report at the normal approval gates
- B. Dwayne wants explicit notice before the Edge Fleet production flag is ever flipped on, separate from the workflow's own approval gates
- C. Not yet defined
- X. Other (please specify)

[Answer]: A. None — proceed and report at the normal approval gates

## Q8. The workflow was started with the scope `edge-fleet-mesh-learning-activation` (a custom 26-of-33-stage plan: full inception + construction + operation, no market research/team-formation/mockups/user-stories/practices-discovery). Does that match your intended product boundary?

- A. Yes, that scope is correct as composed
- B. No — narrow it (specify which of the four items to drop or defer in Other)
- C. No — widen it (specify what's missing in Other)
- X. Other (please specify)

[Answer]: A. Yes, that scope is correct as composed

## Q9. The task description said "every live agent set to `learning: false`," but `config.yaml` shows 17 of 18 agents are already `learning: true` — only the `analyst` (OpenClaw-style) agent is `false`. What should "wire up the governed learning loop" actually mean here?

- A. The `learning: true` flag already exists as intended; the real gap is that the promotion pipeline (capture → review → promote → install into `openclaw_root`/`hermes_root`) is never invoked at runtime — wire that up for the agents that already have `learning: true`
- B. Flip `analyst` to `learning: true` as well so every live agent participates, in addition to wiring the runtime promotion path
- C. "Learning" here meant something narrower than the `learning:` config flag (explain in Other)
- X. Other (please specify)

[Answer]: B. Flip `analyst` to `learning: true` as well so every live agent participates, in addition to wiring the runtime promotion path

## Q10. Given Edge Fleet's `WorkerRuntime` is hardcoded to exactly `"openclaw"` and `"hermes"`, and there is no real Hermes service anywhere in this codebase (only a competitor name-check and placeholder runtime identity) — should this work touch Hermes at all?

- A. No — leave Hermes exactly as a placeholder identity in the `WorkerRuntime` literal; do not attempt to build or simulate a Hermes counterpart
- B. Yes — build a synthetic/mock Hermes counterpart good enough to validate the Edge Fleet and learning-loop paths end-to-end (explain what "good enough" means in Other)
- C. Not yet defined
- X. Other (please specify)

[Answer]: X. Other — "heremes is installed locally"

## Q11. For the real OpenClaw gateway enrollment handshake (item 3) — is there an actual OpenClaw gateway instance/spec available to integrate against, or does this need to be built against a documented protocol with no live counterpart to test?

- A. There is a real OpenClaw gateway (or a documented protocol spec) available to build and test against — provide details in Other
- B. No real counterpart exists yet; build the handshake client per `mesh/enrollment.py`'s existing extension points and validate with a mock/simulated gateway, the same way `edge_fleet_cross_device_demo.py` validates Edge Fleet today
- C. Not yet defined
- X. Other (please specify)

[Answer]: X. Other — "openclaw is installed locally"

## Q12. For Edge Fleet production activation specifically — beyond Dwayne's explicit sign-off, are there other conditions that must be true before `MINDROOM_EDGE_FLEET_ENABLED=true` ships to production (e.g. a specific security review checklist, a staging soak period, a rollback rehearsal)?

- A. No — Dwayne's explicit sign-off is the only gate; the security-architecture doc's threat model is otherwise already sufficient
- B. Yes — additional conditions apply (list them in Other)
- C. Not yet defined
- X. Other (please specify)

[Answer]: A. No — Dwayne's explicit sign-off is the only gate; the security-architecture doc's threat model is otherwise already sufficient

## Q13. `hermes` is installed locally (`/Users/dwayne/.local/bin/hermes`, with a `~/.hermes/` directory). What role should it play in this work?

- A. Full integration target — Edge Fleet must accept a real `hermes`-runtime worker that enrolls, leases, and completes work against the local Hermes install
- B. Validation harness only — use the local install to exercise the `hermes` runtime path, but do not treat Hermes as a supported production runtime in this initiative
- C. Learning-loop target only — Hermes matters for skill promotion into `hermes_root`, not for Edge Fleet worker enrollment
- D. Not yet defined
- X. Other (please specify)

[Answer]: A. Full integration target — Edge Fleet must accept a real `hermes`-runtime worker that enrolls, leases, and completes work against the local Hermes install

## Q14. `openclaw` is installed locally (`/Users/dwayne/.npm-global/bin/openclaw`, with `~/.openclaw/`). Which surface should the Mesh Gateway enrollment handshake integrate against?

- A. The local OpenClaw CLI — drive enrollment through its command surface
- B. A local OpenClaw gateway service (HTTP/socket endpoint) exposed by that install — specify the endpoint in Other if known
- C. Determine it during Reverse Engineering — inspect the local install alongside `mesh/enrollment.py`'s extension points and propose the integration surface then
- D. Not yet defined
- X. Other (please specify)

[Answer]: C. Determine it during Reverse Engineering — inspect the local install alongside `mesh/enrollment.py`'s extension points and propose the integration surface then

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct

## Assumptions & Open Questions

None.
