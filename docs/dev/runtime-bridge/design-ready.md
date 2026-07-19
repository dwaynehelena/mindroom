# Relay: Project 1 Design Ready

Status: **complete** (2026-07-17 UTC)

Design: stable internal runtime identities only; transactional SQLite room/thread sessions and `reserved`/`invoking`/`accepted`/`failed` lifecycle; request/response digests and sanitized failure; strict human-origin guard for future trusted composition; process-session/group cancellation; named OpenClaw/Hermes adapters using exact-schema strict NDJSON v1; argv/no-shell/minimal environment; deny-all tools/consequential actions; one external attempt with no automatic uncertain replay; explicit bounded concurrency.

No live upstream compatibility, sandboxing, or production Matrix identity/ingress/delivery is claimed. Executables must be trusted, reviewed, preferably attested shims. `#aidlc` is not Telegram-bridged; Telegram delivery is blocked.