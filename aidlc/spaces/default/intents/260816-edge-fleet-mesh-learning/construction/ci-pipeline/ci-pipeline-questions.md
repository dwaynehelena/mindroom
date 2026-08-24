# CI pipeline questions — edge-fleet-mesh-learning

Standing approvals from `@dwayne:localhost` are in force. Answers are taken from the workspace profile and existing pipelines, not from a new human Q&A turn.

## Q1 — CI tool

**Question:** What CI tool is in use (CodePipeline, CodeBuild, GitHub Actions, Jenkins)?

**Answer:** GitHub Actions. Existing workflows live under `.github/workflows/` (`pytest.yml`, `tach.yml`, `security-scan.yml`, `build-mindroom.yml`, `calver-auto-release.yml`, `release.yml`). This intent adds `.github/workflows/edge-fleet-mesh-learning.yml`.

## Q2 — Branch strategy

**Question:** What is the branch strategy?

**Answer:** Trunk-based development on `main` with short-lived feature branches, squash-merge (`aidlc/spaces/default/memory/org.md` Way of Working). CI triggers: `push` and `pull_request` to `main`, plus `workflow_dispatch`.

## Q3 — Quality gates before merge

**Question:** What quality gates are required before merge?

**Answer:** Intent-scoped U1–U4 pytest commands recorded in `construction/build-and-test/test-results.md` (build-test-results) and `build-and-test-summary.md` (build-and-test-summary), plus the import/docs checks in `construction/build-and-test/build-instructions.md`. Existing repo gates (`pytest.yml` full-ish suite, `tach.yml`) remain; they are **not** treated as sufficient for this intent because quality did not verify the full suite (NFR5 minor). Live Tailscale helper and live worker path are **not** merge gates.

## Q4 — Artifact repositories

**Question:** What artifact repositories are used (ECR, CodeArtifact, S3)?

**Answer:** Existing product artifacts: GHCR (`build-mindroom.yml`, `REGISTRY: ghcr.io`) and PyPI (`release.yml`). This intent is a local-install activation (NFR6). It does not publish a new package, image, or CodeArtifact/S3 artifact. CI produces only job logs / pass-fail.

## Assumptions & Open Questions

None.

## Positions

None.