"""Tests for content-bound offline skill sandbox evidence."""

# ruff: noqa: ANN001, ANN202, D103

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mindroom.skill_registry import SkillRegistryError, translate_openclaw_skill
from mindroom.skill_sandbox import DockerSkillSandboxRunner

pytestmark = pytest.mark.asyncio
IMAGE = "python@sha256:" + "a" * 64


def _manifest():
    return translate_openclaw_skill({"id": "safe-read", "version": "1", "command": "bin/read"})


async def test_runner_binds_manifest_and_enforces_offline_unprivileged_container(tmp_path) -> None:
    calls = []

    async def run(argv, max_seconds):
        calls.append((argv, max_seconds))
        mount = argv[argv.index("--mount") + 1]
        source = mount.split(",")[1].removeprefix("src=")
        content = await asyncio.to_thread((Path(source) / "manifest.json").read_text, encoding="utf-8")
        assert json.loads(content)["skill_id"] == "safe-read"
        return 0

    runner = DockerSkillSandboxRunner(IMAGE, scratch_root=tmp_path, command_runner=run)
    evidence = await runner.run(_manifest(), (("python", "-c", "assert True"),))
    command = calls[0][0]
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert command[-3:] == ("python", "-c", "assert True")
    assert evidence.network_disabled
    assert evidence.isolated
    assert evidence.passed
    assert evidence.runner_id == f"docker:{IMAGE}"


async def test_failure_and_timeout_never_produce_passing_evidence() -> None:
    async def fail(_argv, _timeout):
        return 3

    with pytest.raises(SkillRegistryError, match="failed"):
        await DockerSkillSandboxRunner(IMAGE, command_runner=fail).run(_manifest(), (("false",),))

    async def timeout(_argv, _timeout):
        raise TimeoutError

    with pytest.raises(SkillRegistryError, match="timed out"):
        await DockerSkillSandboxRunner(IMAGE, command_runner=timeout).run(_manifest(), (("slow",),))


async def test_runner_rejects_mutable_images_and_empty_test_suites() -> None:
    with pytest.raises(ValueError, match="digest-pinned"):
        DockerSkillSandboxRunner("python:latest", command_runner=lambda _argv, _timeout: None)
    runner = DockerSkillSandboxRunner(IMAGE, command_runner=lambda _argv, _timeout: None)
    with pytest.raises(SkillRegistryError, match="between one and 100"):
        await runner.run(_manifest(), ())
