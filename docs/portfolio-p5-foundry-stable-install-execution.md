# P5 Execution — Active-Package STABLE Install + Live Skill Execution (Foundry/Marketplace)

**Date:** 2026-08-09
**Author:** AI-DLC Pipeline Deploy (@mindroom_aidlc_pipeline_deploy:localhost)
**Directive:** P5 EXECUTION — implement active-package **STABLE** installation from the foundry/marketplace and run ONE live skill execution through the committed marketplace. Produce evidence: installation receipt (package, version, install mode=STABLE, integrity check), live skill execution result, test result; commit scoped evidence.

---

## 1. Active Package Selection

The active package is **`mindroom-docs`**, referenced by the `agent_builder` agent in `config.yaml` (`skills: [mindroom-docs]`). It was taken from the committed foundry/marketplace source (`skills/mindroom-docs`) and pinned to a STABLE, reproducible version.

| Field | Value |
|-------|-------|
| Package | `mindroom-docs` |
| Version | `1.0.0` (pinned) |
| Files | 70 |
| Integrity manifest (sha256) | `0db4792ce645a47c07d74bd93b1cb6236d25e7ffc659edcb9c674b9cff2d8d4d` |

## 2. STABLE Installation Receipt

Performed through the foundry/marketplace installer (`SkillInstaller`/`SkillRegistry`), **install mode = STABLE** — versioned and reproducible, **not** a dev/volatile install.

- **Install mode:** `STABLE`
- **Source origin:** `registry`
- **Install path:** `…/.p5-evidence/skills/mindroom-docs`
- **Integrity check:** installed manifest sha256 == published manifest sha256 → `matches_published_manifest: true`; `verify_installation` returned **no issues**
- **Registry version:** `1.0.0` recorded
- **Dependencies:** none beyond the package itself

```
STABLE_INSTALL_OK package=mindroom-docs version=1.0.0 files=70
```

## 3. Live Skill Execution Result

One skill was executed **live** against the STABLE-installed package through the real agno skill tool entrypoint (`get_skill_reference`).

| Field | Value |
|-------|-------|
| Tool | `get_skill_reference` |
| Skill | `mindroom-docs v1.0.0` |
| Argument | `llms.txt` |
| Bytes read | 4477 |
| Status | **completed** |

```
LIVE_SKILL_EXECUTION_OK skill=mindroom-docs tool=get_skill_reference arg=llms.txt bytes=4477
P5=VERIFIED
```

## 4. Test Result

- **Unit tests:** `tests/test_skill_foundry_integration.py`, `tests/test_installer.py`, `tests/test_skill_deps.py` — **95 passed** in 16.27s.

## 5. External Blocker?

**None for the deliverable.** The remote registry `https://skills.openclaw.ai/index.json` is unreachable (DNS/network failure: `nodename nor servname provided`). The active package was staged locally from the committed foundry/marketplace source, so the STABLE install + live execution completed end-to-end without that remote. This is noted as a working-around of an external network blocker, not a blocker on the P5 deliverable itself.

## 6. Committed Evidence

- `scripts/testing/p5_foundry_stable_install_evidence.py` — reproducible demo script
- `docs/p5_foundry_stable_install_evidence.log`
- `docs/p5_foundry_stable_install_receipt.json`