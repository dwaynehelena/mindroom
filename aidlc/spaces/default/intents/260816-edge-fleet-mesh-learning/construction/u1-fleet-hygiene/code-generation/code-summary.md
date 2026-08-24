# Code Summary — U1 fleet-hygiene (slice 1)

**Date:** 2026-08-20  
**Unit:** U1 `u1-fleet-hygiene`  
**Contracts:** C1, C2 (tailnet on C5 node handlers owned by U1)  
**FRs:** FR1.1–FR1.7, FR6.1, FR6.2

## What landed

Brownfield fleet sources were missing from this checkout (`release/2026.8.79`) and were restored from commit `65508f0f0`, then patched in place. No fifth unit, no new credential, no health-sentence or mesh-start edits.

| Change | File | Why |
|--------|------|-----|
| Drop unused `admin.nodes.revoke` check | `src/mindroom/api/edge_fleet.py` | FR1.1 / ADR-002 / C1: signed-in operator gets 204/404, not 403 |
| Remove unused HTTP `node_allowlist` param | `src/mindroom/api/edge_fleet.py` | FR1.6; store remains source of truth |
| Tailnet fail-closed on enroll/heartbeat/lease/complete | `src/mindroom/api/edge_fleet.py`, `src/mindroom/edge_tailscale.py` | FR1.3, FR1.4, ADR-003 |
| Store tombstone + requeue + allowlist `None` denies enroll | `src/mindroom/edge_fleet.py` (restored; docstring only) | C2, FR1.2, FR1.5 |
| Flag-off unmount | `src/mindroom/api/main.py` | FR6.1 |
| Rotate leftover demo bearer | `edge_fleet_cross_device_demo.py` | FR1.7 — reads `MINDROOM_API_KEY` |
| Written cleanup | `docs/edge-fleet-rollback.md` | FR6.2 |

## Not in this slice

- C3 `issue-enrollment`, C4 `enqueue-compatible-job`, C5 worker start, C6 health sentence (U2)
- C7 LearningCapture caller (U3)
- C8 inspect-or-remove (U4)