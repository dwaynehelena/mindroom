# Construction Kickoff — edge-fleet-mesh-learning

**Date:** 2026-08-20  
**Author:** AI-DLC Architect  
**Authorization:** standing human approval from `@dwayne:localhost` (Lobby `$dmYc1X2PeNmUkIzAX8vh8EanGyk2c1h6nrA-9YSpcGc`). Do not wait for another human review.  
**Status:** kickoff written. Construction **has not started** (no 3.1–3.5 unit artifacts, no code edits).

Operators in `unit-of-work.md` beat `components.md`. Do not invent a fifth unit, a new credential (NFR3), or public REST for learning/mesh.

## Recovered artifacts

| Artifact | Path |
|----------|------|
| Contract summary C1–C8 | `aidlc/spaces/default/intents/260816-edge-fleet-mesh-learning/inception/contract-design/contract-summary.md` |
| Contract Q&A | `…/inception/contract-design/contract-design-questions.md` |
| Contract traceability | `…/inception/contract-design/traceability.json` |
| Units | `…/inception/units-generation/unit-of-work.md` |
| DAG | `…/inception/units-generation/unit-of-work-dependency.md` |
| Story map + cheap minors | `…/inception/units-generation/unit-of-work-story-map.md` |
| Requirements | `…/inception/requirements-analysis/requirements.md` |
| Domain catalogue (lag) | `…/inception/domain-design/components.md` |
| ADRs | `…/inception/domain-design/decisions.md` |
| Brownfield API map | `aidlc/spaces/default/codekb/mindroom/api-documentation.md` |
| Brownfield architecture | `aidlc/spaces/default/codekb/mindroom/architecture.md` |
| Brownfield code structure | `aidlc/spaces/default/codekb/mindroom/code-structure.md` |

Not recovered: Architecture Reviewer’s numbered five-minor list as a `## Review` section on `contract-summary.md`. Cheap documented minors applied below; none delay slice 1.

## Slice order

DAG (`unit-of-work-dependency.md`): one edge `live-fleet → fleet-hygiene`. Parallel set `{fleet-hygiene, learning-promotion, handshake}` is valid topologically. **Do not fan-out batch 1** — SharedRuntime is one composition root (story-map ownership table).

| Slice | Unit | Contracts | Ships | Depends on |
|-------|------|-----------|-------|------------|
| **1** | U1 `u1-fleet-hygiene` | C1, C2 (tailnet on C5 handlers owned by U1) | Containment. Done when revoke + fail-closed work, **not** when a worker finishes a job | none |
| 2 | U2 `u2-live-fleet` | C3, C4, C5, C6 | Increment that counts. Sign-off after this unit | slice 1 |
| 3 | U3 `u3-learning-promotion` | C7 | Visible reply → FlightRecorder → Learning Runtime → LearningCapture `proposed` | none hard; serialize after slice 1 if touching shared files |
| 4 | U4 `u4-handshake` | C8 | Inspect local OpenClaw; bind XOR remove unread mesh switch | none hard; serialize mesh files; inspection may shrink the unit |

AIDLC engine next stage remains **delivery-planning (2.9)** for `bolt-plan.md`. This kickoff is the implementation sequence the Developer uses immediately. Do not start U2 code until slice 1 acceptance passes.

---

## Slice 1 — U1 fleet-hygiene

### Scope (in)

- **C1 `revoke`:** signed-in operator `DELETE /api/edge-fleet-admin/nodes/{node_id}` → `204` (existed or already revoked) or `404` (never existed). After U1: **not** permissions `403`. ADR-002 / FR1.1.
- **C2 store:** tombstone `revoked_at`; requeue that node’s `leased` jobs to `queued` and clear lease columns; allowlist `None` denies enroll; unused HTTP `node_allowlist` parameter **removed** (FR1.6).
- **Tailnet fail-closed on node ops** (FR1.3, FR1.4, ADR-003): `enroll` / `heartbeat` / `lease` / `complete` call `TailscaleConnectivityCheck` and refuse when the check fails. Missing tailnet is **not** a new revoke error.
- **FR6.1:** flag-off unmounts or refuses **new** enroll/lease. In-flight complete residual is unspecified in requirements (minor); do not invent a new complete-after-flag-off protocol in slice 1. Written cleanup is FR6.2 (procedure, later in U1).
- **FR1.7:** rotate leftover demo administrative key out of the repo (procedure; not a payload).

### Scope (out)

- `issue-enrollment`, `enqueue-compatible-job`, worker start, health sentence (U2 / C3–C6).
- LearningCapture caller (U3 / C7).
- Mesh inspect/bind/remove (U4 / C8).
- New routes, new credential, new unit.
- Implementing stale `components.md` EdgeFleetHttpApi / LearningCapture responsibility lists.

