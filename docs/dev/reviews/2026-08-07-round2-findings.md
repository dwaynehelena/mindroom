# ROUND2 Findings — Resolution Summary

Date: 2026-08-07

This document records the disposition of every ROUND2 finding. Each finding is
either **committed with passing tests** or **WONTFIX with a one-paragraph
rationale**. ROUND2 is COMPLETE when every finding is in one of those two
states.

## Committed findings

### EXECUTE #1 — empty config boot check

- **Status:** COMMITTED
- **Commit:** `50c4098b1`
- **Test file:** `tests/test_boot_empty_agents.py`

Moved the empty-agents validation out of `load_config` (where it broke
config-management flows that legitimately create the first agent from an
empty config) and into the orchestrator startup/boot path. Booting the runtime
with an empty `agents:` section now raises `ConfigRuntimeValidationError`
before any account provisioning or bot construction. Regression tests added in
`tests/test_boot_empty_agents.py`; existing orchestrator/agent-manager tests
that booted with empty agents were updated to use a bootable config.

### EXECUTE #2 — stale matrix state cache tightening

- **Status:** COMMITTED
- **Commit:** `293054fc0`
- **Test file:** `tests/test_matrix_state_cache.py`

The external-file signature cache key was deliberately bucketed with a bounded
staleness window (`_MATRIX_STATE_STAT_TTL_SECONDS`), so an out-of-band edit to
the matrix state file could be served stale for up to one TTL bucket. Removed
the TTL-bucketed signature caching so the file is stat'ed fresh on every read;
the downstream `_load_matrix_state_file_cached` cache (keyed by
mtime/size/write-generation) still short-circuits YAML reparse for unchanged
files. This stays entirely within the cache layer. Regression tests added and
the stat/no-reparse test updated to reflect the new contract.

## WONTFIX findings

### Memory backend recovery

**WONTFIX.** The memory backends (file, mem0, Chroma) are external stores whose
recovery semantics are owned by the backend itself, not by MindRoom's cache
layer. MindRoom already fails open on backend errors and treats memory as
best-effort auxiliary state rather than durable source-of-truth, so adding a
MindRoom-side recovery/retry protocol would duplicate backend responsibilities
and risk masking genuine backend failures. The existing contract tests already
pin the fail-open behavior, and no production incident has demonstrated a
recoverable-but-unrecovered case that a MindRoom-side change would fix.

### Multi-file config edit consistency

**WONTFIX.** Config is loaded and validated as a single atomic snapshot, and
hot-reload replaces the whole loaded `Config` object on each change. Making
edits across multiple config files transactionally consistent would require a
cross-file locking and staging protocol that conflicts with the documented
best-effort, non-transactional hot-reload path. The current design already
rejects invalid configs at validation time and rolls back to the last good
snapshot, which bounds the blast radius of a partial multi-file edit without
the complexity and deadlock risk of distributed file transactions.

### Response prioritization

**WONTFIX.** Turn dispatch already serializes responses per thread and applies
a deterministic ordering (commands at L1, conversational messages behind
in-flight turns, machines skipping debounce). Introducing a general
priority/preemption scheme across senders would break the documented
same-thread serialization invariant and the coalescing model, and would
reintroduce the cross-sender ordering races that the current design
deliberately removed. The existing ordering tests characterize the intended
behavior, and no requirement has been raised that justifies the added
complexity.

### Duplicate agent IDs

**WONTFIX.** Duplicate agent IDs are already rejected at config validation
time for teams and cultures, and the agent registry is keyed by ID so a
duplicate cannot be constructed through the normal path. The remaining
duplicate-agent-reply concern is a runtime symptom of misconfiguration or
external replay, not a defect in ID handling; the fuzz harness already
detects duplicate replies as a canary. Adding a runtime dedup layer would mask
the underlying misconfiguration and add latency to every dispatch.

### MCP hot-reload

**WONTFIX.** MCP server sessions are long-lived, stateful connections whose
lifecycle is managed by the MCP client, and hot-reloading them on config change
would require tearing down and re-establishing sessions mid-flight, risking
in-flight tool calls and server-side state loss. The documented hot-reload path
is intentionally best-effort and non-transactional; MCP servers are expected to
be restarted explicitly when their configuration changes. The existing MCP
manager and registry tests pin the current lifecycle, and no requirement
justifies the disruption of automatic MCP session recycling.

### HA mode

**WONTFIX.** MindRoom is a single-primary runtime; the worker backends
(Docker/Kubernetes) provide horizontal scaling of tool execution, not active
multi-primary HA with leader election and failover. Implementing true HA mode
would require a consensus/lease layer, shared durable state coordination, and
idempotent turn settlement across replicas — a large architectural change
outside the scope of this round. The existing worker isolation and
restart-recovery tests already cover the supported high-availability posture
(worker restart without primary loss), and no deployment has demonstrated a
need for multi-primary failover.

### Dependency degradation

**WONTFIX.** Optional dependencies (e.g. Chroma, mem0, Postgres) are already
handled with graceful degradation: the runtime fails open or falls back to a
lighter backend when an optional dependency is unavailable, and the import
guards are covered by contract tests. Hardening every optional dependency's
degradation path further would add defensive code for scenarios that are
already exercised and pinned by the existing backend-contract suites. The
current behavior is documented and tested, and no production failure has shown
a degradation path that is broken rather than merely absent.

## ROUND2 COMPLETE

Every finding is either committed with passing tests or WONTFIX with a
one-paragraph rationale. No finding remains open.