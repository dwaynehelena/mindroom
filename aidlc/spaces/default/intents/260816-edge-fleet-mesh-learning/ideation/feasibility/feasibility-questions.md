# Feasibility & Constraint Analysis — Questions

## Sources

- [scope] Workflow-selected scope: `edge-fleet-mesh-learning-activation`.

## Q1. What does "production" mean for Edge Fleet activation?

The intent statement's first success metric is Edge Fleet accepting real worker
enrollments "in production". MindRoom has both a local single-user install and a
Kubernetes-hosted SaaS platform, so "production" is ambiguous.

- A. The local install on this machine — activation means the flag is on in a real running MindRoom, not a test harness, but still a single-user local deployment
- B. The hosted SaaS platform (`saas-platform/`, Kubernetes instances) — activation means it ships to deployed instances
- C. Both, sequenced: prove it locally first, then promote to hosted
- D. Not yet defined
- X. Other (please specify)

[Answer]: A. The local install on this machine — activation means the flag is on in a real running MindRoom, not a test harness, but still a single-user local deployment

## Q2. How do external workers reach the Edge Fleet enrollment surface?

- A. Tailscale — workers reach it over the existing tailnet, no public exposure
- B. Localhost only — worker processes run on the same machine, no network exposure at all
- C. A publicly reachable HTTP endpoint with its own authentication
- D. Not yet defined — this is part of what the work must decide
- X. Other (please specify)

[Answer]: A. Tailscale — workers reach it over the existing tailnet, no public exposure

## Q3. What authentication and authorization model must a worker enrollment satisfy?

- A. Whatever the existing MindRoom credential/authorization machinery already provides — reuse it, add nothing new
- B. A dedicated worker-identity mechanism (enrollment token, shared secret, or certificate) separate from user authorization
- C. No authentication needed given the trust boundary implied by the answer to Q2
- D. Not yet defined — a design decision for a later stage
- X. Other (please specify)

[Answer]: A. Whatever the existing MindRoom credential/authorization machinery already provides — reuse it, add nothing new

## Q4. Are there regulatory or compliance obligations on this work?

Worker enrollment moves work and possibly conversation content across a process
or machine boundary, and the learning loop captures agent behaviour and writes it
into external runtimes.

- A. None — this is a personal/internal system with no regulated data, no external data subjects, and no audit obligation
- B. Yes — some apply (name them: GDPR, SOC 2, data residency, contractual, or other)
- C. None today, but external exposure is foreseeable and the design should not foreclose it
- D. Not yet defined
- X. Other (please specify)

[Answer]: A. None — this is a personal/internal system with no regulated data, no external data subjects, and no audit obligation

## Q5. What is the delivery timeline or deadline pressure?

- A. No deadline — correctness and thoroughness over speed
- B. Soft target tied to the competitive-positioning trigger (specify the horizon)
- C. Hard deadline (specify the date and what it is tied to)
- D. Not yet defined
- X. Other (please specify)

[Answer]: A. No deadline — correctness and thoroughness over speed

## Q6. Are there organizational or practical blockers to this work?

- A. None — no change freeze, no competing priority, no approval chain beyond the sign-off already recorded
- B. Yes — one or more blockers apply (specify)
- C. Not yet defined
- X. Other (please specify)

[Answer]: A. None — no change freeze, no competing priority, no approval chain beyond the sign-off already recorded

## Q7. What testing posture applies to this work?

The org default ties the test floor to named scopes; this workflow runs a custom
composed scope, so the floor is not resolved automatically. The workflow itself
is configured for a Comprehensive test strategy.

- A. Treat it like a feature: tests written alongside code, minimum 80% line coverage, green in CI before merge
- B. Stricter — every activated path needs an end-to-end test against the real local `openclaw`/`hermes` installs, not just unit tests
- C. Lighter — existing suite must stay green; no new coverage floor for this work
- D. Not yet defined
- X. Other (please specify)

[Answer]: C. Lighter — existing suite must stay green; no new coverage floor for this work

## Q8. In the governed learning loop, what governs promotion?

"Governed" implies a review step between capturing a skill from a live agent run
and installing it into an external runtime root.

- A. Human review — you approve each promotion before it installs
- B. Automated policy — promotion proceeds when defined criteria pass, with no per-promotion human step
- C. Human review initially, with automation as a later goal once the criteria are proven
- D. Not yet defined
- X. Other (please specify)

[Answer]: B. Automated policy — promotion proceeds when defined criteria pass, with no per-promotion human step

## Q9. What is the risk appetite for changes to the live agent runtime?

Wiring the promotion path and flipping the remaining agent to participate in
learning both touch the runtime that serves your live agents.

- A. Low — the live agent runtime must not regress; every change is guarded by a flag or is reversible without a redeploy
- B. Moderate — occasional disruption to your own local agents is acceptable while this is being built
- C. High — this is a development system; break it freely and fix forward
- D. Not yet defined
- X. Other (please specify)

[Answer]: C. High — this is a development system; break it freely and fix forward

## Q10. How should the dependency on the locally installed `openclaw` and `hermes` be treated?

Both are third-party runtimes installed outside this repository, so their
interfaces can change independently of MindRoom.

- A. Pin and verify — record the versions integrated against, and fail loudly with a clear message when the installed version does not match
- B. Track loosely — integrate against whatever is installed, tolerate drift, fix when it breaks
- C. Abstract behind an adapter so a version or vendor change is contained
- D. Not yet defined
- X. Other (please specify)

[Answer]: C. Abstract behind an adapter so a version or vendor change is contained

