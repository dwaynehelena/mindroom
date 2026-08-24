# Contract Design — Questions

## Sources

- `unit-of-work.md` Operators fields (source of truth)
- `unit-of-work-dependency.md` DAG and integration points
- `requirements.md` FR1–FR6, NFR1–NFR6
- `components.md` entity shapes only — catalogue lag is not the construction contract
- Brownfield `api-documentation.md` / `architecture.md` / `component-inventory.md`

These questions pin boundaries. They do not reopen units-generation.
Time-boxed directive: do not wait. Answers inferred from approved units-generation (2026-08-19, Matrix `$WJzrB7TuRKmYqqGaZ5uLz0O37dOadhNTK7zsp4LqIsc`), reviewer iteration 2 READY, and brownfield routes.

## Q1. Which surfaces are public/external APIs consumed outside the MindRoom process?

- A. U1 revoke plus U2 `issue-enrollment`, `enqueue-compatible-job`, node enroll/heartbeat/lease/complete, and health — all existing admin/node/health HTTP. U3 and U4 stay in-process.
- B. Add a new public REST for learning propose/review and mesh admit
- C. Store-only for token issue and enqueue; no HTTP operators
- X. Other (please specify)

[Answer]: A. U1 revoke plus U2 `issue-enrollment`, `enqueue-compatible-job`, node enroll/heartbeat/lease/complete, and health — all existing admin/node/health HTTP. U3 and U4 stay in-process.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Q2. What integration mechanism applies at each unit boundary?

- A. U1↔U2 shared process + SQLite + same HTTP routers. U2 operators are sync REST over existing `/api/edge-fleet-admin` and `/api/edge-fleet`. U3 is in-process call (FlightRecorder write then Learning Runtime → LearningCapture). U4 is in-process start/inspect.
- B. Async messaging between units
- C. New gRPC or a fifth HTTP surface
- X. Other (please specify)

[Answer]: A. U1↔U2 shared process + SQLite + same HTTP routers. U2 operators are sync REST over existing `/api/edge-fleet-admin` and `/api/edge-fleet`. U3 is in-process call (FlightRecorder write then Learning Runtime → LearningCapture). U4 is in-process start/inspect.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Q3. Who owns each spec?

- A. Provider unit owns the spec: U1 owns hygiene/revoke/flag-off; U2 owns issue-enrollment, enqueue-compatible-job, node path, and health sentence; U3 owns FlightRecorder-write → Learning Runtime → LearningCapture; U4 owns inspect-or-remove. No fifth unit.
- B. SharedRuntime owns every spec because all slices share one process
- C. Domain-design `components.md` owns the construction contract
- X. Other (please specify)

[Answer]: A. Provider unit owns the spec: U1 owns hygiene/revoke/flag-off; U2 owns issue-enrollment, enqueue-compatible-job, node path, and health sentence; U3 owns FlightRecorder-write → Learning Runtime → LearningCapture; U4 owns inspect-or-remove. No fifth unit.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Q4. What is the versioning and breaking-change policy?

- A. Reuse brownfield schemas (`mindroom.edge-enrollment/1`, `mindroom.edge-request/1`, `mindroom.edge-node/1`, `mindroom.edge-result/1`). No new credential type (NFR3). Additive fields only; consumers ignore unknown fields. Breaking change needs human gate.
- B. Mint new schema versions for this initiative
- C. Allow silent breaking changes inside a unit
- X. Other (please specify)

[Answer]: A. Reuse brownfield schemas (`mindroom.edge-enrollment/1`, `mindroom.edge-request/1`, `mindroom.edge-node/1`, `mindroom.edge-result/1`). No new credential type (NFR3). Additive fields only; consumers ignore unknown fields. Breaking change needs human gate.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Q5. What is the error, timeout, and retry behaviour?

- A. Fail-closed (NFR1). Admin issue/queue: 401 / 422 / 409 / 429 as brownfield. Node path: 401 auth, 409 store, complete 204. Denied enroll/lease is not retried into success. Tailnet miss refuses the node op. Local install; no cross-network retry budget.
- B. Retry denied enroll and missing tailnet until success
- C. Invent new error codes and a public retry protocol
- X. Other (please specify)

