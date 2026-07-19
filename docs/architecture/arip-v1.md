---
title: ARIP v1 fixture-only foundation
summary: Strict synthetic event fixtures and validation helpers with no runtime consumer.
---

# ARIP v1 fixture-only foundation

ARIP v1 is an additive **fixture-only** foundation in `mindroom.arip`. It defines synthetic `arip/1` envelopes for `tool.call.requested`, `approval.requested`, and `approval.decided`. There is no shadow consumer, subscription, transport registration, metrics path, service configuration, or execution wiring. `AripSettings` is a fixture-construction model—not application settings. Its `shadow` label reserves vocabulary only; setting it creates no behavior, and `execution_enabled` can only be `false`.

The slice does not interact with Matrix, Telegram, the existing `io.mindroom.tool_approval` flow, tool hooks, or services.

## Hash domain and preview digest

The dependency-free encoder is intentionally a constrained deterministic JSON profile, **not an RFC 8785/JCS claim**. Hash inputs are limited to:

- JSON `null` and booleans;
- signed 64-bit integers;
- Unicode scalar-value strings (no unpaired surrogates);
- arrays; and
- objects with string keys.

All floats (including finite values), sets, tuples, custom containers, non-string keys, and out-of-range integers are rejected before redaction. Encoding uses UTF-8, lexicographically sorted keys, no insignificant whitespace, and direct non-ASCII characters. Tests normatively pin these rules. Models reject unknown fields and require lowercase 64-character SHA-256 hex digests.

`redacted_preview_digest` binds only a tool name and centrally redacted argument preview. It is useful for detecting mismatch among these synthetic fixtures, but deliberately loses information: different secrets can have the same preview digest. It **MUST NOT be accepted as authorization for an executable operation**.

Any future execution design must introduce a distinct, protected full-operation authorization digest over the exact unredacted executable tool name and arguments. That digest must be retained and compared inside an authenticated, access-controlled boundary, with explicit domain separation and without exposing secrets in events or logs. This slice does not define or compute it.

## Validation and trust boundaries

Argument objects/arrays and eligible actors are authoritative immutable snapshots; later mutation of caller-owned containers cannot change validated events or replay digests. Event envelopes also enforce timezone-aware UTC-normalized instants and decision `decided_at == occurred_at`.

`validate_authored_chain` validates historical authored facts independently of wall-clock state:

- the event kinds are ordered tool → request → decision;
- all three event IDs are distinct;
- `tool_call_event_id` names the supplied tool event;
- `approval_id` joins request and decision;
- the redacted preview digest matches across all three events;
- event times are ordered, request expiry is not before request authorship, and decision time is within expiry; and
- the decision actor was eligible.

`validate_live_approval` first validates that historical chain, then requires a caller-supplied, timezone-aware `observed_at`. There is intentionally no clock or decision-time default: trusted ingress must supply the observation time. Live use rejects observations before the decision, observations after expiry, and denied decisions.

The atomic in-memory replay guard allows an identical event replay and rejects one event ID with different canonical content under concurrent threads. It remains process-local, is lost on restart, and provides no cross-process guarantee.

## Explicit non-goals

This slice provides no signatures, authentication, authorization discovery, confidentiality, transport security, durable/distributed replay protection, revocation, ordering service, availability control, or execution authorization. Actor and source identifiers are assertions in synthetic data. Redaction is not a confidentiality boundary. No claim is made that fixtures are observed or consumed in a running shadow path.

A future integration requires authenticated ingress, trusted observation timestamps, protected full-operation digests, durable transactional replay storage, explicit transport mapping, observability, and a separately reviewed execution gate.

## Rollback and verification

Rollback is deletion of `src/mindroom/arip.py`, `tests/test_arip.py`, `tests/fixtures/arip/`, this document, and the corresponding `tach.toml` module entry. There is no config migration, database state, service restart, or wire registration.

Safe focused verification from the repository root:

```bash
.venv/bin/pytest -n 0 tests/test_arip.py tests/test_redaction.py
.venv/bin/ruff check src/mindroom/arip.py tests/test_arip.py
.venv/bin/tach check
```