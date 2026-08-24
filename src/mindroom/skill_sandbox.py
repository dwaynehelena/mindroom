"""Content-bound, network-disabled Docker sandbox evidence for skill tests."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from mindroom.skill_registry import PortableSkillManifest, SandboxEvidence, SkillRegistryError, manifest_digest

CommandRunner = Callable[[tuple[str, ...], float], Awaitable[int]]
_DIGEST_IMAGE = re.compile(r"^[a-z0-9./_-]+@sha256:[0-9a-f]{64}$")


class DockerSkillSandboxRunner:
    """Run explicit argv tests in a pinned, unprivileged, offline container."""

    def __init__(
        self,
        image: str,
        *,
        timeout_seconds: float = 60.0,
        scratch_root: Path | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        if _DIGEST_IMAGE.fullmatch(image) is None or timeout_seconds <= 0 or timeout_seconds > 600:
            message = "skill sandbox requires a digest-pinned image and bounded timeout"
            raise ValueError(message)
        docker = shutil.which("docker")
        if command_runner is None and docker is None:
            message = "skill sandbox requires the Docker CLI"
            raise ValueError(message)
        self._image = image
        self._timeout = timeout_seconds
        self._docker = str(Path(docker).resolve()) if docker is not None else "/usr/bin/docker"
        if scratch_root is not None:
            resolved_scratch = scratch_root.expanduser()
            if not resolved_scratch.is_absolute() or not resolved_scratch.is_dir() or resolved_scratch.is_symlink():
                message = "skill sandbox scratch root must be an existing absolute real directory"
                raise ValueError(message)
            self._scratch_root = resolved_scratch.resolve(strict=True)
        else:
            self._scratch_root = None
        self._command_runner = command_runner or _run_command

    async def run(
        self,
        manifest: PortableSkillManifest,
        tests: Sequence[tuple[str, ...]],
    ) -> SandboxEvidence:
        """Run every non-shell test with the exact manifest mounted read-only."""
        commands = tuple(tests)
        if (
            not commands
            or len(commands) > 100
            or any(not command or any(not part for part in command) for command in commands)
        ):
            message = "skill sandbox requires between one and 100 non-empty argv tests"
            raise SkillRegistryError(message)
        with tempfile.TemporaryDirectory(
            prefix="mindroom-skill-sandbox-",
            dir=self._scratch_root,
        ) as temporary:
            root = Path(temporary).resolve()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            root.chmod(0o711)  # Container uid needs traversal to the read-only bind source.
            manifest_path.chmod(0o444)
            for command in commands:
                argv = self._container_command(root, command)
                try:
                    return_code = await self._command_runner(argv, self._timeout)
                except TimeoutError as exc:
                    message = "skill sandbox test timed out"
                    raise SkillRegistryError(message) from exc
                if return_code != 0:
                    message = "skill sandbox test failed"
                    raise SkillRegistryError(message)
        return SandboxEvidence(
            runner_id=f"docker:{self._image}",
            manifest_digest=manifest_digest(manifest),
            isolated=True,
            network_disabled=True,
            passed=True,
            test_count=len(commands),
        )

    def _container_command(self, root: Path, test: tuple[str, ...]) -> tuple[str, ...]:
        return (
            self._docker,
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=64",
            "--memory=256m",
            "--cpus=1",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount",
            f"type=bind,src={root},dst=/skill,readonly",
            self._image,
            *test,
        )


async def _run_command(argv: tuple[str, ...], timeout_seconds: float) -> int:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
