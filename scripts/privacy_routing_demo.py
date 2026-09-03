#!/usr/bin/env python3
"""Run live governed local, cloud, isolated-tool, and denial routes."""

# The demo intentionally keeps the full evidence sequence in one auditable lifecycle.
# ruff: noqa: C901, PLR0915

from __future__ import annotations

import argparse
import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from mindroom.config.privacy import PrivacyRouteConfig, PrivacyRoutingConfig
from mindroom.privacy_composition import ConfiguredPrivacyExecutors, build_configured_privacy_dispatcher
from mindroom.privacy_dispatch import PrivacyDispatchStore
from mindroom.privacy_executors import DockerJsonToolExecutor, OpenAICompatibleModelExecutor
from mindroom.privacy_router import RouteRequest, RoutingError, Sensitivity

IMAGE = "python@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--local-model", default="llama3.2:3b")
    parser.add_argument("--sandbox-image", default=IMAGE)
    return parser


def _route(
    *,
    kind: Literal["model", "tool"],
    executor: str,
    location: Literal["local", "cloud"],
    residency: str,
    sensitivity: Literal["public", "internal", "confidential", "restricted"],
    capabilities: frozenset[str],
    cost: int,
    isolated: bool,
) -> PrivacyRouteConfig:
    return PrivacyRouteConfig(
        kind=kind,
        executor=executor,
        location=location,
        residency=residency,
        max_sensitivity=sensitivity,
        capabilities=capabilities,
        cost_microunits=cost,
        isolated=isolated,
    )


def _policy(
    *,
    kind: str,
    sensitivity: Sensitivity,
    capability: str,
    residencies: frozenset[str],
    isolation: bool = False,
) -> RouteRequest:
    return RouteRequest(
        kind=kind,
        sensitivity=sensitivity,
        capabilities=frozenset({capability}),
        allowed_residencies=residencies,
        budget_microunits=100,
        require_isolation=isolation,
    )


async def _hermes_model(executable: str, prompt: str) -> str:
    process = await asyncio.create_subprocess_exec(
        executable,
        "--ignore-rules",
        "-z",
        prompt,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=180)
    except TimeoutError:
        process.kill()
        await process.wait()
        message = "cloud model demonstration timed out"
        raise RuntimeError(message) from None
    if process.returncode != 0:
        message = "cloud model demonstration failed"
        raise RuntimeError(message)
    return stdout.decode(errors="replace")


