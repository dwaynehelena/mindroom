# Feasibility Assessment — Edge Fleet / Mesh / Learning Activation

## Glossary

Terms used below, defined once so this document reads without prior knowledge of
the codebase.

| Term | Meaning |
|------|---------|
| Edge Fleet | The part of MindRoom that lets separate worker processes enroll, take on queued jobs, and report results back |
| Worker enrollment | The one-time handshake in which a new worker proves its identity and is admitted to the fleet |
| Lease | A claim a worker takes on one queued job, so two workers cannot do the same work |
| Attestation | A signature a worker attaches to each request, proving the request came from that worker and was not replayed |
| Mesh Gateway | A separate coordination layer for routing work between MindRoom instances |
| Feature flag | An on/off switch, read from the environment, that decides whether a capability is available at all |
| Canary promotion | Installing a learned skill to a side location first, where it can be observed before becoming the real thing |
| Stable promotion | Installing a learned skill into the runtime's real skill directory, where it takes effect |
| Tailnet | The private network created by Tailscale, reachable only by machines you have enrolled in it |

## What This Assessment Covers

This assessment tests whether the four activation outcomes named in
`intent-statement.md` can actually be reached, and at what risk. It is grounded
in a direct reading of the current code, not in the project's own documentation —
a distinction that matters here, because the gap between the two is the stated
problem.

The assessment was made with three perspectives applied in turn: an architect's
view of whether the pieces fit, a platform engineer's view of how the thing runs
and rolls back, and a compliance view of what obligations attach. The compliance
view is short by design: this is an internal system with no regulated data and no
external data subjects, so no regulatory framework applies.

## Headline Finding

**The four outcomes are not equally feasible, and they are not equally
understood.** Three of the four rest on machinery that already exists and is
extensively tested; one rests on a connection that does not exist in any form.

The recurring pattern across all four is the same one the initiative set out to
close: capability is built and tested in isolation, but nothing in the running
system ever calls it. This was confirmed rather than assumed — several documents
in the repository describe these capabilities as complete and production-ready
while no runtime path reaches them.

## Item-by-Item Assessment

### 1. Edge Fleet production activation — **Feasible, with specific defects to clear**

The underlying machinery is the most mature of the four. Worker identity,
single-use enrollment tokens, per-request attestation with replay protection and
a clock-skew window, result signing, revocation, and rate limiting are all
implemented and covered by roughly seventy tests that exercise real cryptography
and a real database rather than mocks.

Activation is gated by a feature flag that is off by default, and three further
conditions must hold before any of it mounts: an enrollment key must be present,
must be valid, and must be long enough. When any condition fails, the system
mounts no worker-facing routes at all. This is a sound fail-closed posture and
means activation cannot happen by accident.

Four defects stand between the current state and a working activation:

- **Enrollment is currently impossible by configuration.** The admission check is
  fail-closed against an allowlist, and when no allowlist is configured *every*
  enrollment is denied. Activation therefore requires configuring the allowlist,
  not just flipping the flag.
- **An allowlist parameter on the request-handling layer is accepted and never
  used.** Enforcement lives in one place only. The unused parameter is a trap for
  anyone who assumes configuring it there has an effect, and an existing test
  asserts only that the parameter exists, not that it works.
- **Administrative revocation cannot succeed in a real deployment.** The
  permission check reads from a placeholder that is always empty, and the real
  authentication path does not supply the permission data the check looks for. In
  practice every revocation attempt would be refused. Revocation is the primary
  containment control for a misbehaving worker, so this is the most consequential
  of the four.
- **The Tailscale requirement is documented but not enforced.** A standing
  directive in the code states that every fleet operation must verify tailnet
  connectivity first. No fleet code performs that check; only a standalone demo
  script does. Since the chosen network posture is Tailscale-only with no public
  exposure, this directive must become real rather than aspirational.

None of these is architecturally hard. All four are defects in wiring rather than
gaps in capability, which is why this item is assessed as feasible.

### 2. AI-DLC team runtime access — **Not feasible as currently stated; the outcome needs redefinition**

This is the significant finding of the assessment.

The intent statement's second success metric is that "the AI-DLC agent team can
queue and lease work through Edge Fleet, or route through Mesh Gateway, at
runtime." Investigation found no connection of any kind — but more importantly,
it found that the two things named are not the same kind of thing.

The AI-DLC agents are definitions belonging to the development tooling that runs
this workflow. They are not MindRoom runtime agents, do not appear in MindRoom's
agent configuration, and are not referenced anywhere in MindRoom's source. The
MindRoom agent set and the AI-DLC agent set do not overlap at all. The only place
the two meet today is that two architecture documents in this repository carry an
AI-DLC agent name as their stated author.

So the gap is not a missing wire between two live systems. It is that one of the
two endpoints does not exist as a runtime entity. This outcome cannot be assessed
as feasible or infeasible until it is restated — either as making AI-DLC agents
into real MindRoom runtime agents, or as giving the development tooling a client
path into Edge Fleet, or as something else. That restatement is a requirements
question, and it should be resolved before any design work depends on it.

