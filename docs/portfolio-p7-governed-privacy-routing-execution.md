# P7 Execution — Governed Privacy Routing over the Live System

**Date:** 2026-08-09
**Author:** AI-DLC Developer (@mindroom_aidlc_developer:localhost)
**Directive:** P7 EXECUTION — instantiate site-configured executors, bind to the architect's route mapping, run the live route demo end-to-end over the real system, and commit scoped evidence.

---

## 1. Instantiation Confirmation

Site-configured executors were instantiated and bound to the P7 governed privacy-routing route mapping:

| Executor binding | Route(s) | Concrete adapter | Live status |
|------------------|----------|------------------|-------------|
| `ollama-local` | `local-private` | `OpenAICompatibleModelExecutor` → loopback Ollama `http://127.0.0.1:11434/v1`, model `llama3.2:3b` | ✅ live |
| `hermes-cloud` | `cloud-public` | Hermes CLI cloud model adapter (`/Users/dwayne/.local/bin/hermes`) | ✅ live |
| `docker-transform` | `isolated-transform` | `DockerJsonToolExecutor` → pinned `python@sha256:6d43...` image, `--network=none`, read-only root, cap-drop=ALL, no-new-privileges, UID 65534 | ✅ live |

Composition used `build_configured_privacy_dispatcher` with `ConfiguredPrivacyExecutors(models=…, tools=…)`, which **refuses** disabled, missing, ambiguous, or wrong-kind executor bindings at startup. All declared routes were bound to exactly one kind-matching live executor before any dispatch.

## 2. Live Route Demo Receipt

Each request carried the complete sensitivity/residency/capability/budget/isolation constraints. The router selected the least-cost, least-privileged eligible route; the dispatcher bound the exact request digest to that route in SQLite and never substituted after failure/timeout.

| Request id | Route selected | Executor | Request → Response evidence | Status |
|------------|----------------|----------|------------------------------|--------|
| `live-local-restricted` | `local-private` | ollama-local | prompt `"Reply with exactly PRIVACY_LOCAL_OK"` → `PRIVACY_LOCAL_OK` present | ✅ completed |
| `live-cloud-public` | `cloud-public` | hermes-cloud | prompt `"Reply with exactly PRIVACY_CLOUD_OK"` → `PRIVACY_CLOUD_OK` present | ✅ completed |
| `live-isolated-tool` | `isolated-transform` | docker-transform | `{"values":[3,1,2]}` → `{"normalized":[1,2,3]}` (network=none) | ✅ completed |
| `denied-restricted-cloud` | — (denied) | — | restricted cloud-only request rejected **before execution** (`RoutingError`) | ✅ denied |
| `selected-cloud-failure` | `cloud-public` | hermes-cloud | selected cloud failure durably recorded as `failed`; **no local fallback** (`local_fallback_calls=0`) | ✅ failed-recorded |

## 3. Test Result

- **Unit tests:** `tests/test_privacy_{composition,dispatch,executors,handlers,router}.py` — all passed.
- **Live demo:** `scripts/privacy_routing_demo.py --scratch-root "$PWD"` → exit 0, all 5 scenarios passed, temporary dispatch ledger closed and removed.

## 4. External Blocker?

**None.** The full live demo completed end-to-end over the real system (loopback Ollama, Hermes cloud, and the pinned offline Docker image were all available and live).

## 5. Committed Evidence

- `docs/p7_live_route_demo_evidence.log`
- `docs/p7_live_route_demo_receipt.json`