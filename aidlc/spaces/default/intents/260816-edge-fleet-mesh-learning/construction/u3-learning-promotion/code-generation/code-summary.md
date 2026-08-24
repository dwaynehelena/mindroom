# Code Summary — U3 learning-promotion (slice 3)

**Date:** 2026-08-20  
**Unit:** U3 `u3-learning-promotion`  
**Contracts:** C7  
**FRs:** FR3.1, FR3.2, FR3.5 (config stays off); FR3.3/FR3.4 later in unit, not this call  
**Operator:** `LearningCapture caller`

## What landed

Brownfield P10 learning modules were missing from this checkout (`release/2026.8.79`) and were restored from commit `65508f0f0`, then wired to the visible-delivery path. SharedRuntime writes FlightRecorder only. Existing Learning Runtime (`capture_visible_reply_learning_event` → `capture_runtime_learning_event` → `capture_learning_candidate`) is the named LearningCapture caller. LearningCapture stays propose policy. No fifth unit, no new HTTP, no `main.py` duplication.

| Change | File | Why |
|--------|------|-----|
| Restore FlightRecorder + Learning Runtime/Capture/Loop | `src/mindroom/flight_recorder.py`, `learning_*.py` | Brownfield C7 modules |
| Restore capture deps used by restored tests | `src/mindroom/skill_registry.py`, `skill_sandbox.py`, `provenance_*.py` | Import graph for restored P10 tests |
| Demo-script-only origin fail-closed | `src/mindroom/learning_capture.py` | FR3.2 |
| Learning Runtime caller after visible reply | `src/mindroom/learning_runtime.py` (`capture_visible_reply_learning_event`) | C7 operator |
| Visible-delivery FlightRecorder write then caller | `src/mindroom/post_response_effects.py` | ADR-005 / FR3.1 |
| Tach deps for the writer | `tach.toml` | Runtime import of recorder + learning runtime |
| Slice tests | `tests/test_learning_promotion.py` | C7 |

OpenClaw Agno learning remains `learning: false` in `config.yaml` (FR3.5). Canary/stable dual-root (FR3.3/FR3.4) is later in unit, not this call.

## Not in this slice

- C8 inspect-or-remove (U4)
- Live Tailscale or full-suite verification
- Canary both roots / stable both roots with named reviewer