### Files / paths (brownfield; restore if missing)

| Path | Role in slice 1 |
|------|-----------------|
| `src/mindroom/api/edge_fleet.py` | Drop `_require_revoke_permission` / `admin.nodes.revoke`. Remove unused `node_allowlist` on `create_edge_fleet_router`. Call tailnet check on node handlers. |
| `src/mindroom/edge_fleet.py` | Store invariants C2: tombstone, requeue, allowlist fail-closed, audit. Do not add Tailscale here. |
| `src/mindroom/edge_tailscale.py` | Helper must fail the op (raise / refuse), not log-and-continue. |
| `src/mindroom/api/main.py` | Flag-gated mount `_edge_fleet_from_runtime_paths` (FR6.1). **Do not** change health sentence (U2) or mesh start (U4). |
| `src/mindroom/api/auth.py` | Read only. `verify_user` stays identity-only. No new auth scheme. |
| `tests/api/test_edge_fleet_revocation_api.py` | Signed-in operator → 204/404, not 403. |
| `tests/api/test_edge_fleet_revocation_core.py` | Tombstone + requeue. |
| `tests/api/test_edge_fleet_api.py` / `tests/api/test_edge_fleet_security.py` / `tests/test_edge_fleet.py` | Allowlist unused-param gone; store effect asserted. Tailnet refuse on node ops. |
| `edge_fleet_cross_device_demo.py` and any committed demo bearer | FR1.7 rotate/remove leftover demo key. |

### Acceptance checks (slice 1)

Run against the local install / existing pytest suite (NFR5: stay green; no new coverage floor).

1. **Revoke reachable:** authenticated dashboard user, no `permissions` field, `DELETE /api/edge-fleet-admin/nodes/{node_id}` → `204` if the node existed or is already revoked; `404` if it never existed; `401` if `verify_user` fails; `429` if more than 5 revokes / 60s / principal. **No `403`.**
2. **Tombstone:** after `204`, that `node_id` is refused on enroll, heartbeat, lease, complete (`revoked_at IS NOT NULL`).
3. **Requeue:** that node’s `leased` rows become `queued` with lease columns cleared; a non-revoked worker can lease them.
4. **Allowlist:** `node_allowlist is None` denies every enroll. HTTP layer no longer accepts/advertises the unused allowlist parameter; tests assert the **store** effect.
5. **Tailnet:** failing or absent Tailscale helper refuses enroll/heartbeat/lease/complete. Direct store tests may still bypass; API tests must not.
6. **Flag-off:** `MINDROOM_EDGE_FLEET_ENABLED` missing/false or enrollment key missing/short → routers unmounted (or equivalent refuse of new enroll/lease). No new public bind (NFR2).
7. **NFR3:** still HMAC enrollment token + Ed25519 per-request attestation. No new credential type.
8. **NFR4:** audit/log evidence for revoke success and deny-by-allowlist (existing `edge_fleet_audit`).
9. **NFR5:** existing suite green.

Do **not** require an OpenClaw/Hermes worker to finish a job for slice 1.

---

## Later slices (do not implement in this run)

**Slice 2 U2** — brownfield `POST /api/edge-fleet-admin/enrollments` (C3), `POST /api/edge-fleet-admin/jobs` or `queue_job` (C4), node path enroll→heartbeat→lease→complete (C5), health `edge_fleet` + one operator-quotable sentence (C6). Finish = attested `POST /api/edge-fleet/complete` → `204`, not process exit. MindRoom does not start workers. Documented start command per runtime (OpenClaw and Hermes). C6 exact sentence wording may stay TBD; HTTP shape is pinned.

**Slice 3 U3** — SharedRuntime/turn pipeline writes FlightRecorder after successful visible reply; existing `learning_runtime` / `GovernedLearningRuntime` calls LearningCapture; propose policy only; demo-script-only origin yields no candidate; Agno learning for OpenClaw-named agent stays off.

**Slice 4 U4** — inspect local OpenClaw (not only the MindRoom extension point). Surface found → bind existing `Callable[[], None]`; surface absent → do not implement FR4.2 and remove unread `MINDROOM_MESH_ENROLLMENT` switch.

---

## Developer handoff (exact)

Copy this block. Implement **slice 1 only**.

