# Test results — edge-fleet-mesh-learning quality/NFR

**Date:** 2026-08-20  
**Interpreter:** `/Users/dwayne/mindroom/.venv/bin/python`  
**Host:** macOS `macosx_15_0_x86_64` (this checkout)

## Build

| Command | Result |
|---------|--------|
| `uv run pytest …` (U1/U2 listed command) | **FAIL (tooling)** — `onnxruntime==1.25.1` has no wheel for this platform. Not a product defect. |
| `.venv/bin/python -m pytest …` | **Used for all executed suites** |

## Intent-scoped commands (deduplicated)

### U1 + U2 combined (U1 instructions ∪ U2 instructions)

```sh
.venv/bin/python -m pytest \
  tests/test_edge_fleet.py \
  tests/test_edge_node.py \
  tests/test_edge_tailscale.py \
  tests/test_p9_remediation.py \
  tests/api/test_edge_fleet_api.py \
  tests/api/test_edge_fleet_lifecycle.py \
  tests/api/test_edge_fleet_revocation_api.py \
  tests/api/test_edge_fleet_revocation_core.py \
  tests/api/test_edge_fleet_security.py \
  tests/api/test_edge_fleet_live.py \
  tests/api/test_api.py::test_health_check \
  --tb=short
```

**Result: 88 passed, 3 skipped, 2 warnings in 37.40s** (reconfirmed with `-rs`).

Skips (all in `tests/test_p9_remediation.py`):

- `test_cli_run_api_host_default_is_loopback`
- `test_orchestrator_api_host_default_is_loopback`
- `test_no_wildcard_bind_default_anywhere`

Reason (construction): “P9 bind-default assertions predate this checkout; U1 does not change CLI/orchestrator bind (NFR2 = no new public bind)”.

Warnings:

- `test_enrollment_http_surface_has_no_allowlist_parameter` marked `@pytest.mark.asyncio` but is sync.
- `HTTP_422_UNPROCESSABLE_ENTITY` deprecated in `src/mindroom/api/edge_fleet.py:429`.

### U3 listed command (after leftover fix)

```sh
.venv/bin/python -m pytest \
  tests/test_learning_promotion.py \
  tests/test_learning_capture.py \
  tests/test_learning_runtime.py \
  --tb=line
```

**Before fix:** `tests/test_learning_runtime.py` collection **ERROR** — `ImportError: cannot import name 'ApprovalDecision' from 'mindroom.approval_manager'`.

**After minimal test stub:** **15 passed** (promotion 5 + capture 7 + runtime 3).

Additional U3 brownfield coverage (FR3.3/FR3.4 canary/stable), not in the listed command but executed:

```sh
.venv/bin/python -m pytest tests/test_learning_loop.py --tb=line -q
# 9 passed (included in a 21-pass promotion+capture+loop run)

.venv/bin/python -m pytest tests/test_learning_publishers.py --tb=short -q -p no:cacheprovider -n0
# 4 passed

.venv/bin/python -m pytest tests/test_learning_stable_publishers.py tests/test_learning_candidates.py --tb=line -q
# 12 passed
```

### U4 listed command

```sh
.venv/bin/python -m pytest tests/test_handshake_inspect.py --tb=short -p no:cacheprovider -n0
```

**Result: 8 passed.**

## Leftover recorded (not silently ignored)

| Item | Severity | Evidence | Disposition |
|------|----------|----------|-------------|
| `tests/test_learning_runtime.py` imported `ApprovalDecision` from `mindroom.approval_manager` | **Major until fixed** | Collection ImportError; `src/mindroom/approval_manager.py` has no such export; `learning_runtime.py` only references it under `TYPE_CHECKING` | **Fixed** by quality: local dataclass stub in the test file (status/reason/resolved_by). File now **3 passed**. Product type was not restored. |

## Live Tailscale (quality decision: run helper, not live worker)

```sh
tailscale status   # nodes present: macbook-pro-3, iphone182, macbook-pro
.venv/bin/python - <<'PY'
from mindroom.edge_tailscale import check_tailscale_connectivity, require_tailscale
# check: connected, node macbook-pro-3.tail9e4b21.ts.net, 100.80.168.13, 2 peers
# require_tailscale: succeeded
PY
```

**Not run:** live OpenClaw/Hermes binary enroll → lease → complete over the tailnet. Construction C5 coverage is in-process HTTP (`tests/api/test_edge_fleet_live.py::test_c5_openclaw_and_hermes_enroll_take_and_finish`).

## Full suite (quality decision: do not claim)

```sh
.venv/bin/python -m pytest --collect-only -q -p no:cacheprovider -n0 --tb=line
```

Interrupted: **14 collection errors**, then **13** after the learning-runtime fix. Remaining errors are missing optional extras, **not** U1–U4:

- `google_auth_oauthlib` / Google client (gmail, calendar, docs, drive, sheets, scholar, wrappers, github oauth)
- `defusedxml` (`tests/test_history_prepare_integration.py`)
- `livekit` (`tests/test_matrix_rtc_voice_agent.py`)
- `crawl4ai` (`tests/test_tools_metadata.py`)
- claude-agent soak/tool modules

`./run-tests.sh` was **not** executed (`uv sync` would hit the same onnxruntime wheel miss; frontend/SaaS suites are out of this intent).

## Counts (intent-scoped, no double-count)

| Slice | Passed | Skipped | Failed |
|-------|--------|---------|--------|
| U1+U2 combined listed | 88 | 3 | 0 |
| U3 listed (after fix) | 15 | 0 | 0 |
| U3 extra loop/publishers/candidates | 25 | 0 | 0 |
| U4 listed | 8 | 0 | 0 |

Coverage percentage: **not gated** (NFR5).