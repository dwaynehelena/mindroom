---
title: Governed Privacy Routing
---

# Governed Privacy Routing

Privacy routing separates policy selection from execution. Each configured route attests its kind, location, residency, maximum sensitivity, capabilities, budget cost, isolation state, privileges, health, and fixed executor identity. Startup composition refuses disabled, missing, ambiguous, or wrong-kind executor bindings.

Every request supplies the complete sensitivity, residency, capability, budget, isolation, and privilege constraints. Restricted requests are implicitly local-only. The router deterministically selects the least-cost, least-privileged eligible route or denies the request before execution. The dispatcher binds the exact request digest to that route in SQLite, attempts it once, and never substitutes another route after failure or timeout.

Production executor adapters include:

- `OpenAICompatibleModelExecutor`, which binds one fixed model over HTTPS or loopback HTTP, requires credentials for remote HTTPS, bounds time, and returns only validated text.
- `DockerJsonToolExecutor`, which binds one fixed JSON-in/JSON-out argv in a digest-pinned container with networking disabled, a read-only root, dropped capabilities, no-new-privileges, an unprivileged UID, and bounded input, output, time, CPU, memory, processes, and temporary storage.

Run the live evidence harness with:

```bash
PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH" \
  .venv/bin/python scripts/privacy_routing_demo.py --scratch-root "$PWD"
```

The harness requires a loopback Ollama daemon with `llama3.2:3b`, Hermes cloud authentication, Docker, and the pinned Python image. It proves a restricted request executes locally, a public request executes through the cloud route, an isolated tool runs without networking, a restricted cloud-only request is denied before execution, and a selected cloud failure is durably failed without local fallback. Its temporary dispatch ledger is closed and removed afterward.
