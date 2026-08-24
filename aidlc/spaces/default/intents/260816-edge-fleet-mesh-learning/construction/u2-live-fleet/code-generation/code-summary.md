# Code Summary — U2 live-fleet (slice 2)

**Date:** 2026-08-20  
**Unit:** U2 `u2-live-fleet`  
**Contracts:** C3–C6 (C2 consumer)  
**FRs:** FR2.1–FR2.5, FR5.1–FR5.2  
**Operators:** `issue-enrollment`, `enqueue-compatible-job`

## What landed

Brownfield admin HTTP and store already implemented C3 (`POST /api/edge-fleet-admin/enrollments` / `issue_enrollment`) and C5 node path. U2 completed the live increment on those surfaces plus C4 HTTP mapping, C6 health, and documented operator start commands. No fifth unit, no new credential, no worker supervisor.

| Change | File | Why |
|--------|------|-----|
| C6 `edge_fleet` fragment + one activation-status sentence on `GET /api/health` | `src/mindroom/api/main.py` | FR5.1 / ADR-007 |
| C4 invalid job → 422; equivocation → 409 | `src/mindroom/api/edge_fleet.py` | C4 responses |
| Operator start command for OpenClaw and Hermes | `src/mindroom/edge_node.py` (`python -m mindroom.edge_node`) | FR2.1, FR2.5, ADR-004 |
| Docs quote health; start commands | `docs/edge-fleet.md`, `docs/edge-fleet-rollback.md` | FR5.2, FR2.1 |
| Slice tests | `tests/api/test_edge_fleet_live.py`, `tests/test_edge_node.py` | C3–C6 |

C3 and C5 reuse brownfield routers. C2 consumer: enroll/heartbeat/lease/complete still honor U1 tombstone, allowlist, and tailnet fail-closed.

## Not in this slice

- Live Tailscale or full-suite verification
- C7 LearningCapture caller (U3)
- C8 inspect-or-remove (U4)
- MindRoom starting workers