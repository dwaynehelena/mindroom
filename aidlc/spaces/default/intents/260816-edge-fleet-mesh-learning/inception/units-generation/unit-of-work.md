# Units of Work

Four units, from `requirements.md`, `components.md`, `decisions.md`, and the confirmed answers in `units-generation-questions.md`.
There is no `stories` artifact.
The excluded-agent flag stays off and is not a unit. [Q1]

## Unit Index

| Unit ID | Directory | Name | Kind | Complexity |
|---------|-----------|------|------|------------|
| U1 | `u1-fleet-hygiene` | fleet-hygiene | service | M |
| U2 | `u2-live-fleet` | live-fleet | service | L |
| U3 | `u3-learning-promotion` | learning-promotion | library | L |
| U4 | `u4-handshake` | handshake | library | M |

## U1 fleet-hygiene

- **Directory**: `u1-fleet-hygiene`
- **Kind**: `service`
- **Deploy**: embedded in the local MindRoom process
- **Complexity**: M
- **Owns**: FR1, FR6
- **Components**: EdgeFleetHttpApi (revoke, unused allowlist parameter), EdgeFleetStore (tombstone, requeue, allowlist), TailscaleConnectivityCheck, SharedRuntime (flag-off)
- **Operators**: `revoke` — signed-in operator revokes an enrolled worker over existing admin HTTP. U1 does not issue enrollment tokens or enqueue jobs.
- **Delivers**: signed-in operator can revoke; every fleet operation fail-closes without tailnet; allowlist fail-closed and unused HTTP parameter gone; demo key rotated out; flag-off plus written cleanup
- **Constraints**: no new auth scheme; local install only
- **Notes**: Done when containment works, not when a worker has finished a job

## U2 live-fleet

- **Directory**: `u2-live-fleet`
- **Kind**: `service`
- **Deploy**: embedded API plus operator-started workers
- **Complexity**: L
- **Owns**: FR2, FR5
- **Components**: EdgeNodeClient, EdgeFleetHttpApi, EdgeFleetStore, SharedRuntime health
- **Operators**:
  - `issue-enrollment` — signed-in operator issues a one-time enrollment token through EdgeFleetHttpApi admin HTTP, persisted/consumed by EdgeFleetStore. Construction uses the brownfield `POST /api/edge-fleet-admin/enrollments` / `issue_enrollment` path; it does not invent a fifth unit or a new credential type.
  - `enqueue-compatible-job` — signed-in operator or documented command enqueues a job the named admitted runtime can lease. Construction uses EdgeFleetHttpApi or EdgeFleetStore (`queue_job`); “finish” is the fleet accepting that worker’s attested complete for the lease.
- **Delivers**: operator can issue an enrollment token and enqueue a compatible job; documented start command for both admitted runtimes; OpenClaw and Hermes each enroll, take a job, and finish; one activation-status sentence on health that docs quote
- **Constraints**: MindRoom does not start workers (FR2.5)
- **Notes**: Depends on U1; sign-off is requested after this unit, not after U1 alone. Token issue and enqueue are in-unit work on the existing HTTP/store components, not a new unit.

## U3 learning-promotion

- **Directory**: `u3-learning-promotion`
- **Kind**: `library`
- **Deploy**: embedded
- **Complexity**: L
- **Owns**: FR3
- **Components**: FlightRecorder, LearningCapture, GovernedLearning, SharedRuntime visible-delivery hook, Learning Runtime (`learning_runtime` / `GovernedLearningRuntime`)
- **Operators**:
  - `LearningCapture caller` — existing Learning Runtime invokes LearningCapture after the SharedRuntime / turn-pipeline FlightRecorder write. SharedRuntime records visible-delivery evidence; Learning Runtime is the named caller that turns that record into a promotion candidate. Construction does not invent a new ingest module; LearningCapture stays the propose policy, not the hook.
- **Delivers**: a successful visible reply records evidence; Learning Runtime then calls LearningCapture and yields a candidate; canary and stable land in both runtime roots; stable needs a named reviewer
- **Constraints**: Agno learning for the OpenClaw-named agent stays off
- **Notes**: No hard dependency on U2

## U4 handshake

