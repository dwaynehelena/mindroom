#!/usr/bin/env python3
"""Run an authenticated OpenClaw or Hermes Edge Fleet node."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from mindroom.edge_node import EdgeNodeClient, EdgeNodeError, EdgeNodeIdentity, SubprocessJobExecutor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("init", help="create a private node identity")
    initialize.add_argument("--identity", type=Path, required=True)
    initialize.add_argument("--node-id", required=True)
    initialize.add_argument("--runtime", choices=("openclaw", "hermes"), required=True)
    initialize.add_argument("--capability", action="append", default=[], required=True)

    for name in ("enroll", "heartbeat"):
        command = subparsers.add_parser(name)
        command.add_argument("--identity", type=Path, required=True)
        command.add_argument("--url", required=True)

    once = subparsers.add_parser("once", help="lease and execute at most one job")
    once.add_argument("--identity", type=Path, required=True)
    once.add_argument("--url", required=True)
    once.add_argument("--lease-seconds", type=int, default=60)
    once.add_argument("worker", nargs=argparse.REMAINDER)
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.command == "init":
        identity = EdgeNodeIdentity.generate(
            args.identity.expanduser(),
            node_id=args.node_id,
            runtime=args.runtime,
            capabilities=tuple(args.capability),
        )
        print(f"node_id={identity.node_id}")
        print(f"runtime={identity.runtime}")
        print(f"public_key={identity.public_key}")
        return 0

    identity = EdgeNodeIdentity.load(args.identity.expanduser())
    client = EdgeNodeClient(base_url=args.url, identity=identity)
    if args.command == "enroll":
        token = sys.stdin.readline().strip()
        if not token:
            message = "edge enrollment token must be supplied on stdin"
            raise EdgeNodeError(message)
        await client.enroll(token)
        print("edge node enrolled")
        return 0
    if args.command == "heartbeat":
        await client.heartbeat()
        print("edge node heartbeat delivered")
        return 0
    if not args.worker:
        message = "edge node once requires a worker command after --"
        raise EdgeNodeError(message)
    worker = tuple(args.worker[1:] if args.worker[0] == "--" else args.worker)
    completed = await client.run_once(SubprocessJobExecutor(worker), lease_seconds=args.lease_seconds)
    print("edge node job completed" if completed else "edge node queue empty")
    return 0


def main() -> int:
    """Parse arguments and execute one bounded node operation."""
    try:
        return asyncio.run(_run(_parser().parse_args()))
    except EdgeNodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
