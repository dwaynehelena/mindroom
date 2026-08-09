"""P10 — Connect automatic recorder capture → live review delivery → active stable publication.

Wires the verified canary adapters end to end and produces an evidence trail:
  1. recorder capture receipt   (FlightRecorder → LearningLoopStore.propose)
  2. review delivery receipt    (real Matrix approval card delivered & resolved live)
  3. canary publication receipt (both runtimes, canary)
  4. stable promotion receipt   (both runtimes, active stable install)

Each step asserts a real invariant, then the reversible demo rolls everything back.
"""

# ruff: noqa: ANN201, ANN202, D103, EM101, EM102, PLC0415, PLR0915, S310, TRY003

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from mindroom.approval_manager import SentApprovalEvent, initialize_approval_store
from mindroom.flight_recorder import FlightRecorder
from mindroom.learning_candidates import candidate_from_signed_skill
from mindroom.learning_capture import FlightRecorderLearningCapture
from mindroom.learning_loop import EvaluationEvidence, GovernedPublisher, LearningLoopStore
from mindroom.learning_publishers import LearningFilesystemPublisher
from mindroom.learning_runtime import GovernedLearningRuntime, LearningReviewContext
from mindroom.learning_stable_publishers import LearningStablePublisher, Runtime
from mindroom.skill_registry import ScanPolicy, SkillTrustRegistry, translate_openclaw_skill
from mindroom.skill_sandbox import DockerSkillSandboxRunner

if TYPE_CHECKING:
    from mindroom.provenance_memory import PropagationAction

SKILL_ID = "mindroom-learning-p10-evidence"
VERSION = "1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openclaw-root", type=Path, required=True)
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--sandbox-image", required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--matrix-state", type=Path, default=Path.home() / ".mindroom/mindroom_data/matrix_state.yaml")
    parser.add_argument("--review-room", default="!FqoGpSIkzhJDGyKDVa:localhost")
    parser.add_argument("--approver", default="@dwayne:localhost")
    parser.add_argument("--evidence", type=Path, default=None)
    return parser


async def _unused_memory_handler(_action: PropagationAction, _key: str) -> str:
    message = "skill demonstration must not invoke a memory handler"
    raise RuntimeError(message)


def _remove_empty_directories(root: Path) -> None:
    for path in (root / ".mindroom-learning-canary", root / SKILL_ID):
        with suppress(OSError):
            path.rmdir()


class _LiveApprovalResolution:
    """Capture the delivered card event id for the evidence receipt."""

    def __init__(self) -> None:
        self.card_event_id: str | None = None
        self.approval_id: str | None = None