- **Directory**: `u4-handshake`
- **Kind**: `library`
- **Deploy**: embedded, started by SharedRuntime if the mesh switch is on
- **Complexity**: M
- **Owns**: FR4
- **Components**: MeshGateway, MeshEnrollmentCoordinator, SharedRuntime
- **Operators**: `inspect-openclaw` — inspect the local OpenClaw install for a usable handshake surface, then either bind the existing handshake callable or remove the unread mesh switch. No fleet token or job-queue operators.
- **Delivers**: inspect local OpenClaw; implement handshake only if a usable surface exists; otherwise remove the unread mesh switch
- **Constraints**: no general-purpose mesh routing
- **Notes**: No hard dependency on U3; presence of FR4.2 is conditional on inspection

## Assumptions & Open Questions

- **Open question** — U4 may shrink to “remove the unread switch” if inspection finds no surface.
- **Assumption** — U2 `issue-enrollment` and `enqueue-compatible-job` reuse brownfield admin HTTP / `queue_job`; U3 `LearningCapture caller` is the existing Learning Runtime after the FlightRecorder write. No fifth unit.

## Architect response (units-generation, iteration 2)

**Date:** 2026-08-19T00:00:00Z
**Agent:** AI-DLC Architect
**Scope:** Close review finding 1 only. Human gate left `[?]`. Domain-design catalogue and Construction not modified.

| # | Severity | Disposition | What changed |
|---|---|---|---|
| 1 | Major | Closed in units-generation | U2 now boxes `issue-enrollment` and `enqueue-compatible-job` as in-unit operators on EdgeFleetHttpApi / EdgeFleetStore. U3 now boxes `LearningCapture caller` as existing Learning Runtime after the SharedRuntime / turn-pipeline FlightRecorder write. U1 stays revoke/hygiene; U4 stays inspect-or-remove. Four units unchanged. |
| 2 | Minor | Closed 2026-08-20 (construction kickoff) | Intra-unit FR order added to `unit-of-work-story-map.md`. Not a contract blocker. |
| 3 | Minor | Closed 2026-08-20 (construction kickoff) | SharedRuntime file ownership table added to `unit-of-work-story-map.md`. DAG unchanged. |
| 4 | Minor | Standing rule | Traceability sensor still expects `USx.y`; no invented story IDs. Coverage stays on FR IDs. |

## Review (iteration 2 — finding 1 re-judgment)

**Verdict:** READY
**Reviewer:** aidlc-architecture-reviewer-agent
**Date:** 2026-08-19T10:30:00Z
**Iteration:** 2
**Scope:** Re-judge units-generation finding 1 only, after U2/U3 operators were added. Human already approved the units-generation gate at 2026-08-19 10:22 UTC.

### Finding 1 verdict: pass

The original major is closed. Construction no longer has to invent callers.

| Check | Evidence |
|---|---|
| U2 names token issue | `unit-of-work.md` U2 operator `issue-enrollment` → brownfield `POST /api/edge-fleet-admin/enrollments` / `issue_enrollment` (`architecture.md` enroll sequence; inventory `EdgeFleet.issue_enrollment`) |
| U2 names job enqueue | U2 operator `enqueue-compatible-job` → EdgeFleetHttpApi or EdgeFleetStore `queue_job`; “finish” = attested complete accepted for the lease |
| U3 names LearningCapture caller | U3 operator `LearningCapture caller` = existing Learning Runtime (`learning_runtime` / `GovernedLearningRuntime`) after the SharedRuntime / turn-pipeline FlightRecorder write |
| No fifth unit | Story-map absorbs operators under FR2/FR3; U1 stays revoke/hygiene; U4 stays inspect-or-remove |

Residual (not a fail): `components.md` still lists LearningCapture `dependents: []` and omits issue-enrollment / enqueue from EdgeFleetHttpApi responsibilities. That is catalogue lag. Units + brownfield paths are the construction contract.

Findings 2–4 remain minor (intra-unit FR order, SharedRuntime file ownership, `USx.y` sensor mismatch). They are not architecture blockers.

### Construction readiness

Architecture of the four units is implementable. Do not start Construction yet — next inception stage is contract-design. No new human gate.

### Next action

