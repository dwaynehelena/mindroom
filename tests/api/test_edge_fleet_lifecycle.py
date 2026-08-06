"""Tests for optional edge-fleet application mounting policy."""

# ruff: noqa: ANN001, ANN202, D103

from __future__ import annotations

import base64
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from mindroom.api.main import _edge_fleet_from_runtime_paths, _mount_edge_fleet, verify_user
from mindroom.constants import resolve_primary_runtime_paths


def _paths(tmp_path, value: str | None, *, enabled: bool = True):
    base = resolve_primary_runtime_paths()
    process_env = dict(base.process_env)
    process_env.pop("MINDROOM_EDGE_FLEET_ENROLLMENT_KEY", None)
    process_env.pop("MINDROOM_EDGE_FLEET_ENABLED", None)
    if enabled:
        process_env["MINDROOM_EDGE_FLEET_ENABLED"] = "true"
    if value is not None:
        process_env["MINDROOM_EDGE_FLEET_ENROLLMENT_KEY"] = value
    return replace(base, storage_root=tmp_path, process_env=process_env, env_file_values={})


def test_edge_fleet_is_disabled_when_key_is_absent(tmp_path) -> None:
    assert _edge_fleet_from_runtime_paths(_paths(tmp_path, None)) is None


def test_edge_fleet_is_disabled_when_flag_is_false(tmp_path) -> None:
    encoded = base64.urlsafe_b64encode(b"e" * 32).decode().rstrip("=")
    assert _edge_fleet_from_runtime_paths(_paths(tmp_path, encoded, enabled=False)) is None


def test_edge_fleet_requires_strong_decoded_key(tmp_path) -> None:
    weak = base64.urlsafe_b64encode(b"weak").decode().rstrip("=")
    fleet = _edge_fleet_from_runtime_paths(_paths(tmp_path, weak))
    assert fleet is None  # Returns None with a warning log instead of raising


def test_edge_fleet_uses_runtime_storage_when_enabled(tmp_path) -> None:
    encoded = base64.urlsafe_b64encode(b"e" * 32).decode().rstrip("=")
    fleet = _edge_fleet_from_runtime_paths(_paths(tmp_path, encoded))
    assert fleet is not None
    assert fleet._path == tmp_path / "edge_fleet.db"


def test_disabled_fleet_mounts_no_node_or_coordinator_routes() -> None:
    app = FastAPI()
    _mount_edge_fleet(app, None)
    assert not [route for route in app.routes if route.path.startswith("/api/edge-fleet")]


def test_enabled_fleet_mounts_authenticated_coordinator_routes(tmp_path) -> None:
    encoded = base64.urlsafe_b64encode(b"e" * 32).decode().rstrip("=")
    fleet = _edge_fleet_from_runtime_paths(_paths(tmp_path, encoded))
    assert fleet is not None
    app = FastAPI()
    _mount_edge_fleet(app, fleet)
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    assert any(route.path == "/api/edge-fleet/enroll" for route in routes)
    admin = [route for route in routes if route.path.startswith("/api/edge-fleet-admin/")]
    assert admin
    assert all(any(dependency.call is verify_user for dependency in route.dependant.dependencies) for route in admin)