```text
You are the AIDLC Developer for project edge-fleet-mesh-learning
(intent 260816-edge-fleet-mesh-learning).

AUTHORIZED: contract-design gate APPROVED 2026-08-20 by standing human
approval from @dwayne:localhost. Do not wait for another human review.
Do not ask Architect or Dwayne for further approval of this slice.

READ FIRST (operators beat components.md):
- aidlc/.../inception/contract-design/contract-summary.md  (C1–C8)
- aidlc/.../inception/units-generation/unit-of-work.md
- aidlc/.../inception/units-generation/unit-of-work-dependency.md
- aidlc/.../inception/units-generation/unit-of-work-story-map.md
- aidlc/.../construction/construction-kickoff.md   (this kickoff)
- aidlc/spaces/default/codekb/mindroom/api-documentation.md
- aidlc/spaces/default/codekb/mindroom/architecture.md
- aidlc/spaces/default/codekb/mindroom/code-structure.md

THIS RUN = SLICE 1 ONLY (U1 fleet-hygiene, C1+C2, FR1, FR6.1).
Done when containment works, not when a worker finishes a job.

IN:
- DELETE /api/edge-fleet-admin/nodes/{node_id} reachable for signed-in
  local operator (verify_user). Drop unused admin.nodes.revoke check
  (ADR-002). 204 idempotent / 404 never-existed / 401 / 429. No 403.
- Store: tombstone + requeue leased jobs; allowlist None denies enroll;
  remove unused HTTP node_allowlist param (assert store effect).
- Tailnet fail-closed on enroll/heartbeat/lease/complete (not on revoke).
- Flag-off unmounts or refuses new enroll/lease.
- Rotate leftover demo admin key (FR1.7) if present.

OUT:
- issue-enrollment, enqueue-compatible-job, worker start, health sentence
- LearningCapture caller, mesh handshake
- new unit, new credential, new public REST
- components.md stale responsibility lists

FILES: src/mindroom/api/edge_fleet.py, edge_fleet.py, edge_tailscale.py,
api/main.py (mount/flag-off only), matching tests under tests/ and tests/api/.
Do not edit health sentence or mesh start in api/main.py.

IF brownfield .py sources are missing (pycache-only): STOP and restore
them from git / last good tree before coding. Do not re-invent the fleet
store or routers from the OpenAPI sketches.

ACCEPTANCE: kickoff "Acceptance checks (slice 1)". NFR5 stay green.

RETURN: files changed, tests run, leftover risks. Do not start slice 2.
```

---

## Minors applied this run (cheap; did not delay slice 1)

| Source | Action |
|--------|--------|
| Units-generation minor 2 — intra-unit FR order | Added to `unit-of-work-story-map.md` |
| Units-generation minor 3 — SharedRuntime file ownership | Added serialization table to `unit-of-work-story-map.md`; DAG unchanged |
| Units-generation minor 4 — `USx.y` sensor | Standing rule restated; **no invented story IDs** |
| Catalogue lag in `components.md` | Not rewritten (would delay slice 1 / reopen domain-design). Construction uses Operators. |
| C6 sentence wording, C8 inspect outcome | Open questions; not slice 1 |

Architecture Reviewer’s five numbered contract-design minors were **not on disk**. Nothing else was rewritten “just in case.”

---

## Blockers

| # | Severity | Blocker | Who |
|---|----------|---------|-----|
| B1 | **Real** | Brownfield fleet/learning/mesh **`.py` sources are missing** from this workspace. `code-structure.md` names them; `__pycache__` exists (`edge_fleet.cpython-313.pyc`, `api/edge_fleet.cpython-313.pyc`, `mesh/*.pyc`); current trees do not contain `src/mindroom/edge_fleet.py`, `src/mindroom/api/edge_fleet.py`, `src/mindroom/edge_node.py`, `src/mindroom/edge_tailscale.py`, `src/mindroom/flight_recorder.py`, `src/mindroom/learning_*.py`, `src/mindroom/config/mesh.py`, or `tests/api/test_edge_fleet_*.py`. `src/mindroom/api/main.py` currently has **no** `edge_fleet` mount. | Developer must restore from git before slice 1 code. Kickoff does not reconstruct those modules. |
| B2 | Process | AIDLC `aidlc-state.md` still listed contract-design `[?]` before this kickoff. Closed in state by this run. Full Bolt plan is still **delivery-planning (2.9)** if the engine is used. | Delivery / engine |
| B3 | Non-blocking | C6 exact activation-status sentence; C8 local OpenClaw surface. | Slice 2 docs / slice 4 inspect |

No approval blocker. No architecture blocker for C1–C8.

## Construction status

- Kickoff artifact: this file.
- Slice 1 code started 2026-08-20 by AI-DLC Developer: brownfield fleet modules restored from `65508f0f0` and patched (revoke, tailnet fail-closed, unused HTTP allowlist removed, flag-off mount, demo key rotated). See `construction/u1-fleet-hygiene/code-generation/`.