"""Dry-run CD checks for intent 260816-edge-fleet-mesh-learning (4.1).

Importing ``mindroom.api.main`` runs composition-root mount. Force the
process flag off *before* that import so this script cannot construct a
fleet against the operator's live ``~/.mindroom`` store.
"""

from __future__ import annotations

import os

# Fail-closed for this process only. Do not write the live .env.
os.environ["MINDROOM_EDGE_FLEET_ENABLED"] = "false"

import base64
from dataclasses import replace
from pathlib import Path

import yaml
from fastapi import FastAPI

from mindroom.api.main import _edge_fleet_from_runtime_paths, _mount_edge_fleet
from mindroom.constants import resolve_primary_runtime_paths
from mindroom.handshake_inspect import inspect_openclaw


def main() -> None:
    repo = Path(__file__).resolve().parents[2]

    base = resolve_primary_runtime_paths()
    process_env = dict(base.process_env)
    process_env["MINDROOM_EDGE_FLEET_ENABLED"] = "false"
    process_env.pop("MINDROOM_EDGE_FLEET_ENROLLMENT_KEY", None)
    process_env.pop("MINDROOM_EDGE_FLEET_NODE_ALLOWLIST", None)
    paths = replace(base, process_env=process_env, env_file_values={})
    assert _edge_fleet_from_runtime_paths(paths) is None, "missing/false flag must unmount"
    print("flag-off-ok")

    process_env["MINDROOM_EDGE_FLEET_ENABLED"] = "false"
    encoded = base64.urlsafe_b64encode(b"e" * 32).decode().rstrip("=")
    process_env["MINDROOM_EDGE_FLEET_ENROLLMENT_KEY"] = encoded
    paths = replace(base, process_env=process_env, env_file_values={})
    fleet = _edge_fleet_from_runtime_paths(paths)
    assert fleet is None, "false flag must unmount even with a valid key"
    print("flag-false-ok")

    app = FastAPI()
    _mount_edge_fleet(app, None)
    assert not [
        route
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/edge-fleet")
    ]
    print("unmounted-routes-ok")

    result = inspect_openclaw()
    assert result.outcome == "surface_absent", result
    assert result.implement_fr4_2 is False
    assert result.unread_switch_removed is True
    print("handshake-surface-absent-ok")

    config = yaml.safe_load((repo / "config.yaml").read_text())
    openclaw = config["agents"]["openclaw"]
    assert openclaw.get("learning") is False, openclaw.get("learning")
    print("openclaw-learning-false-ok")

    example = (repo / ".env.example").read_text()
    for key in (
        "MINDROOM_EDGE_FLEET_ENABLED",
        "MINDROOM_EDGE_FLEET_ENROLLMENT_KEY",
        "MINDROOM_EDGE_FLEET_NODE_ALLOWLIST",
        "MINDROOM_EDGE_FLEET_PATH",
    ):
        assert key in example, key
    assert "MINDROOM_MESH_ENROLLMENT" not in example
    print("env-example-ok")
    print("cd-dry-run-ok")


if __name__ == "__main__":
    main()
