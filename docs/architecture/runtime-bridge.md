---
title: External Runtime Production Integration
summary: Strict OpenClaw/Hermes invocation, durable response outbox, and native Matrix delivery.
---

# External Runtime Production Integration

## Production-safe slice

`mindroom.runtime_bridge` is disabled by default. Enabled instances are composed per bot at the real post-`TurnPolicy.effective_response_action()` individual-response seam, never raw ingress. Startup owns store/adapter creation, hot reload builds and preflights a candidate generation before atomically swapping it, and shutdown disables admission before closing adapters and stores. A failed preflight/recovery leaves readiness false.

`MatrixRuntimeBridge` requires the canonical requester to equal the authenticated transport sender and to be present in the instance owner allowlist; the canonical room ID must be in that instance's room allowlist. It accepts only direct external-human `USER_MESSAGE` envelopes. Managed entities, bot/runtime/system output, trusted relays, hooks, schedules, aliases masquerading as room IDs, and synthetic origins fail closed. Successful delivery is recorded in the normal handled-turn ledger.

The native Matrix path reuses `DeliveryGateway.send_text`, including existing E2EE behavior. Runtime output carries loop-suppression metadata and `skip_mentions`; no Telegram API is involved.

## Verified runtime interfaces

### OpenClaw 2026.7.1

The adapter launches one POSIX process group, without a shell:

```text
openclaw agent --agent <id> --session-key <stable> --message <text> --json --timeout <s>
```

It never adds `--deliver` or `--yolo`. Cancellation/reload/shutdown sends `SIGTERM` to the process group, waits two seconds, then `SIGKILL`. Production requires an exact allowlisted absolute regular executable: PATH basenames, symlinks, unapproved owners, and group/world-writable path components are rejected. The child receives a minimal environment without PATH. Readiness must fail when the deployed OpenClaw version or Node runtime does not satisfy the pinned deployment contract; operators must run that preflight rather than trying the currently incompatible live CLI.

OpenClaw's verified interface has no stdin prompt option: the prompt is necessarily present in the process argv (`--message`) and can be visible to same-host process inspection subject to OS permissions. This residual exposure is unavoidable with this upstream CLI; do not place secrets in prompts and isolate the service account/host accordingly.

### Hermes 0.18.2

Hermes requires plain HTTP on a numeric loopback address and the exact configured approved port/origin; hostnames, remote endpoints, credentials in URLs, redirects, and proxy environment variables are rejected/ignored. `/health` is intentionally unauthenticated. `/v1/capabilities`, `/v1/runs`, run status, and `/stop` are authenticated. Only the literal environment name `API_SERVER_KEY` is accepted. File references must be absolute, regular, no-follow files under an absolute approved root, owned by the service UID, mode `0600` or stricter, and 1–4096 bytes. Responses, connection pools, and every timeout phase are bounded. Stop waits for authenticated terminal-status confirmation.

Hermes stays disabled unless authenticated capabilities prove host tool execution is disabled; Hermes 0.18.2 currently reports server-side tool execution and therefore fails closed. OpenClaw has no explicit no-tools flag and stays disabled unless an immutable dedicated-agent artifact proves empty tools/hooks/channels/MCP; its ordinary agent CLI is not considered a gateway-only transport because it may fall back to embedded execution.

## Durable lifecycle and recovery

SQLite schema v5 (v0 initializes only a physically empty database; all existing databases are integrity/structure checked) has:

```text
reserved -> invoking -> response_ready -> delivering -> delivered
                                      \-> delivery_failed
                    \-> failed
```

A validated response, complete canonical `MessageTarget`, and stable Matrix transaction ID are transactionally stored before delivery. `response_ready` is recoverable. `delivering` is reconciled by repeating the identical payload with the identical Matrix transaction ID, which Matrix defines idempotently; it is never assigned a new transaction ID. `invoking` is uncertain and is never replayed. Delivered rows retain the Matrix event ID so a missed normal handled-turn write can be repaired without reinvocation. Legacy unsettled rows without a target remain quarantined. Sessions are deterministic and can be pruned without deleting unsettled work.

Each invocation also appends content-free lifecycle events with a per-source monotonic sequence. Observers reconnect with their last seen sequence and replay `reserved`, `invoking`, and terminal state without repeating external execution or persisting prompt/response content in the progress stream.

## Health, readiness, observability, and backpressure

- readiness requires an open ledger, configured allowlists, available secret reference, and runtime health/capability checks;
- Hermes health is content-free and unauthenticated;
- active concurrency and admission waiters are separately bounded; overload is rejected before durable invocation reservation;
- logs/metrics should include runtime key, state, duration, queue depth, and failure class—never prompts, responses, authorization headers, or secrets;
- adapter `close()` cancels active process groups/runs for reload and shutdown.

## Milestone relay

`MilestoneRelayStore` transactionally enforces unique project/sequence and strict monotonic order, and `milestone_entry()` accepts an authenticated owner Matrix ID plus a configured owner allowlist—never a caller-supplied authorization boolean—and creates redacted, monotonic idempotent entries only for portal `!kNowWhbQOKJMwCNzqB:localhost`. Portal delivery must use native E2EE Matrix and same-transaction retry through a durable outbox. The durable relay redacts before storage and again before bounded, stable-transaction native Matrix delivery, and recovers response-ready/delivering rows with event IDs. It remains deployment-disabled without authenticated principal wiring and authoritative portal E2EE; no portal event was sent.

## Deployment and demo

1. Back up the runtime-bridge SQLite ledger and validate it with `validate_database()`.
2. Install compatible OpenClaw 2026.7.1 / Hermes 0.18.2 using the deployment's normal package mechanism.
3. Configure `external_runtimes.instances` with `enabled: false`; validate executable/endpoint and secret references.
4. Deploy code. A service restart is required to load it; this implementation did not restart the running service.
5. Enable one owner/room allowlisted canary instance and restart during a change window.
6. Readiness: check Hermes `/health` without auth, then authenticated capabilities; verify no secret/content in logs.
7. Send one owner-authored canary in an allowlisted Matrix room. Verify a single native Matrix response event ID, lifecycle `delivered`, E2EE where applicable, and no runtime echo dispatch.
8. Cancel one long canary and verify OpenClaw descendants or the Hermes run stop.
9. Restart with a synthetic `response_ready` row and verify delivery recovery; verify `invoking` is not replayed and `delivering` repeats the same transaction and payload.

## Rollback

1. Disable every external runtime instance and milestone relay entry source.
2. Restart during the approved window so no new bridge ingress is accepted.
3. Close adapters and verify active process groups/Hermes runs are cancelled.
4. Retain the v5 SQLite ledger for audit; do not downgrade or delete it.
5. Roll back application code/config. Do not alter Matrix ownership/permissions or Telegram bridge configuration.
