---
title: Governed Learning Loop
summary: Trace-gated candidate capture, exact live review, and reversible active publication.
---

# Governed Learning Loop

The learning loop never turns an ordinary run directly into active behavior. A runtime first emits a `RuntimeLearningCandidateEvent` from one of the explicit verified producers:

- skill candidates require a signed Skill Trust Registry entry with passing isolated sandbox evidence;
- memory candidates require an active cited record with current owner/purpose consent and TTL.

`capture_runtime_learning_event` is the production event sink. It verifies the source run's complete tamper-evident Flight Recorder chain, requires a successful visible terminal response, rejects failed/interrupted model or tool execution, and idempotently persists the exact candidate digest as a proposal. Memory candidates retain their consent grant so stable promotion can recheck it at promotion time rather than relying on stale capture-time authority.

Regression evaluation must bind the exact artifact digest, run at least one test, pass every test, and not regress the baseline score. `request_runtime_learning_review` then uses MindRoom's initialized live Matrix approval manager. The approval card and ARIP ledger bind proposal identity, artifact digest, source run, kind, suite, test counts, and scores as one exact payload. Only the configured Matrix approver can approve or deny it; expiry makes no governance transition, and a decision records the canonical Matrix actor and reason.

Canaries are private, non-executable JSON artifacts in both runtime roots. Stable promotion is a distinct external operation, not a status relabel:

- a learned skill re-verifies the deployment signing key, atomically installs a proposal-specific active `SKILL.md`, and refuses to adopt different pre-existing content;
- a learned memory revalidates consent/TTL and uses the native OpenClaw Markdown or Hermes holographic memory handler under a proposal-derived identity.

Both runtime stable receipts are persisted. Partial failure compensates successful installs in reverse order and restores the original canary receipts. Failed compensation is marked `uncertain`; it is never reported as rolled back. A restart may resume a mixed canary/stable ledger without repeating a durably recorded stable target, while an unrecorded atomic write is idempotently recognized by its exact proposal-specific bytes.

The reversible installed-runtime demonstration is:

```bash
PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH" \
  PYTHONPATH=src .venv/bin/python scripts/learning_stable_demo.py \
  --openclaw-root "$HOME/.openclaw/workspace/skills" \
  --hermes-root "$HOME/.hermes/skills" \
  --sandbox-image 'python@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df' \
  --scratch-root "$PWD"
```

It obtains real offline sandbox evidence, captures a verified source run, records evaluation and attributable review, publishes both canaries, actively installs into both runtime roots, verifies exact native discovery, then receipt-rolls both stable and canary artifacts back with zero residue.
