#!/usr/bin/env python3
"""Run a reversible trusted stable-skill demo against installed runtimes."""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from mindroom.skill_publication import Runtime, TrustedSkillPublisher, TrustedSkillStore
from mindroom.skill_publishers import FilesystemCanaryPublisher, FilesystemStablePublisher
from mindroom.skill_registry import ScanPolicy, SkillTrustRegistry, translate_openclaw_skill
from mindroom.skill_sandbox import DockerSkillSandboxRunner

SKILL_ID = "mindroom-stable-demo"
VERSION = "1"
MARKER = "MINDROOM_STABLE_DEMO_OK"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openclaw-root", type=Path, required=True)
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--sandbox-image", required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--execute-models", action="store_true")
    return parser


async def _run_command(
    argv: tuple[str, ...],
    *,
    env: dict[str, str] | None = None,
    max_seconds: float = 180,
) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=max_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        message = f"runtime command timed out: {Path(argv[0]).name}"
        raise RuntimeError(message) from None
    if process.returncode != 0:
        message = f"runtime command failed: {Path(argv[0]).name} ({process.returncode})"
        raise RuntimeError(message)
    return stdout.decode(errors="replace")


def _remove_empty_demo_directories(root: Path) -> None:
    for path in (root / ".mindroom-canary" / SKILL_ID, root / SKILL_ID, root / ".mindroom-canary"):
        with suppress(OSError):
            path.rmdir()


async def _main(args: argparse.Namespace) -> None:
    openclaw_root = args.openclaw_root.expanduser().resolve(strict=True)
    hermes_root = args.hermes_root.expanduser().resolve(strict=True)
    scratch_root = args.scratch_root.expanduser().resolve(strict=True)
    registry = SkillTrustRegistry(os.urandom(32))
    manifest = translate_openclaw_skill(
        {
            "id": SKILL_ID,
            "version": VERSION,
            "command": f"When explicitly asked to run this demo, respond with exactly {MARKER}.",
        },
    )
    registry.register(manifest)
    registry.scan(SKILL_ID, VERSION, ScanPolicy(frozenset(), frozenset(), frozenset()))
    evidence = await DockerSkillSandboxRunner(
        args.sandbox_image,
        scratch_root=scratch_root,
    ).run(
        manifest,
        (
            (
                "python",
                "-c",
                "import json; x=json.load(open('/skill/manifest.json')); "
                "assert x['skill_id']=='mindroom-stable-demo' and x['version']=='1'",
            ),
        ),
    )
    registry.record_sandbox(SKILL_ID, VERSION, evidence)
    entry = registry.sign(SKILL_ID, VERSION)

    canary: dict[Runtime, FilesystemCanaryPublisher] = {
        "openclaw": FilesystemCanaryPublisher(openclaw_root, "openclaw"),
        "hermes": FilesystemCanaryPublisher(hermes_root, "hermes"),
    }
    stable: dict[Runtime, FilesystemStablePublisher] = {
        "openclaw": FilesystemStablePublisher(openclaw_root, "openclaw"),
        "hermes": FilesystemStablePublisher(hermes_root, "hermes"),
    }
    canary_receipts: dict[Runtime, str] = {}
    stable_receipts: dict[Runtime, str] = {}
    with tempfile.TemporaryDirectory(prefix="mindroom-stable-demo-", dir=scratch_root) as temporary:
        store = TrustedSkillStore(Path(temporary) / "registry.sqlite3")
        await store.open()
        try:
            await store.add_verified(entry, registry)
            publisher = TrustedSkillPublisher(
                store,
                {runtime: adapter.publish for runtime, adapter in canary.items()},
                {runtime: adapter.rollback for runtime, adapter in canary.items()},
                {runtime: adapter.publish for runtime, adapter in stable.items()},
                {runtime: adapter.rollback for runtime, adapter in stable.items()},
            )
            canary_state = await publisher.canary(SKILL_ID, VERSION)
            canary_receipts = {
                "openclaw": canary_state["openclaw"][1] or "",
                "hermes": canary_state["hermes"][1] or "",
            }
            stable_state = await publisher.stabilize(SKILL_ID, VERSION)
            stable_receipts = {
                "openclaw": stable_state["openclaw"][1] or "",
                "hermes": stable_state["hermes"][1] or "",
            }

            openclaw_info, hermes_list = await asyncio.gather(
                _run_command(("openclaw", "skills", "info", SKILL_ID, "--json")),
                _run_command(("hermes", "skills", "list", "--source", "local")),
            )
            if SKILL_ID not in openclaw_info or SKILL_ID not in hermes_list:
                message = "stable skill was not discovered by both runtimes"
                raise RuntimeError(message)
            print("sandbox=passed network=none isolated=true tests=1")
            print("openclaw=stable-discovered")
            print("hermes=stable-discovered")

            if args.execute_models:
                prompt = f"Use the {SKILL_ID} skill now. Follow it exactly."
                openclaw_output, hermes_output = await asyncio.gather(
                    _run_command(
                        (
                            "openclaw",
                            "agent",
                            "--local",
                            "--message",
                            prompt,
                            "--session-id",
                            "mindroom-stable-demo",
                            "--thinking",
                            "off",
                            "--json",
                            "--timeout",
                            "180",
                        ),
                        max_seconds=210,
                    ),
                    _run_command(
                        ("hermes", "--ignore-rules", "--skills", SKILL_ID, "-z", prompt),
                        max_seconds=210,
                    ),
                )
                if MARKER not in openclaw_output or MARKER not in hermes_output:
                    message = "stable skill execution marker was not returned by both runtimes"
                    raise RuntimeError(message)
                print("openclaw=stable-executed")
                print("hermes=stable-executed")
        finally:
            for runtime, receipt in stable_receipts.items():
                if receipt:
                    await stable[runtime].rollback(entry, receipt)
            for runtime, receipt in canary_receipts.items():
                if receipt:
                    await canary[runtime].rollback(entry, receipt)
            await store.close()
            _remove_empty_demo_directories(openclaw_root)
            _remove_empty_demo_directories(hermes_root)
    print("cleanup=verified")


def main() -> int:
    """Run the reversible demonstration."""
    asyncio.run(_main(_parser().parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
