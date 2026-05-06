"""Minimal presets schema for backward compatibility.

This module provides the Suggestion schema used by the suggestions service.
The legacy preset functionality has been removed in favor of per-client presets
via ClientSettingsService.
"""

from pydantic import BaseModel, Field


class Suggestion(BaseModel):
    """A quick prompt suggestion for the chat interface."""

    label: str = Field(..., min_length=1, description="Display label for the suggestion")
    text: str = Field(..., min_length=1, description="The prompt text to use when selected")


__all__ = ["Suggestion"]
