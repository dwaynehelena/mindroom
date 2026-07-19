"""Strict external-runtime configuration with fail-closed defaults."""
# ruff: noqa: C901, EM101, PLR0912, TRY003

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExternalRuntimeInstanceConfig(BaseModel):
    """One allowlisted OpenClaw CLI or Hermes loopback instance."""

    model_config = ConfigDict(extra="forbid")

    runtime: Literal["openclaw", "hermes"]
    agent_name: str
    enabled: bool = False
    owner_user_ids: tuple[str, ...] = ()
    room_ids: tuple[str, ...] = ()
    max_concurrency: int = Field(default=1, ge=1, le=64)
    max_sessions: int = Field(default=1000, ge=1, le=100_000)
    max_waiters: int = Field(default=16, ge=0, le=4096)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    allow_tools: Literal[False] = False
    require_e2ee: Literal[True] = True
    executable: str | None = None
    executable_allowlist: tuple[str, ...] = ()
    agent_id: str | None = None
    endpoint: str | None = None
    endpoint_allowlist: tuple[str, ...] = ()
    approved_port: int | None = Field(default=None, ge=1, le=65535)
    api_key_env: Literal["API_SERVER_KEY"] | None = None
    api_key_file: Path | None = None
    secret_root: Path | None = None
    executable_sha256: str | None = None
    expected_version: str | None = None
    expected_node_version: str | None = None
    deny_all_attestation: Path | None = None

    @model_validator(mode="after")
    def validate_runtime_interface(self) -> ExternalRuntimeInstanceConfig:
        """Require exact interface fields and deny ambiguous secret/network configuration."""
        if self.enabled and (not self.owner_user_ids or not self.room_ids):
            raise ValueError("enabled external runtimes require owner_user_ids and room_ids allowlists")
        if self.runtime == "openclaw":
            if not self.executable or not self.agent_id:
                raise ValueError("OpenClaw requires executable and agent_id")
            if self.enabled and not all((self.executable_sha256, self.expected_version, self.expected_node_version, self.deny_all_attestation)):
                raise ValueError("enabled OpenClaw requires hash, exact version/Node, and deny-all attestation")
            executable = Path(self.executable)
            if not executable.is_absolute() or any(not Path(item).is_absolute() for item in self.executable_allowlist):
                raise ValueError("OpenClaw executable allowlist must contain absolute paths only")
            if self.executable not in self.executable_allowlist:
                raise ValueError("OpenClaw executable must be explicitly allowlisted")
            if self.endpoint or self.api_key_env or self.api_key_file or self.approved_port or self.secret_root:
                raise ValueError("OpenClaw does not accept Hermes endpoint/secret fields")
        else:
            if not self.endpoint:
                raise ValueError("Hermes requires an endpoint")
            if bool(self.api_key_env) == bool(self.api_key_file):
                raise ValueError("Hermes requires exactly one env/file API secret reference")
            if self.executable or self.agent_id or self.executable_sha256 or self.expected_node_version or self.deny_all_attestation:
                raise ValueError("Hermes does not accept OpenClaw executable/attestation fields")
            if self.enabled and not self.expected_version:
                raise ValueError("enabled Hermes requires an exact expected_version")
            parsed = urlparse(self.endpoint)
            try:
                loopback = bool(parsed.hostname) and ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = False
            if (
                parsed.scheme != "http"
                or not loopback
                or parsed.port is None
                or parsed.port != self.approved_port
                or self.endpoint.rstrip("/") not in {x.rstrip("/") for x in self.endpoint_allowlist}
            ):
                raise ValueError("Hermes endpoint must be numeric loopback on the exact allowlisted approved port")
            if self.api_key_file is not None and self.secret_root is None:
                raise ValueError("Hermes file secrets require an approved secret_root")
        return self


class ExternalRuntimesConfig(BaseModel):
    """External runtimes are disabled unless explicitly configured and authorized."""

    model_config = ConfigDict(extra="forbid")

    instances: dict[str, ExternalRuntimeInstanceConfig] = Field(default_factory=dict)
