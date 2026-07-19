---
title: Skill Foundry and Trust Registry
---

# Skill Foundry and Trust Registry

The Skill Foundry converts OpenClaw and Hermes descriptors into one strict portable manifest. Promotion is fail-closed and ordered: translate, scan, sandbox-test, sign, publish to both canaries, then install into both stable skill roots.

`DockerSkillSandboxRunner` produces content-bound evidence by mounting the exact manifest read-only into a digest-pinned container. Each test is an explicit argv vector, not a shell string. The container runs without networking, with a read-only root, all capabilities dropped, no-new-privileges, an unprivileged UID, and bounded CPU, memory, process count, temporary storage, and time. A failed or timed-out command produces no passing evidence.

Stable publication writes an active `SKILL.md` atomically, verifies its digest after installation, and returns a receipt required for rollback. Existing different content, symlinks, substituted runtime keys, and mismatched receipts are denied. Cross-runtime promotion rolls back partial canary or stable success.

The reversible installed-runtime demonstration is:

```bash
PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH" \
  .venv/bin/python scripts/skill_stable_demo.py \
  --openclaw-root "$HOME/.openclaw/workspace/skills" \
  --hermes-root "$HOME/.hermes/skills" \
  --sandbox-image 'python@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df' \
  --scratch-root "$PWD" \
  --execute-models
```

The harness creates a uniquely named skill, obtains real offline sandbox evidence, signs and persists the evidence, publishes both canaries, promotes both stable installations, requires native discovery and an exact model-level execution marker from each runtime, then receipt-verifies rollback and removes only empty demo directories.
