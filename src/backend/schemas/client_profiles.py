"""Schemas for client profiles defining which MCP servers to use."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClientProfile(BaseModel):
    """A client profile defining which MCP servers to use."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Annotated[
        str,
        Field(
            ...,
            min_length=1,
            pattern=r"^[a-z0-9_-]+$",
            description="Unique identifier for the profile (lowercase, alphanumeric, -, _)",
        ),
    ]
    enabled_servers: list[str] = Field(
        default_factory=list,
        description="List of MCP server IDs that are enabled for this profile",
    )
    description: str = Field(
        default="",
        description="Human-readable description of the profile's purpose",
    )

    @field_validator("enabled_servers", mode="before")
    @classmethod
    def _normalize_enabled_servers(cls, value: list[str] | None) -> list[str]:
        if value is None:
            return []
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for item in value:
            if isinstance(item, str) and item not in seen:
                seen.add(item)
                unique.append(item)
        return unique


class ClientProfileList(BaseModel):
    """Response model for listing all profiles."""

    profiles: list[ClientProfile] = Field(default_factory=list)


class ClientProfileUpdate(BaseModel):
    """Request model for updating a profile."""

    model_config = ConfigDict(extra="forbid")

    enabled_servers: list[str] | None = Field(
        default=None,
        description="New list of enabled server IDs (replaces existing)",
    )
    description: str | None = Field(
        default=None,
        description="New description",
    )


class ClientProfileCreate(BaseModel):
    """Request model for creating a new profile."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Annotated[
        str,
        Field(
            ...,
            min_length=1,
            pattern=r"^[a-z0-9_-]+$",
            description="Unique identifier for the profile",
        ),
    ]
    enabled_servers: list[str] = Field(
        default_factory=list,
        description="List of MCP server IDs to enable",
    )
    description: str = Field(
        default="",
        description="Human-readable description",
    )


__all__ = [
    "ClientProfile",
    "ClientProfileList",
    "ClientProfileUpdate",
    "ClientProfileCreate",
]
