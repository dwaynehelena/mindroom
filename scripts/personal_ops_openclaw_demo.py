#!/usr/bin/env python3
"""Send and remove one reversible Personal Ops proof through OpenClaw Gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import cast

from mindroom.arip import canonical_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, required=True, help="supported Node.js executable")
    parser.add_argument("--openclaw-root", type=Path, default=Path.home() / ".npm-global/lib/node_modules/openclaw")
    parser.add_argument("--config", type=Path, default=Path.home() / ".openclaw/openclaw.json")
    parser.add_argument("--worker", type=Path, default=Path(__file__).with_name("openclaw_gateway_worker.mjs"))
    return parser


def _target(config_path: Path) -> str:
    config = json.loads(config_path.read_bytes())
    groups = config["channels"]["telegram"]["groups"]
    for group in groups.values():
        allowed = group.get("allowFrom", [])
        if allowed:
            return str(allowed[0])
    message = "OpenClaw Telegram configuration has no private proof target"
    raise RuntimeError(message)


async def _call(args: argparse.Namespace, method: str, params: dict[str, object]) -> object:
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN")
    if not token:
        message = "OPENCLAW_GATEWAY_TOKEN is unavailable"
        raise RuntimeError(message)
    request = {
        "url": "ws://127.0.0.1:18789",
        "token": token,
        "method": method,
        "params": params,
        "timeoutMs": 30_000,
        "packageRoot": str(args.openclaw_root.resolve()),
    }
    process = await asyncio.create_subprocess_exec(
        str(args.node.resolve()),
        str(args.worker.resolve()),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _stderr = await asyncio.wait_for(process.communicate(canonical_json(request) + b"\n"), timeout=35)
    response = json.loads(stdout)
    if process.returncode != 0 or not isinstance(response, dict) or response.get("ok") is not True:
        message = "OpenClaw gateway proof call failed"
        raise RuntimeError(message)
    return response.get("result")


async def _run(args: argparse.Namespace) -> None:
    target = _target(args.config)
    key = secrets.token_hex(16)
    sent = await _call(
        args,
        "send",
        {
            "to": target,
            "channel": "telegram",
            "message": "MindRoom Personal Ops reversible gateway proof",
            "silent": True,
            "idempotencyKey": key,
        },
    )
    if not isinstance(sent, dict):
        message = "OpenClaw send receipt omitted messageId"
        raise TypeError(message)
    sent_result = cast("dict[str, object]", sent)
    if not isinstance(sent_result.get("messageId"), (str, int)):
        message = "OpenClaw send receipt omitted messageId"
        raise TypeError(message)
    await _call(
        args,
        "message.action",
        {
            "channel": "telegram",
            "action": "delete",
            "params": {"to": target, "messageId": str(sent_result["messageId"])},
            "idempotencyKey": f"{key}-delete",
        },
    )
    print("openclaw=sent,receipt-bound,deleted")
    print("cleanup=verified")


def main() -> int:
    """Run the reversible live write."""
    asyncio.run(_run(_parser().parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
