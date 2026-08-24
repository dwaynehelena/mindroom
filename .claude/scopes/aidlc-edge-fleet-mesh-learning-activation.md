---
name: edge-fleet-mesh-learning-activation
depth: Comprehensive
keywords: []
description: Close the MindRoom edge-fleet/mesh/OpenClaw/governed-learning integration gaps
skeleton: on
---

# edge-fleet-mesh-learning-activation scope

Composed scope for closing four security-sensitive integration gaps found
in the MindRoom system integration map: activating the gated Edge Fleet
(P9) production path behind an explicit sign-off, wiring the live
`aidlc_*` agent team into the Edge Fleet job queue and Mesh Gateway (P1)
runtime call path, implementing the real OpenClaw gateway enrollment
handshake at its already-designed extension point in
`mesh/enrollment.py`, and live-wiring the governed learning loop that
today only runs via a standalone demo script.

## Why this shape

None of the four gaps are UI/market-facing, so ideation stays lean
(market-research, team-formation, rough-mockups, refined-mockups skip —
single named approver, no UI surface, no external market). But the work
spans five coupled subsystems (`mesh/`, `edge_fleet.py`, `tool_system/`,
the learning-loop scripts, `config.yaml`) with real production-activation
and external-network-egress risk, so inception, construction, and
operation run in full: requirements, domain design, units, contracts, and
delivery planning to decompose the four gaps; the full construction spine
including NFR requirements/design and infrastructure design for the
security and activation-gate concerns; and the full operation phase
(deployment pipeline, environment provisioning, deployment execution,
observability, incident response, performance validation) because this is
a first-time activation of a previously-disabled, network-facing
production surface. `feedback-optimization` skips — this is bounded
gap-closure, not an ongoing iteration loop. `user-stories` skips — no
user-facing personas; its acceptance-criteria role folds into
`requirements-analysis`. `practices-discovery` skips — conventions are
already embodied in the existing `test_mesh_*`/`test_learning_*`/
`test_edge_fleet*` test trees, which `reverse-engineering` and
`build-and-test` cover instead.

## Membership

Composed via the adaptive workflow composer (ARS 64/100, Comprehensive
band) on 2026-08-16. 26 of 33 stages EXECUTE; SKIP: `market-research`,
`team-formation`, `rough-mockups`, `practices-discovery`, `user-stories`,
`refined-mockups`, `feedback-optimization`.
