---
title: Project 1 Runtime Bridge Production Integration Handoff
summary: Implemented production-safe integration primitives; deployment restart and credentialed smoke remain operator steps.
---

# Project 1 — Production Integration Handoff

Implemented the smallest disabled-by-default production slice:

- fail-closed OpenClaw 2026.7.1 boundary with process-group cancellation and immutable executable/deny-all attestation requirements; its fallback-capable agent CLI is not safe to enable as a gateway-only transport;
- Hermes 0.18.2 loopback/allowlisted `/v1` API with unauthenticated health, authenticated capabilities/runs/status/stop, env/file-only `API_SERVER_KEY`, and no `-z`; current server-side tool execution deliberately fails preflight;
- strict configuration, owner/room allowlists, bounded concurrency/sessions, deny-all tools;
- direct-human-only composition after canonical origin and target resolution;
- transactional `response_ready` / `delivering` / `delivered` / `delivery_failed`, stable delivery keys, and no replay of uncertain invocation/delivery;
- native `DeliveryGateway` delivery with mention and loop suppression;
- owner-authorized redacted monotonic relay entries fixed to `!kNowWhbQOKJMwCNzqB:localhost`.

The running service must not be restarted into an enabled runtime configuration until one upstream interface can prove deny-all execution. The native Matrix Telegram portal is the intended status-delivery path; relay entries are code-ready, but lifecycle composition remains required before automatic milestone delivery can be claimed.

See `docs/architecture/runtime-bridge.md` for deployment, smoke, and rollback steps and `project-1-status.md` for verified command results.