**This is flagged as a blocking concern rather than glossed over**, per the
ideation guardrail on surfacing blockers early.

### 3. OpenClaw gateway enrollment handshake — **Feasibility unknown until the local install is inspected**

The extension point exists and is deliberately inert. Its shape is a callable
that takes no arguments and returns nothing, bound to nothing by default, behind
a switch that is off. A separate gate constant permits the handshake but does not
perform it. The design intent is clear: nothing reaches the network unless an
operator both supplies an implementation and turns the switch on.

What does not exist is any specification of what that implementation would say.
There is no protocol, no message schema, no endpoint, and no client — the
supporting document describes the intended round-trip in prose only. The inverse
direction *is* concrete: the mesh layer can issue a token that the Edge Fleet
enrollment surface already accepts, and that path is cross-verified by tests.

Feasibility therefore depends entirely on what the locally installed OpenClaw
exposes, which has been deliberately deferred to the Reverse Engineering stage.
Two conservative observations apply regardless of what that inspection finds:
the mesh layer narrows its accepted worker runtime to OpenClaw only, and the mesh
package has no runtime caller anywhere in MindRoom — it is started only by a demo
and a stress-test script, and its default message transport is an in-memory queue
rather than a real one. Reaching this outcome therefore means wiring the mesh
into the running system as well as implementing the handshake.

### 4. Governed learning loop promotion — **Feasible; the governance split resolves cleanly onto what exists**

The pipeline is present end to end as separable pieces: candidate construction,
capture gated on evidence that a real agent run actually succeeded, a state
machine that moves a candidate from proposed through evaluation to review, a
canary install to a side location, and a stable install into the runtime's skill
directory. Roughly thirty tests cover these against real files and a real
database.

Two ends are missing, and they are the two ends that matter:

- **Nothing produces a candidate from a live agent run.** The function documented
  as the production sink for runtime candidate events is called only by its own
  tests. No part of the running system emits the event it consumes.
- **Nothing performs promotion outside two demonstration scripts.** The function
  that would request a human review at runtime has no callers anywhere,
  including tests.

The decision to automate the canary stage while requiring human approval for the
stable install maps well onto what is already built: the review step already
demands a named reviewer and a stated reason, and an approval mechanism already
exists for exactly that human step. The split keeps that mechanism alive on the
consequential half rather than retiring it.

One factual correction belongs here. The initiative was framed around an agent
named `analyst` being excluded from learning. The agent actually excluded is
named for the OpenClaw runtime itself, and the counts are sixteen participating
and one excluded. The intent statement will be revised accordingly. The
coincidence is worth noting rather than dismissing: the single agent excluded
from learning is named for the same runtime the promotion pipeline installs into.

## Risk Concentration

Scrutiny was directed evenly across the four items rather than concentrated on a
prior. That produced an uneven result, which is more useful than an even one:

| Item | Feasibility | Dominant concern |
|------|-------------|------------------|
| Edge Fleet activation | Feasible | Revocation is unreachable, so containment of a bad worker fails |
| AI-DLC runtime access | Not assessable as stated | One endpoint is not a runtime entity |
| OpenClaw handshake | Unknown pending inspection | No protocol exists; the mesh itself is unwired |
| Learning promotion | Feasible | Both ends of the pipeline lack callers |

## Constraints Applied

The delivery context is permissive and does not itself constrain the work: no
deadline, no organizational blockers, no compliance obligations, and a high
tolerance for disruption to the local development runtime.

Three decisions do constrain it:

- Activation targets the local single-user install, not the hosted platform. This
  narrows blast radius substantially and removes multi-tenant concerns from
  scope.
- Network reach is Tailscale-only with no public exposure, and the existing
  authentication machinery is reused rather than extended.
- Rollback must be a flag flip plus a documented cleanup procedure for enrolled
  workers, leases, and persisted records — the flag alone does not remove state.

The high risk tolerance applies to the development runtime, not to the activation
itself: rollback discipline and the security sign-off both remain in force.

## Conservative Notes and Uncertainty

Per the ideation evidence standard, the following are labelled rather than
presented as fact:

- **Hypothesis**: the four items can be sequenced independently. Nothing found
  contradicts this, but no dependency analysis has been done — that belongs to a
  later stage.
- **Assumption**: the locally installed OpenClaw and Hermes are current enough to
  integrate against. Neither has been inspected yet.
- **Uncertain**: effort for item 2 cannot be estimated at all until the outcome is
  restated.

## Assumptions & Open Questions

- **Assumption** — the locally installed OpenClaw and Hermes expose a stable
  enough interface to build against; neither has been inspected. To be resolved
  during Reverse Engineering.
- **Open question** — what "the AI-DLC agent team can queue and lease work at
  runtime" should mean, given that AI-DLC agents are not MindRoom runtime
  entities. To be resolved in Requirements Analysis before any design depends on
  it.
- **Open question** — the true activation gate status for Edge Fleet, which this
  work must establish rather than inherit from either existing status document.