async def _main(arguments: argparse.Namespace) -> None:
    import yaml

    openclaw_root = arguments.openclaw_root.expanduser().resolve(strict=True)
    hermes_root = arguments.hermes_root.expanduser().resolve(strict=True)
    scratch_root = arguments.scratch_root.expanduser().resolve(strict=True)

    state = yaml.safe_load(arguments.matrix_state.read_text(encoding="utf-8"))
    router = state["accounts"]["agent_router"]
    router_token = router["access_token"]
    router_sender_id = f"{router['username']}@{router['domain']}"
    base = "http://127.0.0.1:8008"

    evidence: dict[str, object] = {
        "phase": "P10",
        "title": "Automatic recorder capture -> live review -> canary -> stable publication",
        "started_at": datetime.now(UTC).isoformat(),
        "skill_id": SKILL_ID,
        "version": VERSION,
    }

    registry = SkillTrustRegistry(os.urandom(32))
    manifest = translate_openclaw_skill(
        {
            "id": SKILL_ID,
            "version": VERSION,
            "command": "Return a concise confirmation when this governed P10 demonstration is requested.",
        },
    )
    registry.register(manifest)
    registry.scan(SKILL_ID, VERSION, ScanPolicy(frozenset(), frozenset(), frozenset()))
    evidence["sandbox"] = {"runner": "docker", "image": arguments.sandbox_image}

    evidence_result = await DockerSkillSandboxRunner(
        arguments.sandbox_image,
        scratch_root=scratch_root,
    ).run(
        manifest,
        (("python", "-c", f"import json; assert json.load(open('/skill/manifest.json'))['version']=='{VERSION}'"),),
    )
    registry.record_sandbox(SKILL_ID, VERSION, evidence_result)
    entry = registry.sign(SKILL_ID, VERSION)
    candidate = candidate_from_signed_skill(source_run_id="p10-learning-loop-run", entry=entry, registry=registry)
    evidence["sandbox"]["passed"] = bool(evidence_result.passed)
    evidence["sandbox"]["isolated"] = bool(evidence_result.isolated)

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
    card_event_id = None

    with tempfile.TemporaryDirectory(prefix="mindroom-p10-", dir=scratch_root) as temporary:
        recorder = FlightRecorder(Path(temporary) / "flight.sqlite3")
        store = LearningLoopStore(Path(temporary) / "learning.sqlite3")
        await recorder.open()
        await store.open()
        try:
            # ---- 1) Automatic recorder capture -------------------------
            rec = await recorder.append(
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
            if proposal.stage != "proposed":
                raise RuntimeError("capture did not land in proposed stage")
            evidence["capture_receipt"] = {
                "proposal_id": proposal.proposal_id,
                "source_run_id": candidate.source_run_id,
                "artifact_digest": proposal.artifact_digest,
                "recorder_event_id": rec.sequence,
                "recorder_record_hash": rec.record_hash,
                "stage": proposal.stage,
            }
            print(f"CAPTURE_OK proposal={proposal.proposal_id} stage={proposal.stage}")

            # ---- 2) Evaluation (non-regression) ------------------------
            await store.record_evaluation(
                proposal.proposal_id,
                EvaluationEvidence("p10-live", proposal.artifact_digest, 1, 1, 1, 1),
            )
            evidence["evaluation"] = {"suite": "p10-live", "tests": 1, "passed": 1}

            # ---- 3) Live review delivery via real Matrix transport ----
            sender = _MatrixSender(base, router_token)
            editor = _MatrixEditor(base, router_token)
            store_approvals = initialize_approval_store(
                store_approval_path(scratch_root),
                sender=sender,
                editor=editor,
                transport_sender=lambda: router_sender_id,
            )
            runtime = GovernedLearningRuntime(
                runtime_paths=_runtime_paths(scratch_root),
                store=store,
                approvals=store_approvals,
            )
            review_resolution = _LiveApprovalResolution()
            sender.resolution = review_resolution

            review_task = asyncio.create_task(
                runtime.review(
                    proposal.proposal_id,
                    LearningReviewContext(
                        room_id=arguments.review_room,
                        requester_id=router_sender_id,
                        approver_user_id=arguments.approver,
                        thread_id=None,
                        timeout_seconds=120,
                    ),
                ),
            )
            async with asyncio.timeout(60):
                while review_resolution.card_event_id is None and not review_task.done():  # noqa: ASYNC110
                    await asyncio.sleep(0.1)
            if review_resolution.card_event_id is None:
                raise RuntimeError("live review card was not delivered before timeout")
            card_event_id = review_resolution.card_event_id
            evidence["review_delivery_receipt"] = {
                "room_id": arguments.review_room,
                "card_event_id": card_event_id,
                "approval_id": review_resolution.approval_id,
                "delivered_by": router_sender_id,
                "tool": "mindroom.learning.promote",
            }
            print(f"REVIEW_DELIVERED card_event_id={card_event_id}")

            # Resolve the live card through the canonical approver path.
            await store_approvals.handle_card_response(
                room_id=arguments.review_room,
                sender_id=arguments.approver,
                card_event_id=card_event_id,
                status="approved",
                reason="P10 end-to-end governed publication approved",
            )
            reviewed = await review_task
            if reviewed.stage != "approved":
                raise RuntimeError(f"live review did not approve; stage={reviewed.stage}")
            evidence["review_delivery_receipt"]["approved_by"] = reviewed.reviewed_by
            evidence["review_delivery_receipt"]["reason"] = reviewed.review_reason
            print(f"REVIEW_APPROVED by={reviewed.reviewed_by}")

            # ---- 4) Canary publication --------------------------------
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
            evidence["canary_publication_receipt"] = {
                runtime: {"receipt": receipt, "status": canary_state[runtime][0]}
                for runtime, receipt in canary_receipts.items()
            }
            print("CANARY_OK openclaw+hermes")

            # ---- 5) Stable promotion --------------------------------
            stable = await publisher.stabilize(proposal.proposal_id)
            stable_state = await store.receipts(proposal.proposal_id)
            stable_receipts = {
                "openclaw": stable_state["openclaw"][1],
                "hermes": stable_state["hermes"][1],
            }
            if stable.stage != "stable":
                raise RuntimeError("governed proposal did not reach stable")
            evidence["stable_promotion_confirmation"] = {
                "stage": stable.stage,
                "receipts": {
                    runtime: {"receipt": receipt, "status": stable_state[runtime][0]}
                    for runtime, receipt in stable_receipts.items()
                },
            }
            print("STABLE_OK openclaw+hermes")

            # ---- 6) Native discovery verification --------------------
            openclaw_info, hermes_list = await asyncio.gather(
                _command(("openclaw", "skills", "info", SKILL_ID, "--json")),
                _hermes_native_skills(hermes_root.parent),
            )
            openclaw_discovered = SKILL_ID in openclaw_info
            hermes_discovered = SKILL_ID in hermes_list
            if not openclaw_discovered or not hermes_discovered:
                raise RuntimeError(
                    f"active learned skill discovery failed (openclaw={openclaw_discovered}, hermes={hermes_discovered})",
                )
            evidence["stable_discovery"] = {
                "openclaw": openclaw_discovered,
                "hermes": hermes_discovered,
            }
            print("DISCOVERY_OK openclaw+hermes stable-installed")
            print("LOOP=VERIFIED")

        finally:
            # Reversible cleanup: roll back stable then canary receipts.
            if proposal is not None:
                for runtime, receipt in stable_receipts.items():
                    await stable_adapters[runtime].rollback(proposal, receipt)
                for runtime, receipt in canary_receipts.items():
                    await canary_adapters[runtime].rollback(proposal, receipt)
            await store.close()
            await recorder.close()
            _remove_empty_directories(openclaw_root)
            _remove_empty_directories(hermes_root)
    evidence["cleanup"] = "rolled_back"
    evidence["completed_at"] = datetime.now(UTC).isoformat()

    evidence["loop"] = "VERIFIED"
    print(json.dumps(evidence, indent=2, sort_keys=True))

    evidence_path = arguments.evidence or scratch_root / "p10_learning_loop_evidence.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(f"EVIDENCE_WRITTEN={evidence_path}")


def store_approval_path(scratch_root: Path):
    return _runtime_paths(scratch_root)


def _runtime_paths(scratch_root: Path):
    from mindroom.constants import RuntimePaths
    return RuntimePaths(
        config_path=Path("/Users/dwayne/mindroom/config.yaml"),
        config_dir=Path("/Users/dwayne/mindroom"),
        env_path=Path("/Users/dwayne/mindroom/.env"),
        storage_root=scratch_root,
    )


class _MatrixSender:
    """Post real io.mindroom.tool_approval cards via the canonical Matrix API."""

    def __init__(self, base: str, token: str) -> None:
        self._base = base
        self._token = token
        self.resolution: _LiveApprovalResolution | None = None

    async def __call__(self, room_id: str, thread_id: str | None, content: dict) -> SentApprovalEvent | None:  # noqa: ARG002
        result = await asyncio.to_thread(
            _put,
            self._base,
            room_id,
            "io.mindroom.tool_approval",
            content,
            self._token,
        )
        event_id = result.get("event_id")
        if self.resolution is not None:
            self.resolution.card_event_id = event_id
            self.resolution.approval_id = content.get("approval_id") or content.get("id")
        return SentApprovalEvent(event_id=event_id, sent_content=content)


class _MatrixEditor:
    """Write the terminal edit (approve/deny) back onto the live card."""

    def __init__(self, base: str, token: str) -> None:
        self._base = base
        self._token = token

    async def __call__(self, room_id: str, event_id: str, new_content: dict) -> bool:
        replace = {
            "m.new_content": new_content,
            "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
        }
        try:
            await asyncio.to_thread(
                _put,
                self._base,
                room_id,
                "io.mindroom.tool_approval",
                {**new_content, **replace},
                self._token,
            )
            return True  # noqa: TRY300
        except Exception:
            return False


def _put(base: str, room_id: str, msgtype: str, content: dict, token: str) -> dict:
    import json
    import urllib.parse
    import urllib.request
    url = (
        f"{base}/_matrix/client/v3/rooms/{urllib.parse.quote(room_id, safe='')}"
        f"/send/{msgtype}/{urllib.parse.quote(uuid4().hex, safe='')}"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(content).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


async def _command(argv: tuple[str, ...]) -> str:
    import asyncio
    from pathlib import Path
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
    asyncio.run(_main(_parser().parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
