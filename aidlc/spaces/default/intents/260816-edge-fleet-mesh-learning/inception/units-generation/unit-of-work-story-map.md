# Unit to Requirement Map

There is no `stories` artifact.
Rows map `FR` IDs from `requirements.md` onto Unit IDs from `unit-of-work.md`.

## Assignment

| Upstream | Unit ID | Directory | Notes |
|----------|---------|-----------|--------|
| FR1 | U1 | `u1-fleet-hygiene` | Containment hygiene |
| FR1.1 | U1 | `u1-fleet-hygiene` | Operator revoke |
| FR1.2 | U1 | `u1-fleet-hygiene` | Tombstone after revoke |
| FR1.3 | U1 | `u1-fleet-hygiene` | Tailnet on every fleet op |
| FR1.4 | U1 | `u1-fleet-hygiene` | Helper fails the op |
| FR1.5 | U1 | `u1-fleet-hygiene` | Allowlist fail-closed |
| FR1.6 | U1 | `u1-fleet-hygiene` | Remove unused HTTP allowlist param |
| FR1.7 | U1 | `u1-fleet-hygiene` | Rotate demo key |
| FR2 | U2 | `u2-live-fleet` | Live worker increment — includes `issue-enrollment` and `enqueue-compatible-job` |
| FR2.1 | U2 | `u2-live-fleet` | Operator start command |
| FR2.2 | U2 | `u2-live-fleet` | OpenClaw worker path (needs token + compatible job) |
| FR2.3 | U2 | `u2-live-fleet` | Hermes worker path (needs token + compatible job) |
| FR2.4 | U2 | `u2-live-fleet` | Both runtimes required |
| FR2.5 | U2 | `u2-live-fleet` | No orchestrator supervisor |
| FR3 | U3 | `u3-learning-promotion` | Live promotion |
| FR3.1 | U3 | `u3-learning-promotion` | Candidate after visible reply — `LearningCapture caller` is Learning Runtime |
| FR3.2 | U3 | `u3-learning-promotion` | No demo-only origin |
| FR3.3 | U3 | `u3-learning-promotion` | Dual-root canary |
| FR3.4 | U3 | `u3-learning-promotion` | Dual-root stable + reviewer |
| FR3.5 | U3 | `u3-learning-promotion` | Keep Agno flag off — config, not a slice |
| FR3.6 | U3 | `u3-learning-promotion` | Process note |
| FR4 | U4 | `u4-handshake` | Handshake increment |
| FR4.1 | U4 | `u4-handshake` | Inspect local OpenClaw |
| FR4.2 | U4 | `u4-handshake` | Implement if surface exists |
| FR4.3 | U4 | `u4-handshake` | Defer if no surface |
| FR4.4 | U4 | `u4-handshake` | Wire mesh switch if in |
| FR4.5 | U4 | `u4-handshake` | Remove switch if deferred |
| FR5 | U2 | `u2-live-fleet` | Single activation status on health |
| FR5.1 | U2 | `u2-live-fleet` | Health is source of truth |
| FR5.2 | U2 | `u2-live-fleet` | Docs quote health |
| FR6 | U1 | `u1-fleet-hygiene` | Rollback |
| FR6.1 | U1 | `u1-fleet-hygiene` | Flag off |
| FR6.2 | U1 | `u1-fleet-hygiene` | Written cleanup |

## Intra-unit FR order

No intra-unit FR is a contract blocker. Construction still uses this order so 2.9 / slice planning does not invent one.

| Unit | Order | Why |
|------|-------|-----|
| U1 | FR1.5 → FR1.6 → FR1.1 → FR1.2 → FR1.3 → FR1.4 → FR1.7 → FR6.1 → FR6.2 | Store fail-closed and unused HTTP param before reachable revoke; tailnet on node ops after tombstone/requeue; demo-key and rollback last |
| U2 | FR2.1 → issue-enrollment (FR2) → enqueue-compatible-job (FR2) → FR2.2 → FR2.3 → FR2.4 → FR2.5 → FR5.1 → FR5.2 | Documented start, then token, then job, then both runtimes finish, then health sentence |
| U3 | FR3.1 → FR3.2 → FR3.3 → FR3.4; FR3.5 config-stays-off (not a slice); FR3.6 process note | Evidence + caller first; canary/stable later in-unit; Agno flag is config |
| U4 | FR4.1 inspect → (FR4.2+FR4.4 bind) XOR (FR4.3+FR4.5 remove) | Closed alternative; inspection decides which branch |

## SharedRuntime file ownership (serialize; do not fan-out)

Leave the DAG as-is. These files are one composition root. Slice owners may touch only the listed symbols. Later slices wait if a prior slice still has an open edit.

| File | U1 (slice 1) | U2 | U3 | U4 |
|------|--------------|----|----|----|
| `src/mindroom/api/main.py` | `_edge_fleet_from_runtime_paths` / fleet mount and flag-off (FR6.1) | `GET /api/health` `edge_fleet` fragment + one activation-status sentence (C6) | none | none |
| Visible-delivery / turn-pipeline writer (brownfield: not `api/main.py`) | none | none | `record_flight_event` / FlightRecorder write after successful visible reply | none |
| Mesh start path (`src/mindroom/mesh/gateway.py` + unread `MINDROOM_MESH_ENROLLMENT` / `config/mesh.py`) | none | none | none | inspect-or-remove (C8) |

Do not invent `USx.y` story IDs. Coverage stays on FR IDs.

## Cross-cutting

- SharedRuntime appears in U1 (flag-off), U2 (health), U3 (visible-delivery hook), and U4 (mesh start).
  That is one process, four slices, not four deployables.
- EdgeFleetHttpApi is changed in U1 (revoke / hygiene) and used in U2 (`issue-enrollment`, enroll/heartbeat/lease/complete, and `enqueue-compatible-job` if exposed over HTTP).
- Learning Runtime is the U3 `LearningCapture caller`; SharedRuntime still only writes FlightRecorder.

## Coverage

Every FR in `requirements.md` is assigned.
Every unit has at least one FR.
No user stories exist to leave unassigned.
Operators added in U2/U3 are absorbed by existing FR2/FR3 rows; no fifth unit.

## Assumptions & Open Questions

None.
