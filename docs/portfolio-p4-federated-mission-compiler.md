# Portfolio P4 — Federated Mission Compiler

**Date:** 2026-08-03  
**Author:** AI-DLC Developer (@mindroom_aidlc_developer:localhost)  
**Status:** ✅ Design Complete — Ready for Implementation  
**Prerequisite:** P1 Agent Mesh Gateway ✅ Complete (59 tests passing, live two-worker demo verified)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What Is a Federated Mission?](#2-what-is-a-federated-mission)
3. [Architecture Overview](#3-architecture-overview)
4. [Core Components](#4-core-components)
5. [Mission Compilation Pipeline](#5-mission-compilation-pipeline)
6. [Worker Collaboration Through the Gateway](#6-worker-collaboration-through-the-gateway)
7. [Checkpointing, Retries, and Compensation](#7-checkpointing-retries-and-compensation)
8. [Demo Scenarios](#8-demo-scenarios)
9. [Implementation Plan](#9-implementation-plan)
10. [Non-Functional Requirements](#10-non-functional-requirements)
11. [Risk Assessment](#11-risk-assessment)

---

## 1. Executive Summary

The **Federated Mission Compiler** (P4) enables multiple AI workers to collaboratively compile, execute, and monitor complex multi-step missions using the P1 Agent Mesh Gateway as the communication backbone. A "mission" is a directed acyclic graph (DAG) of nodes, where each node represents a unit of work assigned to a specific worker. The compiler orchestrates the DAG: it compiles the mission plan, dispatches nodes to workers via the gateway, tracks progress through checkpoints, handles retries and compensation on failure, and reports mission outcomes.

**Key insight:** The P1 gateway already provides cross-worker message routing, durable outbox delivery, reconnect cursors, and content-free lifecycle events. The Federated Mission Compiler layers mission-specific semantics on top of these primitives — it does not reinvent the transport.

---

## 2. What Is a Federated Mission?

A **federated mission** is a multi-step, multi-worker computation expressed as a DAG where:

- **Nodes** are atomic units of work (e.g., "research topic X", "generate report Y", "review output Z")
- **Edges** are dependencies between nodes (node B depends on node A's output)
- **Workers** are registered mesh agents (e.g., `mindroom`, `hermes`, `openclaw`) that execute nodes
- **Checkpoints** are durable snapshots of mission state after each completed node
- **Compensation** is the reverse-order rollback of completed nodes when a downstream node fails

### Mission Lifecycle

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Define  │───▶│ Compile  │───▶│ Execute  │───▶│ Complete │
│ Mission  │    │  Plan    │    │  DAG     │    │  / Fail  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
                      │               │
                      ▼               ▼
               ┌──────────┐    ┌──────────┐
               │ Validate │    │Checkpoint│
               │  DAG     │    │  Store   │
               └──────────┘    └──────────┘
```

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Federated Mission Compiler                        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Mission Compiler Core                     │    │
│  │                                                              │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │    │
│  │  │ DAG Compiler │  │  Executor    │  │ Checkpoint Store │  │    │
│  │  │              │  │              │  │                  │  │    │
│  │  │ • Validate   │  │ • Dispatch   │  │ • Save/load      │  │    │
│  │  │ • Topo sort  │  │ • Track      │  │ • Resume from    │  │    │
│  │  │ • Assign     │  │ • Retry      │  │   checkpoint     │  │    │
│  │  │ • Detect     │  │ • Compensate │  │ • Garbage        │  │    │
│  │  │   cycles     │  │ • Timeout    │  │   collect        │  │    │
│  │  └──────────────┘  └──────────────┘  └──────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                       │
│                              ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │              P1 Agent Mesh Gateway (transport)               │    │
│  │                                                              │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │    │
│  │  │  Route   │  │  Outbox  │  │  Cursor  │  │ Lifecycle  │  │    │
│  │  │ Messages │  │  Store   │  │  Store   │  │  Events    │  │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └────────────┘  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                       │
│                              ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Mesh Workers                               │    │
│  │                                                              │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │    │
│  │  │  mindroom    │  │   hermes     │  │    openclaw      │  │    │
│  │  │  (orchestr.) │  │  (research)  │  │  (code gen)      │  │    │
│  │  └──────────────┘  └──────────────┘  └──────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### Layering

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| **Mission** | `MissionCompiler` | DAG compilation, validation, topological sort |
| **Execution** | `MissionExecutor` | Node dispatch, progress tracking, retry, compensation |
| **Persistence** | `MissionCheckpointStore` | Durable checkpoint save/load for resumable missions |
| **Transport** | `MeshGateway` (P1) | Cross-worker message routing, outbox, cursors, lifecycle |
| **Workers** | Registered mesh agents | Execute individual mission nodes |

---

## 4. Core Components

### 4.1 MissionNode

```python
@dataclass(frozen=True, slots=True)
class MissionNode:
    """One atomic unit of work in a federated mission DAG."""
    node_id: str
    mission_id: str
    role: str                    # e.g. "research", "generate", "review", "notify"
    target_worker: str           # e.g. "hermes", "openclaw", "mindroom"
    action: str                  # e.g. "research_topic", "generate_code", "review_output"
    parameters: dict[str, Any]   # Node-specific parameters
    dependencies: tuple[str, ...]  # Node IDs this node depends on
    retry_limit: int = 3
    timeout_seconds: float = 300.0
    idempotent: bool = True      # Whether retry is safe without compensation
```

### 4.2 MissionPlan

```python
@dataclass(frozen=True, slots=True)
class MissionPlan:
    """A compiled, validated, topologically-sorted mission DAG."""
    mission_id: str
    goal: str
    nodes: tuple[MissionNode, ...]  # Topologically sorted
    created_at: float
    compiled_by: str                # Worker ID of the compiler
```

### 4.3 MissionCheckpointStore

```python
class MissionCheckpointStore:
    """Durable checkpoint store for mission execution state.

    Each checkpoint records:
    - Which nodes have completed (with their outputs)
    - Which nodes are in-flight
    - Which nodes have failed
    - Which nodes have been compensated
    - The current cursor for each involved worker

    Checkpoints are written after each node completion, enabling
    resumable execution after worker disconnect or runtime restart.
    """

    def save_checkpoint(self, state: MissionExecutionState) -> None: ...
    def load_checkpoint(self, mission_id: str) -> MissionExecutionState | None: ...
    def list_missions(self) -> list[str]: ...
    def delete_checkpoint(self, mission_id: str) -> None: ...
```

### 4.4 MissionExecutor

```python
class MissionExecutor:
    """Executes a compiled mission plan through the mesh gateway.

    The executor:
    1. Loads the plan and any existing checkpoint
    2. Identifies ready nodes (all dependencies satisfied)
    3. Dispatches ready nodes to target workers via the gateway
    4. Waits for results (with timeout)
    5. Saves checkpoints after each completion
    6. Handles retries (up to retry_limit)
    7. On terminal failure: compensates completed nodes in reverse order
    8. Reports final mission outcome
    """
```

### 4.5 MissionCompiler

```python
class MissionCompiler:
    """Compiles a high-level mission goal into an executable DAG.

    The compiler:
    1. Accepts a natural-language mission goal + optional constraints
    2. Decomposes the goal into a DAG of MissionNodes
    3. Validates the DAG (no cycles, all deps resolvable, workers registered)
    4. Topologically sorts the nodes
    5. Assigns each node to a registered worker based on role/capability
    6. Returns a MissionPlan ready for execution
    """
```

---

## 5. Mission Compilation Pipeline

### Step 1: Mission Definition

A mission is defined as a natural-language goal with optional structured parameters:

```json
{
  "mission_id": "mission-001",
  "goal": "Research the latest AI agent frameworks, generate a comparison report, and notify the team",
  "constraints": {
    "max_duration_seconds": 600,
    "preferred_workers": ["hermes", "openclaw", "mindroom"],
    "output_format": "markdown"
  }
}
```

### Step 2: DAG Compilation

The `MissionCompiler` decomposes the goal into a DAG:

```
                    ┌──────────────────┐
                    │  research_agents │  (worker: hermes)
                    │  "Research AI    │
                    │  agent frameworks│
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  generate_report  │  (worker: openclaw)
                    │  "Generate       │
                    │  comparison      │
                    │  report"         │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  review_output    │  (worker: mindroom)
                    │  "Review report  │
                    │  for quality"    │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  notify_team     │  (worker: mindroom)
                    │  "Notify team    │
                    │  via Matrix"     │
                    └──────────────────┘
```

### Step 3: DAG Validation

The compiler validates:
- **No cycles:** Uses DFS cycle detection
- **All dependencies resolvable:** Every node's dependencies exist in the plan
- **Workers registered:** Each node's `target_worker` is registered with the gateway
- **Idempotency consistency:** Non-idempotent nodes cannot have automatic retry enabled
- **Timeout bounds:** All timeouts are within the mission's `max_duration_seconds`

### Step 4: Topological Sort

Nodes are sorted using Kahn's algorithm, producing an execution order that respects all dependencies. Nodes with no dependencies (root nodes) are ready for immediate dispatch.

### Step 5: Worker Assignment

The compiler assigns nodes to workers based on:
- **Explicit assignment:** If the mission definition specifies a worker
- **Role-based routing:** The compiler maps roles (research → hermes, code → openclaw, orchestration → mindroom)
- **Capability matching:** Workers advertise capabilities through their registration metadata

---

## 6. Worker Collaboration Through the Gateway

### 6.1 Message Flow

```
┌──────────┐         ┌──────────┐         ┌──────────┐
│  Worker  │         │ Gateway  │         │  Worker  │
│  (src)   │         │          │         │  (tgt)   │
└────┬─────┘         └────┬─────┘         └────┬─────┘
     │                     │                     │
     │  1. Route Message  │                     │
     │──────────────────▶│                     │
     │                    │  2. Create Outbox   │
     │                    │  3. Save Cursor     │
     │                    │                     │
     │                    │  4. Deliver Message  │
     │                    │────────────────────▶│
     │                    │                     │
     │                    │  5. Execute Node    │
     │                    │   (if gate open)    │
     │                    │                     │
     │                    │  6. Result Message   │
     │                    │◀────────────────────│
     │  7. Route Result  │                     │
     │◀──────────────────│                     │
     │                    │                     │
     │  8. Save Checkpoint│                     │
     │  9. Dispatch Next  │                     │
     │     Ready Nodes    │                     │
```

### 6.2 Mission-Specific Message Types

The compiler defines a set of message types carried as `MeshMessage.content` (JSON-encoded):

| Message Type | Direction | Purpose |
|-------------|-----------|---------|
| `mission_dispatch` | Compiler → Worker | Assign a node for execution |
| `mission_result` | Worker → Compiler | Return node execution result |
| `mission_progress` | Worker → Compiler | Intermediate progress update |
| `mission_cancel` | Compiler → Worker | Cancel a dispatched node |
| `mission_query` | Compiler → Worker | Query node status |
| `mission_compensate` | Compiler → Worker | Request compensation for a node |

### 6.3 Message Schema

```json
{
  "message_type": "mission_dispatch",
  "mission_id": "mission-001",
  "node_id": "research_agents",
  "action": "research_topic",
  "parameters": {
    "topic": "AI agent frameworks 2026",
    "depth": "comprehensive"
  },
  "correlation_id": "corr-abc-123",
  "idempotency_key": "mission-001-research_agents-1"
}
```

### 6.4 Gateway Integration Points

The compiler integrates with the P1 gateway at these points:

1. **`MeshGateway.route_message()`** — Dispatch nodes to workers and receive results
2. **`MeshGateway.deliver_pending()`** — Flush pending outbox entries
3. **`MeshGateway.worker_reconnect()`** — Resume mission after worker disconnect
4. **`MeshGateway.lifecycle_events`** — Observe mission progress without message content
5. **`MeshGateway.register_worker()`** — Discover available workers and their capabilities
6. **`GatewayExecutionGate`** — In gateway-only mode, the compiler can route mission messages but workers cannot execute — useful for dry-run validation

---

## 7. Checkpointing, Retries, and Compensation

### 7.1 Checkpoint Strategy

After each node completes (success or terminal failure), the compiler writes a checkpoint:

```json
{
  "mission_id": "mission-001",
  "checkpoint_version": 1,
  "completed_nodes": {
    "research_agents": {
      "status": "succeeded",
      "output": { "summary": "...", "sources": ["..."] },
      "completed_at": 1722700000.0,
      "worker_id": "hermes",
      "attempts": 1
    }
  },
  "in_flight_nodes": ["generate_report"],
  "failed_nodes": {},
  "compensated_nodes": {},
  "worker_cursors": {
    "hermes": "mesh-cursor-abc-1722700000",
    "openclaw": "mesh-cursor-def-1722700001"
  }
}
```

**Properties:**
- Checkpoints are written **before** dispatching the next batch of ready nodes
- On restart, the compiler loads the latest checkpoint and resumes from there
- In-flight nodes are re-dispatched (with idempotency keys to prevent double execution)
- Worker cursors allow the compiler to verify that no messages were lost during disconnect

### 7.2 Retry Policy

| Scenario | Behavior |
|----------|----------|
| Node timeout | Retry up to `retry_limit` with exponential backoff (1s, 2s, 4s, ...) |
| Node returns error | Retry if `idempotent=True`; fail if `idempotent=False` |
| Worker disconnected | Wait for reconnect (up to timeout), then retry |
| Gateway delivery failure | Automatic retry via outbox (P1 handles this) |
| Non-idempotent node fails | Terminal failure — no retry, begin compensation |

### 7.3 Compensation (Saga Pattern)

When a node fails terminally, the compiler initiates **compensation** — a reverse-order rollback of all previously completed nodes:

```
Normal execution order:  A → B → C → D
                                  ↑
                            D fails here

Compensation order:       D (failed) → C → B → A
                                      (compensate in reverse)
```

Each node that supports compensation defines a `compensation_action`:

```json
{
  "node_id": "generate_report",
  "action": "generate_report",
  "compensation_action": "delete_report",
  "parameters": { "topic": "AI agents" },
  "compensation_parameters": { "report_id": "..." }
}
```

**Compensation guarantees:**
- Compensation runs in strict reverse topological order
- Each compensation node is dispatched through the gateway (same as execution)
- Compensation failures are logged but do not block the chain (best-effort)
- The checkpoint records which nodes have been compensated
- After full compensation, the mission is marked `compensated`

---

## 8. Demo Scenarios

### Demo 1: Basic Three-Worker Mission (Hello World)

**Goal:** Demonstrate end-to-end mission compilation, dispatch, and completion.

**Scenario:**
1. User defines a mission: "Research AI safety, generate a summary, and notify me"
2. Compiler decomposes into 3 nodes:
   - `research_safety` → hermes (research)
   - `generate_summary` → openclaw (code gen / formatting)
   - `notify_user` → mindroom (notification)
3. Compiler validates DAG, registers workers with gateway
4. Executor dispatches `research_safety` to hermes
5. Hermes completes → checkpoint saved → `generate_summary` dispatched to openclaw
6. Openclaw completes → checkpoint saved → `notify_user` dispatched to mindroom
7. Mindroom notifies user → mission marked `completed`

**Expected output:** Mission completes in ~3 sequential steps, all checkpoints visible, lifecycle events emitted.

### Demo 2: Retry and Recovery

**Goal:** Demonstrate automatic retry on node failure and recovery from checkpoint.

**Scenario:**
1. Mission with 3 nodes: A → B → C
2. Node B is configured to fail on first attempt (simulated)
3. Executor retries B (idempotent=True) → B succeeds on second attempt
4. C dispatches and completes
5. **Simulate runtime restart:** New compiler instance loads checkpoint
6. Verifies that A, B are marked completed and C is completed
7. No re-execution of completed nodes

**Expected output:** Mission completes after retry; restart recovers without re-executing completed nodes.

### Demo 3: Compensation on Terminal Failure

**Goal:** Demonstrate saga-style compensation when a non-idempotent node fails.

**Scenario:**
1. Mission with 4 nodes: A (prepare) → B (transform) → C (publish) → D (notify)
2. A, B, C complete successfully
3. D fails terminally (non-idempotent, cannot retry)
4. Executor initiates compensation:
   - Compensate C (unpublish)
   - Compensate B (undo transform)
   - Compensate A (cleanup preparation)
5. Mission marked `compensated`
6. All compensation actions dispatched through gateway

**Expected output:** Mission fails gracefully with all nodes compensated in reverse order. Checkpoint shows `compensated` status.

### Demo 4: Gateway-Only Dry Run

**Goal:** Demonstrate mission compilation and routing without worker execution.

**Scenario:**
1. Set `MINDROOM_MESH_GATEWAY_MODE=gateway_only`
2. Compile a mission plan
3. Route all dispatch messages through the gateway
4. Execution gate blocks worker-side execution
5. Verify that:
   - Messages are routed and delivered to target rooms
   - Outbox entries are created
   - Lifecycle events are emitted
   - No worker execution occurs
6. Open the gate and re-dispatch to execute for real

**Expected output:** Mission messages flow through the gateway but workers do not execute. Demonstrates the separation of transport from execution.

### Demo 5: Multi-Mission Concurrent Execution

**Goal:** Demonstrate the compiler handling multiple missions simultaneously.

**Scenario:**
1. Define 3 independent missions: M1, M2, M3
2. Each mission has 2-3 nodes targeting different workers
3. Compile all 3 missions
4. Execute concurrently — the executor dispatches ready nodes from all missions
5. Verify that:
   - Nodes from different missions interleave correctly
   - Checkpoints are per-mission (no cross-mission state corruption)
   - Lifecycle events include mission_id for filtering

**Expected output:** All 3 missions complete successfully with interleaved execution. Each mission's checkpoint is independent.

---

## 9. Implementation Plan

### Phase 1: Core Compiler (Days 1-2)

| Task | Files | Description |
|------|-------|-------------|
| 1.1 | `mindroom/mission_compiler/models.py` | `MissionNode`, `MissionPlan`, `MissionExecutionState` dataclasses |
| 1.2 | `mindroom/mission_compiler/compiler.py` | `MissionCompiler` — DAG decomposition, validation, topological sort |
| 1.3 | `mindroom/mission_compiler/store.py` | `MissionCheckpointStore` — SQLite-backed checkpoint persistence |
| 1.4 | `mindroom/mission_compiler/__init__.py` | Package exports |
| 1.5 | `tests/test_mission_compiler.py` | Unit tests for compilation, validation, cycle detection |

**Deliverable:** Mission plan can be compiled from a goal string, validated, and persisted.

### Phase 2: Executor with Gateway Integration (Days 3-4)

| Task | Files | Description |
|------|-------|-------------|
| 2.1 | `mindroom/mission_compiler/executor.py` | `MissionExecutor` — node dispatch, progress tracking, checkpoint writing |
| 2.2 | `mindroom/mission_compiler/messages.py` | Mission-specific message types and serialization |
| 2.3 | Integration with `mindroom.mesh.gateway` | Route mission messages through the gateway |
| 2.4 | `tests/test_mission_executor.py` | Unit tests for dispatch, checkpoint, retry |

**Deliverable:** Mission plan can be executed through the gateway with checkpointing.

### Phase 3: Retry and Compensation (Days 5-6)

| Task | Files | Description |
|------|-------|-------------|
| 3.1 | Retry logic in `executor.py` | Exponential backoff, idempotency check, timeout handling |
| 3.2 | Compensation logic in `executor.py` | Reverse-order compensation dispatch |
| 3.3 | `tests/test_mission_compensation.py` | Unit tests for compensation scenarios |

**Deliverable:** Missions handle failures gracefully with retry and compensation.

### Phase 4: Demo Scripts (Day 7)

| Task | Files | Description |
|------|-------|-------------|
| 4.1 | `mindroom/mission_compiler/demo_basic.py` | Demo 1: Basic three-worker mission |
| 4.2 | `mindroom/mission_compiler/demo_retry.py` | Demo 2: Retry and recovery |
| 4.3 | `mindroom/mission_compiler/demo_compensation.py` | Demo 3: Compensation |
| 4.4 | `mindroom/mission_compiler/demo_gateway_only.py` | Demo 4: Gateway-only dry run |
| 4.5 | `mindroom/mission_compiler/demo_concurrent.py` | Demo 5: Multi-mission concurrent |

**Deliverable:** All 5 demo scenarios runnable and documented.

### Phase 5: Integration and Hardening (Day 8)

| Task | Description |
|------|-------------|
| 5.1 | End-to-end integration test with real gateway |
| 5.2 | Performance testing (10+ concurrent missions) |
| 5.3 | Edge case testing (worker disconnect mid-mission, gateway restart) |
| 5.4 | Documentation and status report |

**Deliverable:** Production-ready Federated Mission Compiler with passing test suite.

---

## 10. Non-Functional Requirements

| NFR | Target | Approach |
|-----|--------|----------|
| **Resilience** | No mission state loss on single worker/gateway failure | Checkpoints after every node; reconnect cursors for message recovery |
| **Scalability** | 10+ concurrent missions, 50+ nodes per mission | Per-mission checkpoint isolation; gateway handles routing at scale |
| **Latency** | Node dispatch <100ms (excluding worker execution time) | Gateway routing is synchronous; execution time depends on worker |
| **Observability** | Full mission lifecycle without exposing content | Content-free lifecycle events via P1 gateway; mission_id in event metadata |
| **Idempotency** | Safe retry for idempotent nodes; no double-execution for non-idempotent | Idempotency keys in dispatch messages; checkpoint tracking of in-flight nodes |
| **Privacy** | No mission content in lifecycle events | P1 gateway's content-free lifecycle pattern extended with mission_id only |
| **Durability** | Checkpoints survive process restart | SQLite-backed checkpoint store (WAL mode, synchronous=FULL) |

---

## 11. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Worker disconnect mid-mission | Medium | Medium | Reconnect cursors + checkpoint resume; timeout-based failure detection |
| Non-idempotent node double-execution | Low | High | Idempotency keys + checkpoint tracking of in-flight nodes |
| DAG cycle in mission definition | Low | Medium | Compiler validates DAG with cycle detection before execution |
| Checkpoint store corruption | Low | High | WAL mode + fsync; periodic integrity checks |
| Gateway message loss | Low | Medium | P1 outbox guarantees at-least-once delivery; cursors for dedup |
| Compensation chain failure | Medium | Low | Best-effort compensation; failures logged but don't block chain |
| Mission timeout exceeded | Medium | Medium | Per-node timeout + mission-level max_duration enforcement |

---

## Appendix A: Package Structure

```
mindroom/src/mindroom/mission_compiler/
├── __init__.py              # Package exports
├── models.py                # MissionNode, MissionPlan, MissionExecutionState
├── compiler.py              # MissionCompiler — DAG compilation and validation
├── executor.py              # MissionExecutor — node dispatch, retry, compensation
├── store.py                 # MissionCheckpointStore — SQLite-backed persistence
├── messages.py              # Mission-specific message types and serialization
├── demo_basic.py            # Demo 1: Basic three-worker mission
├── demo_retry.py            # Demo 2: Retry and recovery
├── demo_compensation.py     # Demo 3: Compensation
├── demo_gateway_only.py     # Demo 4: Gateway-only dry run
└── demo_concurrent.py       # Demo 5: Multi-mission concurrent

mindroom/tests/
├── test_mission_compiler.py       # Unit tests for compilation
├── test_mission_executor.py       # Unit tests for execution
└── test_mission_compensation.py   # Unit tests for compensation
```

## Appendix B: Gateway Integration Points Reference

| Gateway API | Used By | Purpose |
|-------------|---------|---------|
| `MeshGateway.register_worker()` | Compiler startup | Discover available workers |
| `MeshGateway.route_message()` | Executor | Dispatch nodes, receive results |
| `MeshGateway.deliver_pending()` | Executor | Flush pending outbox |
| `MeshGateway.worker_reconnect()` | Executor | Resume after disconnect |
| `MeshGateway.lifecycle_events` | Observer | Monitor mission progress |
| `MeshGateway.pending_outbox_count()` | Executor | Verify all messages delivered |
| `GatewayExecutionGate.check()` | Executor | Verify execution is allowed |
| `MeshCursorStore` | Checkpoint store | Persist worker cursors with checkpoints |

---

*End of P4 Federated Mission Compiler Architecture Document*