# Business Overview — Edge Fleet, Mesh Enrollment, and Governed Learning

This knowledge store is a **partial** reverse-engineering of the MindRoom workspace for intent `edge-fleet-mesh-learning`.
It covers three activation surfaces that already exist as code, tests, and documentation, and that the local install does not yet run as a single live path.
Sentences below are **FACT** when they come from source, config, tests, or the developer scan, and **HYPOTHESIS** when they are inference.

## Product Context

MindRoom is a Matrix-hosted multi-agent runtime.
The Edge Fleet, Agent Mesh, and governed learning loop sit beside that runtime as coordinator-and-worker capability, not as the chat turn pipeline.
The customer for this activation is internal: the operator of a local install.
There is no external product customer in the approved scope.

The business problem is a documented-but-inert gap.
Capability was delivered as libraries, HTTP routers, scripts, and portfolio items P9 and P10.
Live consumption of those libraries is still gated, default-off, or unconnected.

## What Each Surface Is For

### Edge Fleet (portfolio item P9)

The Edge Fleet lets a coordinator enroll OpenClaw and Hermes worker processes, queue jobs while workers are offline, lease one compatible job exclusively, and accept an attested result.
Enrollment tokens are HMAC-SHA256 claims (`mindroom.edge-enrollment/1`).
Authenticated node calls use Ed25519 request attestation (`mindroom.edge-request/1`).
Results use a separate Ed25519 attestation (`mindroom.edge-result/1`).
**FACT:** admitted runtimes are only `"openclaw"` and `"hermes"`.

### Agent Mesh enrollment and handshake (P1 extension)

The mesh is a separate worker-to-worker routing layer that can admit a stable worker identity, persist a room binding, and optionally invoke an external handshake.
Phase A is local: identity file, HMAC authority, SQLite registry.
The OpenClaw handshake is an extension point (`handshake: Callable[[], None] | None`, default `None`) plus `handshake_enabled=False`.
**FACT:** no orchestrator, bot, or API module constructs `MeshGateway`.
General-purpose mesh routing is out of this intent's scope.
The handshake is a Should Have if inspection finds a usable surface.

### Governed learning loop (portfolio item P10)

The P10 loop is a proposal store with stages `proposed` → `evaluated` → `approved` → `canary` → `stable` (plus `rejected`, `rolled_back`, `uncertain`).
Capture requires Flight Recorder evidence of a successful source run.
Human review requires a non-blank `reviewer_id` and `reason`.
Canary writes JSON under `.mindroom-learning-canary/`.
Stable skill publication writes `SKILL.md` under a runtime skill root.

This loop is not Agno `Agent.learning`.
**FACT:** `config.yaml` sets Agno `learning: false` only on the agent named `openclaw`.
That flag controls Agno preference memory, not P10 promotion.

## Current Business State

**FACT:** Edge Fleet HTTP routes mount only when `MINDROOM_EDGE_FLEET_ENABLED` is true and `MINDROOM_EDGE_FLEET_ENROLLMENT_KEY` decodes to at least 32 bytes.
**FACT:** an unset `MINDROOM_EDGE_FLEET_NODE_ALLOWLIST` is stored as `None`, and `EdgeFleet.enroll` then denies every identity (`"edge node is not on the enrollment allowlist"`).
**FACT:** store-level `revoke_node` works; the production DELETE path requires `admin.nodes.revoke` in `user["permissions"]`, and real `verify_user` returns only `{user_id, email}` (or a trusted-upstream variant of those identity fields).
Production DELETE is therefore always 403 under the mounted `verify_user` dependency.

**FACT:** `check_tailscale_connectivity` is called only from `edge_fleet_cross_device_demo.py`.
**FACT:** `require_tailscale` has zero callers and does not raise when disconnected.

**FACT:** `record_flight_event` has no callers.
Live agent runs do not emit P10 candidates through that helper.
The reversible promotion path that exists today is `scripts/learning_stable_demo.py`, not the chat runtime.

## Success Metrics This Code Must Serve

The approved scope restated the intent metrics after feasibility.

| Metric | Outcome | Priority |
|--------|---------|----------|
| SM1 | A real worker of each admitted runtime enrolls, leases, and completes a job on the local install after sign-off | Must Have |
| SM2 | AI-DLC agent team queues and leases work at runtime | Dropped (Won't Have) |
| SM3 | A real OpenClaw gateway completes the enrollment handshake | Should Have |
| SM4 | The learning loop promotes a skill from a live agent run through canary, then human-approved stable install | Must Have |
| SM5 | The currently excluded agent (`openclaw`) participates in Agno learning after the self-referential path is examined | Should Have |

SM1 is not signed off until administrative revocation actually works through the production auth path and every fleet operation verifies Tailscale connectivity.
Activation is local-only: Tailscale reachability, no public network exposure, no new identity scheme, no third runtime.

## Documents That Disagree About Approval

**FACT:** `docs/dev/portfolio-register.md` lists P9 as **GATED** ("Production activation awaiting explicit security approval from Dwayne, do not activate").
**FACT:** `docs/edge-fleet-production-activation-security-architecture.md` lists Status as **"Security approval obtained — implementation ready"**.
**FACT:** `docs/mesh_enrollment_phase_b_gate.md` records a live inverted enroll against `POST /api/edge-fleet/enroll` returning HTTP 200 and then sets `PHASE_B_HANDSHAKE_ENABLED = True`.
Those three claims cannot all be operationally true at once.
Correcting that disagreement is in scope for this intent.

## Key Functionality Inventory

What the code can already do when constructed in tests or demos:

- Issue and consume single-use enrollment tokens.
- Persist nodes, jobs, leases, request nonces, and a revocation tombstone in SQLite.
- Authenticate node heartbeat, lease, and complete calls.
- Soft-revoke a node, cancel its active leases back to `queued`, and refuse re-enrollment.
- Admit or re-admit a mesh worker from a local identity file.
- Verify both `mindroom.mesh-enrollment/1` and `mindroom.edge-enrollment/1` claims.
- Drive a proposal from capture through evaluation, review, dual-runtime canary, and dual-runtime stable install.

What the local production path does not yet do:

- Mount the fleet unless the operator sets the enable flag and a valid key.
- Admit any node unless the allowlist env var is set to a matching identity.
- Revoke a node through the mounted admin DELETE (always 403).
- Fail a remote call when Tailscale is down.
- Construct or start a `MeshGateway` from the orchestrator.
- Invoke the mesh handshake (default callable is `None`).
- Emit a P10 candidate from a live agent turn.

## Assumptions & Open Questions

- **HYPOTHESIS:** the portfolio-register "GATED" row is the operational truth for production activation, and the security-architecture "approval obtained" line is a stale document status.
- **HYPOTHESIS:** the Phase B "live enroll 200" evidence was a local process started with the fleet flag and a provisioned allowlist, not the default `mindroom run` path.
- Open: whether a real OpenClaw gateway on this machine exposes a handshake richer than `Callable[[], None]`.
- Open: how SM5 should treat Agno `learning: false` on `openclaw` once P10 writes into `openclaw_root`.