**Architect:** start `contract-design` now. Specify request/response and store contracts for U2 `issue-enrollment` and `enqueue-compatible-job`, and the U3 FlightRecorder-write → Learning Runtime → LearningCapture call. Reuse brownfield routes/modules; do not add a fifth unit. Optionally note the catalogue lag; do not reopen units-generation.

**Developer:** wait for contract-design. Do not implement U2/U3 callers from the stale `components.md` responsibility list.

## Review (iteration 1)

**Verdict:** READY
**Reviewer:** aidlc-architecture-reviewer-agent
**Date:** 2026-08-18T12:30:44Z
**Iteration:** 1

### Findings

| # | Severity | Location | Finding | Recommendation |
|---|---|---|---|---|
| 1 | Major | `unit-of-work.md` U2/U3; `components.md` EdgeFleetHttpApi / LearningCapture; domain-design review findings 1–2 | U2 owns FR2 (“enroll, take a job, finish”) and U3 owns FR3.1 (“record evidence and yield a candidate”), but the assigned component lists still omit the operators those increments need. EdgeFleetHttpApi is only enroll/heartbeat/lease/complete plus revoke — no issue-enrollment or enqueue. LearningCapture has `dependents: []` and SharedRuntime does not call it. Units-generation restated the outcomes without boxing the missing work, so construction of U2/U3 still has to invent callers. | On U2, name operator issue-enrollment and enqueue-compatible-job as in-unit work (HTTP or store). On U3, name the existing learning-runtime or turn-pipeline caller that invokes LearningCapture after the FlightRecorder write. |
| 2 | Minor | `unit-of-work-story-map.md` | Stage Step 6 asks for implementation order within each unit. The map assigns every FR but does not order work inside U1–U4. | Add a short per-unit FR order (or “no intra-unit order”) so 2.9 is not left to infer it. |
| 3 | Minor | `unit-of-work.md` SharedRuntime on U1–U4; `parseBoltDag` batch 1 | `fleet-hygiene`, `learning-promotion`, and `handshake` are a valid parallel set and all edit SharedRuntime (flag-off, visible-delivery hook, mesh start) with no file-level ownership. A swarm fan-out of batch 1 will collide on the composition root. | Leave the DAG as-is (Q3). In 2.9, serialize SharedRuntime edits or list the files each unit may touch. |
| 4 | Minor | `traceability.json` + `unit-of-work-story-map.md` | The official traceability sensor reports 33 gaps and “no story-to-unit mappings” because `storyAssignments` only extracts `USx.y`. There is no `stories.md`; the map correctly uses FR IDs and every FR is on a row with U1–U4. | Treat the sensor fail as a parser mismatch, not missing coverage. Do not invent US IDs. |

### Validation Tool Results

| Tool | Result | Interpretation |
|---|---|---|
| `aidlc-sensor-required-sections` (`unit-of-work.md`) | PASS — 6 H2s | Required section floor met. |
| `aidlc-sensor-required-sections` (`unit-of-work-dependency.md`) | PASS — `edge_block: ok` | Fenced yaml present, well-formed, acyclic. |
| `aidlc-sensor-required-sections` (`unit-of-work-story-map.md`) | PASS — 4 H2s | Required section floor met. |
| `aidlc-sensor-upstream-coverage` | PASS — consumes `components`, `decisions`, `requirements`, `stories` all referenced | `stories` is cited as absent; optional consume satisfied. |
| `aidlc-sensor-traceability` | FAIL — 33 extraGaps, 31 invalid_targets, reason: story-map has no US mappings | Confirms finding #4. Manual FR join is complete (33/33 IDs; every OK/Deferred/N/A target is U1–U4 or the FR3.6 process note). |
| `parseBoltDag` | PASS — units `fleet-hygiene`, `live-fleet`, `learning-promotion`, `handshake`; one edge `live-fleet → fleet-hygiene`; batches `[{fleet-hygiene, handshake, learning-promotion}, {live-fleet}]` | Names match the Unit Index. No self-deps, no dangling deps, no cycle. No recommended build order in the dependency file. |

### Summary

Four units, kinds, embed shape, hygiene≠live-fleet, parallel learning/handshake, and no excluded-agent unit all match Q1–Q5; the DAG is a single acyclic edge. The gate should weigh the inherited U2/U3 operator gap (token/enqueue and the LearningCapture caller) before Construction, not a broken unit graph.
