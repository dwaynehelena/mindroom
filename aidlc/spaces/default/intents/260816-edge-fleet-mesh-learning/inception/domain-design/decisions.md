# Architecture Decisions — Domain Design

## ADR-001: No new components

- **Context** — The knowledge base already names fleet, mesh, learning, flight recorder, and runtime mount. Requirements are activation of those paths.
- **Decision** — Change existing components only. [Q1]
- **Consequences** — Faster to implement; no new ownership boundary. Risk is stuffing new behaviour into HTTP and runtime.
- **Alternatives Rejected** — A new worker-start component (Q1 B) would duplicate EdgeNodeClient. Several new components (Q1 C) would invent a second composition root.

## ADR-002: Operator revoke lives in EdgeFleetHttpApi

- **Context** — FR1.1 says the signed-in operator may revoke without a new permission claim. The store already revokes. Dashboard Authentication has no permissions field.
- **Decision** — EdgeFleetHttpApi drops the unused permission check on the local install. [Q2]
- **Consequences** — Revoke becomes reachable without a new auth scheme. Any signed-in local user can revoke.
- **Alternatives Rejected** — Emitting permissions from Dashboard Authentication (Q2 B) is a new auth scheme. Store-only revoke (Q2 C) leaves no HTTP path for the operator.

## ADR-003: Tailscale fail-closed is enforced at EdgeFleetHttpApi

- **Context** — FR1.3 requires every fleet operation to confirm tailnet connectivity. The store should stay free of network probes.
- **Decision** — EdgeFleetHttpApi calls TailscaleConnectivityCheck, then the store. [Q3]
- **Consequences** — One enforcement point at the boundary. Direct store callers in tests can still bypass the check unless tests go through the API.
- **Alternatives Rejected** — Store-owned checks (Q3 A) couple persistence to Tailscale. Runtime-only pre-mount check (Q3 C) would miss per-request loss of tailnet.

## ADR-004: Worker start is EdgeNodeClient

- **Context** — FR2.1 and FR2.5: a documented operator command starts the worker; MindRoom does not supervise it.
- **Decision** — Document and ship a command that uses EdgeNodeClient. [Q4]
- **Consequences** — Matches the existing client. Operator must run it for both admitted runtimes.
- **Alternatives Rejected** — A new CLI wrapper (Q4 B) is a new component, rejected in ADR-001. Orchestrator start (Q4 C) contradicts FR2.5.

## ADR-005: Visible-reply evidence goes through FlightRecorder

- **Context** — FR3.1 needs a live visible reply to produce a candidate. FlightRecorder already exists and has no production caller.
- **Decision** — SharedRuntime writes FlightRecorder after a successful visible reply; LearningCapture reads it before propose. [Q5]
- **Consequences** — Reuses the unused ledger. Capture stays independent of Matrix delivery details.
- **Alternatives Rejected** — LearningCapture hooking delivery directly (Q5 B) retires FlightRecorder and duplicates evidence. SharedRuntime writing proposals itself (Q5 C) skips capture policy.

## ADR-006: Mesh start is SharedRuntime plus the existing switch

- **Context** — FR4.4 says the unread mesh switch becomes the start path if handshake stays in.
- **Decision** — SharedRuntime starts MeshGateway when that switch is on. [Q6]
- **Consequences** — Handshake increment has a composition-root path. Inspection may still defer FR4 and then FR4.5 removes the switch instead.
- **Alternatives Rejected** — Operator-only mesh start (Q6 B) leaves the unread switch unused. Deferring the catalogue (Q6 C) would leave MeshGateway with no live owner.

## ADR-007: Activation status is SharedRuntime health

- **Context** — FR5.1 replaces two disagreeing documents with one statement.
- **Decision** — SharedRuntime health/status is the source of truth; documents quote it. [Q7]
- **Consequences** — Status cannot drift from what the process reports. Docs become derived.
- **Alternatives Rejected** — Docs-only ownership (Q7 A) would keep the same class of drift this initiative is closing.

## Assumptions & Open Questions

- ADR-006 is conditional on FR4 inspection.
- None other.
