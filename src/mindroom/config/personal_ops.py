"""Disabled-by-default Personal Ops runtime configuration."""

# ruff: noqa: EM101, TRY003

from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

_GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class PersonalOpsConfig(BaseModel):
    """Strict configuration for the owned daily Personal Ops service."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    timezone: str = "UTC"
    hour: int = Field(default=8, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    github_repository: str | None = None
    item_limit: int = Field(default=20, ge=1, le=100)
    matrix_origin: str = "http://127.0.0.1:8008"
    room_id: str | None = None
    delivery_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    executor_agent: str | None = None
    mindroom_write_tools: tuple[str, ...] = ()
    openclaw_gateway_url: str | None = None
    openclaw_token_env: str = "OPENCLAW_GATEWAY_TOKEN"  # noqa: S105 - variable name, not a token
    openclaw_write_methods: tuple[str, ...] = ()
    execution_timeout_seconds: float = Field(default=30.0, gt=0, le=120)

    @model_validator(mode="after")
    def validate_enabled_service(self) -> PersonalOpsConfig:  # noqa: C901, PLR0912
        """Require complete, bounded routing before the service may enable."""
        try:
            ZoneInfo(self.timezone)
        except (TypeError, ZoneInfoNotFoundError) as exc:
            message = "personal_ops.timezone must be a valid IANA timezone"
            raise ValueError(message) from exc
        if not self.enabled:
            return self
        if self.github_repository is None or _GITHUB_REPOSITORY.fullmatch(self.github_repository) is None:
            message = "enabled personal_ops requires github_repository as owner/repository"
            raise ValueError(message)
        if self.room_id is None or not self.room_id.startswith("!") or ":" not in self.room_id:
            message = "enabled personal_ops requires a canonical Matrix room_id"
            raise ValueError(message)
        if self.executor_agent is None or not self.executor_agent.strip():
            raise ValueError("enabled personal_ops requires executor_agent")
        if not self.mindroom_write_tools:
            raise ValueError("enabled personal_ops requires mindroom_write_tools")
        if not self.openclaw_write_methods:
            raise ValueError("enabled personal_ops requires openclaw_write_methods")
        for value in self.mindroom_write_tools:
            if not value or value.strip() != value or "." not in value:
                raise ValueError("personal_ops MindRoom write allowlist requires exact tool.function names")
        for value in self.openclaw_write_methods:
            if not value or value.strip() != value or any(character.isspace() for character in value):
                raise ValueError("personal_ops OpenClaw write allowlist requires exact method names")
        if self.openclaw_gateway_url is None or not self.openclaw_gateway_url.startswith(("ws://", "wss://")):
            raise ValueError("enabled personal_ops requires a ws:// or wss:// openclaw_gateway_url")
        if not self.openclaw_token_env.isidentifier():
            raise ValueError("personal_ops.openclaw_token_env must be an environment variable name")
        return self
