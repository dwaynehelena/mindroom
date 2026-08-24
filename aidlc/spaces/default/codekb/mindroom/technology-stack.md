# Technology Stack — Activation Surfaces

Versions below come from `pyproject.toml` at commit `fe018be77b252ddc9faf594f388f95cceb44eeb2` unless labelled **HYPOTHESIS**.
This is a **partial** stack view for the scanned modules, not the whole monorepo.

## Languages and Runtime

| Item | Version / constraint | Role in this scan |
|------|----------------------|-------------------|
| Python | `requires-python = ">=3.12"`; classifiers 3.12–3.14 | Implementation language |
| CI Python | 3.13 (`.github/workflows/pytest.yml`) | Tested line |
| Production Dockerfile note | project docs say 3.13 | Not re-verified in this scan |

Ruff targets `py312`.
`tool.ty.environment.python-version` is `3.13`.

## Application Frameworks

| Name | Constraint | Purpose on these surfaces |
|------|------------|---------------------------|
| FastAPI (`fastapi[standard]`) | `>=0.116.1` | Node and admin routers; process app in `api/main.py` |
| Pydantic | `>=2` | Request models (`extra="forbid"`) and `MeshConfig` |
| Pydantic Settings | `>=2.10.1` | Broader config (not unique to these modules) |
| Uvicorn | `>=0.35` | ASGI server for the same app |
| Typer | `>=0.24` | CLI (`mindroom run`); not fleet-specific |

## Persistence and Crypto

| Name | Constraint | Purpose |
|------|------------|---------|
| aiosqlite | `>=0.20` | `EdgeFleet`, `LearningLoopStore`, `FlightRecorder` |
| sqlite3 (stdlib) | — | `MeshEnrollmentRegistry` (sync) |
| cryptography | `>=47` | Ed25519 node/worker keys and request/result verify |
| hmac / hashlib / secrets (stdlib) | — | enrollment tokens and nonces |
| PyJWT | `>=2.8` | trusted-upstream JWT in `verify_user` (admin mount only) |

Fleet and learning stores set `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=FULL`.

## HTTP clients and Matrix

| Name | Constraint | Purpose |
|------|------------|---------|
| urllib (stdlib) | — | `EdgeNodeClient` default transport |
| httpx | `>=0.27` | Test clients for the fleet routers; not the node client |
| mindroom-nio | `>=0.37,<0.39` | Optional `MatrixMeshTransport.client`; default path does not import it at transport construct time |

## Adjacent product libraries (not P10)

| Name | Constraint | Purpose |
|------|------------|---------|
| agno | `==2.6.12` extras anthropic/google/ollama/openai | `agents.py` Agno `learning` flag |
| mem0ai | `>=0.1.115` | Chat memory backend; not the P10 store |

**FACT:** turning Agno learning on is not the P10 loop.

## Tooling

| Tool | Config | Use |
|------|--------|-----|
| uv | project workflow | install and `uv run` |
| hatchling + hatch-vcs | `[build-system]`, dynamic version | package build |
| Ruff | `[tool.ruff]`, lint select `ALL` | style and quality |
| pytest | `[tool.pytest.ini_options]` | tests; `asyncio_mode=auto`, xdist `-n auto`, timeout 60s |
| coverage.py | `[tool.coverage.report]` | omit lines only; no coverage floor recorded here |
| ty | `[tool.ty.*]` | type check |
| vulture | `[tool.vulture]` | dead-code; `enrollment_flag_enabled` is whitelisted |
| pre-commit | project convention (not opened in this scan) | **HYPOTHESIS:** still the merge gate described in `Agents.md` |
| GitHub Actions | `.github/workflows/pytest.yml` | `uv sync --all-extras` then `pytest -m "not requires_matrix"` |

## Local operations used by these surfaces

| Tool | Role |
|------|------|
| Tailscale CLI | `check_tailscale_connectivity` runs `tailscale status --json` |
| Docker (demo) | `scripts/learning_stable_demo.py` takes `--sandbox-image` for `DockerSkillSandboxRunner` |

Neither is required to import the library modules.

## What this stack is not

- Not a separate microservice deployable.
- Not Kubernetes-specific; `cluster/k8s/instance/default-config.yaml` was skimmed and had no Edge Fleet keys.
- Not the SaaS platform stack (Next.js, Supabase app DB).
- No message bus; fleet jobs are SQLite rows, mesh outbox is in-process memory.

## Version risks that touch this intent

- **FACT:** `google-genai` is capped `<2.9` because of Agno 2.6.12.
  That cap is unrelated to fleet/mesh/P10 but sits in the same package.
- **HYPOTHESIS:** injecting a real `nio.AsyncClient` into `MatrixMeshTransport` will couple handshake/delivery tests to the `mindroom-nio` pin.
