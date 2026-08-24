# Code Summary — U4 handshake (slice 4)

**Date:** 2026-08-20  
**Unit:** U4 `u4-handshake`  
**Contracts:** C8  
**FRs:** FR4.1 inspect, FR4.3 defer, FR4.5 remove unread switch (XOR vs FR4.2+FR4.4)  
**Operator:** `inspect-openclaw`

## Inspection (FR4.1)

Local OpenClaw is installed (`openclaw` 2026.7.1-2 at `/Users/dwayne/.npm-global/bin/openclaw`). The install exposes DM pairing (`openclaw pairing`), device/node pairing (`openclaw devices` / `openclaw nodes`), and a Gateway WebSocket `connect` handshake. Those are not a nullary `Callable[[], None]` mesh-enrollment handshake for `MeshEnrollmentCoordinator.handshake`.

MindRoom extension point: `src/mindroom/mesh/` is pycache-only in this checkout; `mindroom.config.mesh` is absent from the current `Config` model; `MINDROOM_MESH_ENROLLMENT` is not in `.env` / `.env.example` / `config.yaml`. No existing bindable handshake callable.

C8 closed alternative: **surface_absent**. Do not implement FR4.2. Do not start MeshGateway (ADR-006 is conditional on inspection). Keep the unread switch removed (FR4.5).

## What landed

| Change | File | Why |
|--------|------|-----|
| Operator `inspect-openclaw` | `src/mindroom/handshake_inspect.py` | C8 / FR4.1; in-process, no new HTTP |
| Slice tests | `tests/test_handshake_inspect.py` | C8 outcomes + FR4.5 absence |
| This summary | `construction/u4-handshake/code-generation/` | U4 construction artifact |

No MeshGateway start. No `main.py` duplication. No fleet token or job-queue operators. Brownfield mesh sources were **not** restored because the inspect branch is remove, not bind.

## Not in this slice

- FR4.2 live OpenClaw gateway enrollment handshake
- ADR-006 MeshGateway composition-root start
- Live Tailscale or full-suite verification
- Quality/NFR phase