async def _main(args: argparse.Namespace) -> None:
    scratch_root = args.scratch_root.expanduser().resolve(strict=True)
    docker = shutil.which("docker")
    hermes = shutil.which("hermes")
    if docker is None or hermes is None:
        message = "privacy routing demo requires Docker and Hermes"
        raise RuntimeError(message)

    local_model = OpenAICompatibleModelExecutor(
        origin="http://127.0.0.1:11434/v1",
        model=args.local_model,
        timeout_seconds=180,
    )

    async def cloud_model(prompt: str) -> str:
        return await _hermes_model(hermes, prompt)

    isolated_tool = DockerJsonToolExecutor(
        docker_executable=str(Path(docker).resolve()),
        image=args.sandbox_image,
        command=(
            "python",
            "-c",
            "import json,sys; x=json.load(sys.stdin); "
            "print(json.dumps({'normalized': sorted(x['values'])}, separators=(',',':')))",
        ),
    )
    config = PrivacyRoutingConfig(
        enabled=True,
        routes={
            "local-private": _route(
                kind="model",
                executor="ollama-local",
                location="local",
                residency="AU",
                sensitivity="restricted",
                capabilities=frozenset({"reasoning"}),
                cost=5,
                isolated=False,
            ),
            "cloud-public": _route(
                kind="model",
                executor="hermes-cloud",
                location="cloud",
                residency="US",
                sensitivity="confidential",
                capabilities=frozenset({"cloud-reasoning", "reasoning"}),
                cost=1,
                isolated=False,
            ),
            "isolated-transform": _route(
                kind="tool",
                executor="docker-transform",
                location="local",
                residency="AU",
                sensitivity="restricted",
                capabilities=frozenset({"transform"}),
                cost=1,
                isolated=True,
            ),
        },
    )
    with tempfile.TemporaryDirectory(prefix="mindroom-privacy-demo-", dir=scratch_root) as temporary:
        store = PrivacyDispatchStore(Path(temporary) / "dispatch.sqlite3")
        await store.open()
        try:
            dispatcher = build_configured_privacy_dispatcher(
                config=config,
                executors=ConfiguredPrivacyExecutors(
                    models={"ollama-local": local_model, "hermes-cloud": cloud_model},
                    tools={"docker-transform": isolated_tool},
                ),
                store=store,
            )
            local_result, local_receipt = await dispatcher.dispatch(
                request_id="live-local-restricted",
                policy=_policy(
                    kind="model",
                    sensitivity=Sensitivity.RESTRICTED,
                    capability="reasoning",
                    residencies=frozenset({"AU", "US"}),
                ),
                payload={"prompt": "Reply with exactly PRIVACY_LOCAL_OK"},
            )
            if local_receipt.route_id != "local-private" or "PRIVACY_LOCAL_OK" not in str(local_result):
                message = "restricted local route did not return its expected evidence"
                raise RuntimeError(message)
            print("restricted=local-private executed=true")

            cloud_result, cloud_receipt = await dispatcher.dispatch(
                request_id="live-cloud-public",
                policy=_policy(
                    kind="model",
                    sensitivity=Sensitivity.PUBLIC,
                    capability="cloud-reasoning",
                    residencies=frozenset({"US"}),
                ),
                payload={"prompt": "Reply with exactly PRIVACY_CLOUD_OK"},
            )
            if cloud_receipt.route_id != "cloud-public" or "PRIVACY_CLOUD_OK" not in str(cloud_result):
                message = "public cloud route did not return its expected evidence"
                raise RuntimeError(message)
            print("public=cloud-public executed=true")

            tool_result, tool_receipt = await dispatcher.dispatch(
                request_id="live-isolated-tool",
                policy=_policy(
                    kind="tool",
                    sensitivity=Sensitivity.RESTRICTED,
                    capability="transform",
                    residencies=frozenset({"AU"}),
                    isolation=True,
                ),
                payload={"arguments": {"values": [3, 1, 2]}},
            )
            if tool_receipt.route_id != "isolated-transform" or tool_result != {"normalized": [1, 2, 3]}:
                message = "isolated tool route did not return its expected evidence"
                raise RuntimeError(message)
            print("tool=isolated-transform network=none executed=true")

            try:
                await dispatcher.dispatch(
                    request_id="denied-restricted-cloud",
                    policy=_policy(
                        kind="model",
                        sensitivity=Sensitivity.RESTRICTED,
                        capability="cloud-reasoning",
                        residencies=frozenset({"US"}),
                    ),
                    payload={"prompt": "must not execute"},
                )
            except RoutingError:
                print("restricted-cloud=denied-before-execution")
            else:
                message = "restricted cloud-only request was not denied"
                raise RuntimeError(message)

            local_fallback_calls = 0

            async def count_local(prompt: str) -> str:
                nonlocal local_fallback_calls
                local_fallback_calls += 1
                return await local_model(prompt)

            async def fail_cloud(_prompt: str) -> str:
                message = "known cloud failure"
                raise RuntimeError(message)

            failure_dispatcher = build_configured_privacy_dispatcher(
                config=config,
                executors=ConfiguredPrivacyExecutors(
                    models={"ollama-local": count_local, "hermes-cloud": fail_cloud},
                    tools={"docker-transform": isolated_tool},
                ),
                store=store,
            )
            try:
                await failure_dispatcher.dispatch(
                    request_id="selected-cloud-failure",
                    policy=_policy(
                        kind="model",
                        sensitivity=Sensitivity.PUBLIC,
                        capability="reasoning",
                        residencies=frozenset({"AU", "US"}),
                    ),
                    payload={"prompt": "must not fall back"},
                )
            except RuntimeError:
                pass
            else:
                message = "selected cloud failure unexpectedly succeeded"
                raise RuntimeError(message)
            if local_fallback_calls != 0 or (await store.status("selected-cloud-failure"))[0] != "failed":
                message = "selected route failure fell back or was not durably failed"
                raise RuntimeError(message)
            print("selected-failure=failure-recorded fallback=false")
        finally:
            await store.close()
    print("ledger=closed cleanup=verified")


def main() -> int:
    """Run the live privacy-routing demonstration."""
    asyncio.run(_main(_parser().parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
