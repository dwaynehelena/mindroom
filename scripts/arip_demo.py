"""P8 ARIP Live Approval Demo.

Wires the real ops-autopilot ApprovalGate against the live Matrix approval
store and a real Matrix sender. A suggested action in the brief is gated:

  * DENY   -> gate returns denied; the orchestrator/executor is BLOCKED
              (gh action never runs) and the card is resolved as denied.
  * APPROVE-> gate returns approved; the executor RUNS the gh action.

The `io.mindroom.tool_approval` card is posted to the Personal room and is
resolvable through the canonical handle_card_response path (the same path a
real Approve/Deny click in the Matrix UI exercises).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

import yaml

from mindroom.approval_manager import SentApprovalEvent, initialize_approval_store
from mindroom.constants import resolve_runtime_paths
from mindroom.config.main import load_config
from mindroom.ops_autopilot.approval.gate import ApprovalGate

ROOM_ID = "!dMnnRiMXoHdqYOVtqJ:localhost"
STATE = yaml.safe_load(Path.home().joinpath(".mindroom/mindroom_data/matrix_state.yaml").read_text())
ACC = STATE["accounts"]["agent_router"]  # router owns approval cards
BASE = "http://127.0.0.1:8008"
APPROVER = "@dwayne:localhost"
SENDER_ID = f"{ACC['username']}@{ACC['domain']}"
TOKEN = ACC["access_token"]


def _put(room_id: str, msgtype: str, content: dict) -> dict:
    url = (
        f"{BASE}/_matrix/client/v3/rooms/{urllib.parse.quote(room_id, safe='')}"
        f"/send/{msgtype}/{urllib.parse.quote(uuid4().hex, safe='')}"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(content).encode("utf-8"),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


class MatrixSender:
    """Post real io.mindroom.tool_approval cards via the canonical Matrix API."""

    async def __call__(self, room_id: str, thread_id: str | None, content: dict) -> SentApprovalEvent | None:
        result = await asyncio.to_thread(_put, room_id, "io.mindroom.tool_approval", content)
        event_id = result.get("event_id")
        print(f"  [sender] CARD posted event_id={event_id}")
        return SentApprovalEvent(event_id=event_id, sent_content=content)


class MatrixEditor:
    """Write the terminal edit (approve/deny) back onto the live card."""

    async def __call__(self, room_id: str, event_id: str, new_content: dict) -> bool:
        status = new_content.get("status")
        replace = {
            "m.new_content": new_content,
            "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
        }
        try:
            await asyncio.to_thread(_put, room_id, "io.mindroom.tool_approval", {**new_content, **replace})
            print(f"  [editor] card {event_id} resolved -> {status}")
            return True
        except Exception as exc:
            print(f"  [editor] failed: {exc}")
            return False


def gh_action(action_desc: str, cmd: list[str]) -> tuple[bool, str]:
    """Run a gh-based action and return (ok, output)."""
    print(f"  [executor] RUN gh action: {action_desc}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        out = (proc.stdout or proc.stderr or "").strip()
        return proc.returncode == 0, out[:300]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def gate_one(store, gate: ApprovalGate, *, round_name: str, action_desc: str, cmd: list[str]) -> None:
    print(f"\n=== ARIP {round_name}: suggested action '{action_desc}' requires Approve/Deny ===")
    brief = f"Suggested action: {action_desc}. Approve to run it, deny to block it."

    # Request approval in the background: the gate posts a card and awaits the decision.
    gate_task = asyncio.create_task(gate.gate(brief, room_id=ROOM_ID))

    # Wait for the live waiter to be bound so we can resolve the card.
    async with asyncio.timeout(30):
        while not store._pending_by_card_event:
            await asyncio.sleep(0.2)

    waiter = list(store._pending_by_card_event.values())[-1]
    print(f"  [gate] live card event_id={waiter.card_event_id} approval_id={waiter.approval_id}")

    # Resolve the card concurrently (the same path a real Approve/Deny click uses).
    status = "denied" if round_name == "DENY" else "approved"
    await store.handle_card_response(
        room_id=ROOM_ID,
        sender_id=APPROVER,
        card_event_id=waiter.card_event_id,
        status=status,
        reason="operator declined" if round_name == "DENY" else None,
    )

    decision = await gate_task
    print(f"  [gate] decision.status={decision.status}")

    if round_name == "DENY":
        assert decision.status == "denied", "Expected deny path"
        print("  [gate] DENY path: executor BLOCKED - gh action NOT executed")
    else:
        assert decision.status == "approved", "Expected approve path"
        ok, out = gh_action(action_desc, cmd)
        print(f"  [executor] gh output: {out!r} ok={ok}")


async def main() -> None:
    runtime_paths = resolve_runtime_paths(
        config_path=Path("/Users/dwayne/mindroom/config.yaml"),
        storage_path=Path.home() / ".mindroom/mindroom_data",
        process_env={"MATRIX_HOMESERVER": "http://127.0.0.1:8008", "MINDROOM_NAMESPACE": ""},
    )
    config = load_config(runtime_paths)

    sender = MatrixSender()
    editor = MatrixEditor()
    store = initialize_approval_store(
        runtime_paths,
        sender=sender,
        editor=editor,
        transport_sender=lambda: SENDER_ID,
    )
    print("approval store initialized:", store is not None)

    gate = ApprovalGate(tool_name="ops_autopilot.gh_action", approver=APPROVER, timeout_seconds=120)

    # DENY path: block execution.
    await gate_one(
        store, gate,
        round_name="DENY",
        action_desc="gh release create --target main (DENIED)",
        cmd=["gh", "release", "create", "v0.0.0-arip-denied", "--target", "main", "--generate-notes"],
    )

    # APPROVE path: execute.
    await gate_one(
        store, gate,
        round_name="APPROVE",
        action_desc="gh pr list --limit 3 (APPROVED)",
        cmd=["gh", "pr", "list", "--limit", "3"],
    )

    print("\n=== ARIP DEMO COMPLETE ===")


asyncio.run(main())