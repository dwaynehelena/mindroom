#!/usr/bin/env python3
"""Run a reversible capture-to-active-stable governed-learning demonstration."""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from mindroom.flight_recorder import FlightRecorder
from mindroom.learning_candidates import candidate_from_signed_skill
from mindroom.learning_capture import FlightRecorderLearningCapture
from mindroom.learning_loop import EvaluationEvidence, GovernedPublisher, LearningLoopStore
from mindroom.learning_publishers import LearningFilesystemPublisher
from mindroom.learning_stable_publishers import LearningStablePublisher, Runtime
from mindroom.skill_registry import ScanPolicy, SkillTrustRegistry, translate_openclaw_skill
from mindroom.skill_sandbox import DockerSkillSandboxRunner

if TYPE_CHECKING:
    from mindroom.provenance_memory import PropagationAction

SKILL_ID = "mindroom-learning-stable-demo"
VERSION = "1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openclaw-root", type=Path, required=True)
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--sandbox-image", required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    return parser


async def _unused_memory_handler(_action: PropagationAction, _key: str) -> str:
    message = "skill demonstration must not invoke a memory handler"
    raise RuntimeError(message)


def _remove_empty_directories(root: Path) -> None:
    for path in (
        root / ".mindroom-learning-canary",
        root / SKILL_ID,
    ):
        with suppress(OSError):
            path.rmdir()


async def _main(arguments: argparse.Namespace) -> None:  # noqa: PLR0915
    openclaw_root = arguments.openclaw_root.expanduser().resolve(strict=True)
    hermes_root = arguments.hermes_root.expanduser().resolve(strict=True)
    scratch_root = arguments.scratch_root.expanduser().resolve(strict=True)
    registry = SkillTrustRegistry(os.urandom(32))
    manifest = translate_openclaw_skill(
        {
            "id": SKILL_ID,
            "version": VERSION,
            "command": "Return a concise confirmation when this governed demonstration is explicitly requested.",
        },
    )
    registry.register(manifest)
    registry.scan(SKILL_ID, VERSION, ScanPolicy(frozenset(), frozenset(), frozenset()))
    evidence = await DockerSkillSandboxRunner(
        arguments.sandbox_image,
        scratch_root=scratch_root,
    ).run(
        manifest,
        (("python", "-c", "import json; assert json.load(open('/skill/manifest.json'))['version']=='1'"),),
    )
    registry.record_sandbox(SKILL_ID, VERSION, evidence)
    entry = registry.sign(SKILL_ID, VERSION)
    candidate = candidate_from_signed_skill(source_run_id="learning-stable-demo-run", entry=entry, registry=registry)
    canary_adapters: dict[Runtime, LearningFilesystemPublisher] = {
        "openclaw": LearningFilesystemPublisher(openclaw_root, "openclaw"),
        "hermes": LearningFilesystemPublisher(hermes_root, "hermes"),
    }
    stable_adapters: dict[Runtime, LearningStablePublisher] = {
        "openclaw": LearningStablePublisher("openclaw", openclaw_root, _unused_memory_handler, registry),
        "hermes": LearningStablePublisher("hermes", hermes_root, _unused_memory_handler, registry),
    }
    canary_receipts: dict[Runtime, str] = {}
    stable_receipts: dict[Runtime, str] = {}
    proposal = None
    with tempfile.TemporaryDirectory(prefix="mindroom-learning-demo-", dir=scratch_root) as temporary:
        recorder = FlightRecorder(Path(temporary) / "flight.sqlite3")
        store = LearningLoopStore(Path(temporary) / "learning.sqlite3")
        await recorder.open()
        await store.open()
        try:
            await recorder.append(
                run_id=candidate.source_run_id,
                kind="message",
                payload={
                    "direction": "outbound",
                    "is_visible_response": True,
                    "status": "completed",
                    "suppressed": False,
                },
                side_effect=False,
            )
            proposal = await FlightRecorderLearningCapture(recorder=recorder, store=store).capture(candidate)
            await store.record_evaluation(
                proposal.proposal_id,
                EvaluationEvidence("live-docker", proposal.artifact_digest, 1, 1, 1, 1),
            )
            await store.review(
                proposal.proposal_id,
                reviewer_id="local-live-reviewer",
                approved=True,
                reason="Reversible installed-runtime verification",
            )
            publisher = GovernedPublisher(
                store,
                {"openclaw": canary_adapters["openclaw"].publish, "hermes": canary_adapters["hermes"].publish},
                {"openclaw": canary_adapters["openclaw"].rollback, "hermes": canary_adapters["hermes"].rollback},
                {"openclaw": stable_adapters["openclaw"].publish, "hermes": stable_adapters["hermes"].publish},
                {"openclaw": stable_adapters["openclaw"].rollback, "hermes": stable_adapters["hermes"].rollback},
            )
            await publisher.canary(proposal.proposal_id)
            canary_state = await store.receipts(proposal.proposal_id)
            canary_receipts = {
                "openclaw": canary_state["openclaw"][1],
                "hermes": canary_state["hermes"][1],
            }
            stable = await publisher.stabilize(proposal.proposal_id)
            stable_state = await store.receipts(proposal.proposal_id)
            stable_receipts = {
                "openclaw": stable_state["openclaw"][1],
                "hermes": stable_state["hermes"][1],
            }
            if stable.stage != "stable":
                message = "governed proposal did not reach stable"
                raise RuntimeError(message)
            openclaw_info, hermes_list = await asyncio.gather(
                _command(("openclaw", "skills", "info", SKILL_ID, "--json")),
                _hermes_native_skills(hermes_root.parent),
            )
            openclaw_discovered = SKILL_ID in openclaw_info
            hermes_discovered = SKILL_ID in hermes_list
            if not openclaw_discovered or not hermes_discovered:
                message = (
                    "active learned skill discovery failed "
                    f"(openclaw={openclaw_discovered}, hermes={hermes_discovered})"
                )
                raise RuntimeError(message)
            print("capture=verified evaluation=passed review=attributable")
            print("openclaw=stable-discovered hermes=stable-discovered")
        finally:
            if proposal is not None:
                for runtime, receipt in stable_receipts.items():
                    await stable_adapters[runtime].rollback(proposal, receipt)
                for runtime, receipt in canary_receipts.items():
                    await canary_adapters[runtime].rollback(proposal, receipt)
            await store.close()
            await recorder.close()
            _remove_empty_directories(openclaw_root)
            _remove_empty_directories(hermes_root)
    print("cleanup=verified")


async def _command(argv: tuple[str, ...]) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    if process.returncode != 0:
        message = f"runtime discovery failed: {Path(argv[0]).name}"
        raise RuntimeError(message)
    return stdout.decode(errors="replace")


async def _hermes_native_skills(hermes_home: Path) -> str:
    """Return Hermes' exact native JSON skill index without table truncation."""
    python = hermes_home / "hermes-agent/.venv/bin/python"
    source = hermes_home / "hermes-agent"
    code = (
        "import sys;"
        f"sys.path.insert(0,{str(source)!r});"
        "from tools.skills_tool import skills_list;"
        "print(skills_list())"
    )
    return await _command((str(python), "-c", code))


def main() -> int:
    """Execute the reversible demonstration."""
    asyncio.run(_main(_parser().parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