[Answer]: A. Fail-closed (NFR1). Admin issue/queue: 401 / 422 / 409 / 429 as brownfield. Node path: 401 auth, 409 store, complete 204. Denied enroll/lease is not retried into success. Tailnet miss refuses the node op. Local install; no cross-network retry budget.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Q6. How does U2 `issue-enrollment` land?

- A. Existing `POST /api/edge-fleet-admin/enrollments` → `EdgeFleet.issue_enrollment`. Signed-in operator. One-time HMAC token. Not a fifth unit.
- B. New credential type or a new admin route
- C. Store-only helper with no HTTP path
- X. Other (please specify)

[Answer]: A. Existing `POST /api/edge-fleet-admin/enrollments` → `EdgeFleet.issue_enrollment`. Signed-in operator. One-time HMAC token. Not a fifth unit.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Q7. How does U2 `enqueue-compatible-job` land, and what is “finish”?

- A. Existing `POST /api/edge-fleet-admin/jobs` or store `queue_job` for the named admitted runtime. Finish = fleet accepts that worker’s attested `POST /api/edge-fleet/complete` (204) for the lease.
- B. Finish means the worker process exits
- C. New job-queue service
- X. Other (please specify)

[Answer]: A. Existing `POST /api/edge-fleet-admin/jobs` or store `queue_job` for the named admitted runtime. Finish = fleet accepts that worker’s attested `POST /api/edge-fleet/complete` (204) for the lease.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Q8. Who is the U3 `LearningCapture caller`?

- A. Existing Learning Runtime (`learning_runtime` / `GovernedLearningRuntime`) after the SharedRuntime / turn-pipeline FlightRecorder write. LearningCapture stays propose policy. SharedRuntime does not call LearningCapture. No new ingest module.
- B. SharedRuntime calls LearningCapture directly
- C. A new ingest component
- X. Other (please specify)

[Answer]: A. Existing Learning Runtime (`learning_runtime` / `GovernedLearningRuntime`) after the SharedRuntime / turn-pipeline FlightRecorder write. LearningCapture stays propose policy. SharedRuntime does not call LearningCapture. No new ingest module.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Q9. How should `components.md` catalogue lag be treated?

- A. Note it. Do not reopen units-generation. Construction implements unit Operators, not the stale EdgeFleetHttpApi / LearningCapture responsibility lists.
- B. Block contract-design until domain-design is rewritten
- C. Treat `components.md` as source of truth and drop U2/U3 operators
- X. Other (please specify)

[Answer]: A. Note it. Do not reopen units-generation. Construction implements unit Operators, not the stale EdgeFleetHttpApi / LearningCapture responsibility lists.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Q10. Are the units-generation minors (no intra-unit FR order, SharedRuntime file ownership, `USx.y` IDs) contract blockers?

- A. Non-blocking. Do not invent story IDs. SharedRuntime file ownership is a delivery-planning serialization note. Intra-unit FR order is not required for implementable contracts.
- B. Block until all three minors close
- C. Invent `USx.y` IDs so the traceability sensor passes
- X. Other (please specify)

[Answer]: A. Non-blocking. Do not invent story IDs. SharedRuntime file ownership is a delivery-planning serialization note. Intra-unit FR order is not required for implementable contracts.
**Mode:** inferred (do-not-wait)
**Recorded:** 2026-08-19T12:15:00Z

## Consolidated Summary Confirmation

- Public/external HTTP is U1 revoke + U2 issue-enrollment, enqueue, node path, and health
- U1↔U2 is shared process/SQLite; U3/U4 are in-process
- Provider unit owns each spec; Operators beat `components.md`
- Reuse brownfield schemas; no new credential; fail-closed
- U2 `issue-enrollment` = `POST /api/edge-fleet-admin/enrollments`
- U2 `enqueue-compatible-job` = admin jobs / `queue_job`; finish = attested complete 204
- U3 caller = existing Learning Runtime after FlightRecorder write
- Catalogue lag noted, not reopened
- Minors non-blocking

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
**Mode:** inferred from time-boxed do-not-wait directive after units-generation approval
**Recorded:** 2026-08-19T12:15:00Z