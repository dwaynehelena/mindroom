"""Strict site configuration for governed privacy routes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PrivacyRouteConfig(BaseModel):
    """One site-attested model or tool executor route."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["model", "tool"]
    executor: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    location: Literal["local", "cloud"]
    residency: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    max_sensitivity: Literal["public", "internal", "confidential", "restricted"]
    capabilities: frozenset[str] = Field(min_length=1, max_length=128)
    cost_microunits: int = Field(ge=0)
    isolated: bool
    privileges: frozenset[str] = Field(default_factory=frozenset, max_length=128)
    healthy: bool = True
    timeout_seconds: float = Field(default=60.0, gt=0, le=3600, allow_inf_nan=False)

    @field_validator("capabilities", "privileges")
    @classmethod
    def validate_bounded_names(cls, value: frozenset[str]) -> frozenset[str]:
        """Reject blank or oversized capability and privilege labels."""
        if any(not item.strip() or len(item) > 128 for item in value):
            msg = "privacy route capabilities and privileges must be bounded non-empty names"
            raise ValueError(msg)
        return value


class PrivacyRoutingConfig(BaseModel):
    """Disabled-by-default governed privacy-routing configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    routes: dict[str, PrivacyRouteConfig] = Field(default_factory=dict, max_length=256)

    @model_validator(mode="after")
    def validate_enabled_routes(self) -> PrivacyRoutingConfig:
        """Require bounded unique route identities whenever routing is enabled."""
        if self.enabled and not self.routes:
            msg = "enabled privacy routing requires at least one route"
            raise ValueError(msg)
        invalid = [
            route_id
            for route_id in self.routes
            if not route_id
            or len(route_id) > 128
            or any(character not in _ROUTE_ID_CHARACTERS for character in route_id)
        ]
        if invalid:
            msg = "privacy route IDs must contain only letters, numbers, dot, dash, and underscore"
            raise ValueError(msg)
        return self


_ROUTE_ID_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