## Q11. What rollback expectation applies to Edge Fleet production activation?

- A. Flag flip — turning the feature flag off fully deactivates it, and that is sufficient rollback
- B. Flag flip plus documented state cleanup (enrolled workers, leases, persisted records) with a written procedure
- C. Full rollback rehearsal before activation, proving deactivation from a live enrolled state
- D. Not yet defined
- X. Other (please specify)

[Answer]: B. Flag flip plus documented state cleanup (enrolled workers, leases, persisted records) with a written procedure

## Q12. Which of the four activation items is most likely to fail, and where should feasibility scrutiny concentrate?

- A. The OpenClaw gateway handshake — it depends on a third-party interface not yet inspected
- B. Edge Fleet production activation — it carries the security sign-off and the largest blast radius
- C. The governed learning loop's runtime promotion path — it writes into external runtime roots
- D. The AI-DLC team's runtime access — it is the least specified of the four
- E. No strong prior; assess them evenly
- X. Other (please specify)

[Answer]: E. No strong prior; assess them evenly

## Q13. The intent statement names the wrong agent for the learning flag — how should that be corrected?

The approved intent statement's fifth success metric says "`analyst` is set to
`learning: true`". The code scan found the agent carrying `learning: false` is
actually the `openclaw` agent (`config.yaml:270`, block starting `:236`), and the
counts are 16 agents `true` / 1 `false`, not 17 of 18. Notably, the one agent
excluded from learning is named for the same runtime the promotion pipeline
installs into.

- A. Correct it here — the intended agent is `openclaw`; record the correction in this stage's artifacts and carry the corrected fact forward
- B. Correct it here AND revise the approved intent statement so the record is consistent
- C. The intent was genuinely about a different agent — neither `analyst` nor `openclaw` (specify)
- D. Leave `openclaw` at `learning: false` deliberately — excluding it is correct, and the metric should be dropped
- X. Other (please specify)

[Answer]: B. Correct it here AND revise the approved intent statement so the record is consistent

## Q14. Two documents state contradictory activation status for Edge Fleet — which governs?

- `docs/dev/portfolio-register.md:15` — "Production activation awaiting explicit security approval from Dwayne, do not activate"
- `docs/edge-fleet-production-activation-security-architecture.md:6` — "Security approval obtained — implementation ready", while `:466` says only Gate 1 of a 4-gate framework (Architecture Review) is complete

- A. The portfolio register governs — activation is still gated, and the security-architecture doc's status line is inaccurate and should be corrected
- B. The security-architecture doc governs — Gate 1 approval is the approval that matters, and the register is stale
- C. Neither is authoritative — this work must establish the real gate status as an output
- D. Not yet defined
- X. Other (please specify)

[Answer]: C. Neither is authoritative — this work must establish the real gate status as an output

## Q15. A live admin bearer key is hardcoded in a repository file — how should that be handled?

`edge_fleet_cross_device_demo.py:29` contains a literal admin key used as an
`Authorization: Bearer` header against the local API. It is committed to the
repository.

- A. Treat it as in scope for this work: rotate the key and remove the literal as part of the activation effort
- B. Out of scope for this workflow, but record it as a risk in the RAID log for separate handling
- C. Not a concern — it is a local-only development value with no real authority
- D. Not yet defined
- X. Other (please specify)

[Answer]: A. Treat it as in scope for this work: rotate the key and remove the literal as part of the activation effort

## Q16. Contradiction — "governed" promotion vs. an automated policy with no human step

Side by side:

- The intent statement's fourth success metric is that **the governed learning
  loop** promotes a skill from a live agent run into an external runtime root.
- Q8 answered: **automated policy — promotion proceeds when defined criteria
  pass, with no per-promotion human step.**
- The existing review step requires a named reviewer and a stated reason before a
  candidate can advance (`learning_loop.py:182`), and a Matrix approval tool name
  `mindroom.learning.promote` exists for exactly that human step
  (`learning_runtime.py:118`).

These conflict: an automated policy either bypasses that review step or supplies
a synthetic reviewer identity, and it makes the approval tool dead code. Combined
with Q7 (lighter test floor) and Q9 (high risk appetite), the result is
unreviewed, lightly tested writes into external runtime roots.

- A. Keep automated promotion, and redefine "governed" to mean the criteria are the governance — the review step records an automated reviewer identity, and the Matrix approval tool is retired
- B. Automated for the canary stage, human approval required only for the stable install into the runtime root
- C. Revert to human review per promotion — Q8 overstated the intent
- D. Automated, but gated behind its own feature flag that is off by default, so the human step is the flag
- X. Other (please specify)

[Answer]: B. Automated for the canary stage, human approval required only for the stable install into the runtime root

## Q17. Contradiction — a lighter test floor vs. the workflow's Comprehensive test strategy

Side by side:

- The workflow is configured with `Test Strategy: Comprehensive` in
  `aidlc-state.md`, which sets how many tests get written throughout.
- Q7 answered: **lighter — existing suite must stay green; no new coverage floor
  for this work.**

- A. Change the workflow's test strategy to Minimal so the setting matches the answer
- B. Change it to Standard — a middle position: meaningful tests for new paths, no percentage floor
- C. Keep Comprehensive and treat Q7 as meaning only "no fixed coverage percentage", not "few tests"
- D. Keep both as stated — the Comprehensive setting governs, and Q7 is withdrawn
- X. Other (please specify)

[Answer]: A. Change the workflow's test strategy to Minimal so the setting matches the answer

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct

## Assumptions & Open Questions

None.
