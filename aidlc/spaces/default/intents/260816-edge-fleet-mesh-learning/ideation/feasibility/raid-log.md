# RAID Log — Edge Fleet / Mesh / Learning Activation

Risks, Assumptions, Issues, and Dependencies for this initiative. Sources are the
confirmed answers in `feasibility-questions.md`, the outcomes in
`intent-statement.md`, and the findings recorded in `feasibility-assessment.md`.

Severity is assessed as likelihood against impact. Treatment is one of mitigate,
transfer, accept, or avoid.

## Risks

| ID | Risk | Likelihood | Impact | Severity | Treatment |
|----|------|-----------|--------|----------|-----------|
| R-1 | Administrative revocation cannot succeed in a real deployment, because the permission check reads from a placeholder the real authentication path never populates. Revocation is the primary containment control for a misbehaving worker | High — it is the current state, not a possibility | High — no way to eject a worker once enrolled | **Critical** | Mitigate: repair before activation. Activation without working revocation should not be signed off |
| R-2 | A documented standing requirement that every fleet operation verify tailnet connectivity is not enforced anywhere in the fleet code. The chosen network posture depends on it | High | High — the sole network boundary is unenforced | **Critical** | Mitigate: make the directive real, or withdraw it and state the true boundary |
| R-3 | A live administrative bearer key is committed to the repository in a demo script and used as a real authorization header | Certain — already the case | Medium — local scope, but a real credential in version control | **High** | Mitigate: rotate the key and remove the literal. Confirmed in scope |
| R-4 | The second success metric names an actor that does not exist as a runtime entity, so the outcome cannot be designed against | Certain | High — one quarter of the milestone is undefined | **High** | Mitigate: restate the outcome in Requirements Analysis before any dependent design |
| R-5 | An allowlist parameter is accepted by the request-handling layer and never used, so configuring it there has no effect while appearing to | Certain | Medium — silent misconfiguration, and an existing test asserts only that the parameter exists | **High** | Mitigate: remove the dead parameter or wire it, and strengthen the test to assert effect rather than presence |
| R-6 | Reaching the OpenClaw handshake outcome requires wiring the mesh into the running system, not only implementing the handshake. The mesh has no runtime caller and its transport defaults to an in-memory queue | High | High — hidden scope roughly the size of the named work | **High** | Mitigate: size this explicitly during Reverse Engineering rather than discovering it during construction |
| R-7 | Several repository documents describe these capabilities as complete and production-ready while no runtime path reaches them. Planning that trusts the documentation will mis-scope the work | High | Medium — this initiative is now forewarned, but future work is not | **Medium** | Mitigate: correct the status claims as part of this work, since closing exactly this gap is the stated problem |
| R-8 | A lighter test floor combined with high risk tolerance means regressions in the live agent runtime may go undetected | Medium | Medium — bounded by the local, single-user target | **Medium** | Accept, with the existing suite as the floor. Explicitly chosen; no percentage gate applies |
| R-9 | Both external runtimes can change independently of this repository, breaking integration without warning | Medium | Medium | **Medium** | Mitigate: contain behind an adapter, per the recorded constraint |
| R-10 | Turning the flag off leaves enrolled workers, leases, and audit records in place, so deactivation is incomplete without cleanup | Certain | Medium | **Medium** | Mitigate: the documented cleanup procedure is already a required deliverable |
| R-11 | The one agent excluded from the learning loop is named for the same runtime the promotion pipeline installs into. Enabling it may create a self-referential path worth reasoning about before it is switched on | Low | Medium | **Low** | Mitigate: examine the interaction when the learning outcome is designed |

## Assumptions

| ID | Assumption | Owner | Status | Validation |
|----|-----------|-------|--------|-----------|
| A-1 | The locally installed OpenClaw and Hermes expose interfaces stable and complete enough to integrate against | Dwayne | Unvalidated | Reverse Engineering will inspect both installs |
| A-2 | The four outcomes can be sequenced independently, with no hard ordering between them | Dwayne | Unvalidated | Units Generation and Delivery Planning will establish the real dependency order |
| A-3 | Local-only activation keeps the regulatory picture empty | Dwayne | Held, conditional | Holds only while the target stays the local install; reassess if the hosted platform ever comes into scope |
| A-4 | The existing test suite passing is a sufficient regression signal for this work | Dwayne | Accepted by decision | Chosen deliberately; revisit only if a regression escapes it |

## Issues

Issues are problems that already exist, as distinct from risks that might
materialise. Every entry here was observed directly in the current codebase.

| ID | Issue | Severity | Action |
|----|-------|----------|--------|
| I-1 | Enrollment is currently impossible by configuration: admission is fail-closed against an allowlist, and with none configured every enrollment is denied | High | Configure the allowlist as part of activation; treat it as a required step, not a tuning option |
| I-2 | Two repository documents state contradictory activation status for the same item — one says activation is gated pending approval, the other says approval is obtained, while also stating that only the first of four gates is complete | High | This work establishes the real status; neither document may be cited as authoritative in the meantime |
| I-3 | The intent statement names the wrong agent as excluded from learning, and states the wrong counts | Medium | Revise the intent statement to name the correct agent and counts |
| I-4 | A feature flag and a configuration section for mesh enrollment are defined and exported but read by nothing | Low | Either wire them or remove them; leaving them is more of the documented-but-inert surface this initiative exists to reduce |
| I-5 | A helper documented as raising when connectivity is absent never raises, and has no callers | Low | Fold into the R-2 remediation |

## Dependencies

| ID | Dependency | Type | Status | Impact if unmet |
|----|-----------|------|--------|-----------------|
| D-1 | Explicit sign-off from the named approver before Edge Fleet activation | Approval | Outstanding | Activation cannot proceed; the first success metric is unreachable |
| D-2 | Locally installed OpenClaw, and whatever surface it exposes for enrollment | External | Present but uninspected | The third success metric cannot be designed until inspection completes |
| D-3 | Locally installed Hermes, as a full integration target for worker enrollment | External | Present but uninspected | The first success metric is only partly reachable |
| D-4 | A restated definition of the AI-DLC runtime-access outcome | Internal | Outstanding | The second success metric cannot be designed or estimated |
| D-5 | A configured enrollment allowlist and a valid enrollment key of sufficient length | Configuration | Outstanding | The feature mounts no worker-facing routes, or mounts them and denies every enrollment |
| D-6 | Reverse Engineering must complete before Contract Design, since it resolves the integration surface | Internal sequencing | Planned | Contract Design would specify a handshake against an unknown surface |

## Assumptions & Open Questions

- **Open question** — whether R-1 and R-2 should be treated as prerequisites of
  activation or as work items inside it. Both bear directly on the security
  sign-off, so the distinction affects when that sign-off can reasonably be given.
