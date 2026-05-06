"""Service for managing client profiles for MCP server filtering."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Sequence

from ..schemas.client_profiles import ClientProfile, ClientProfileUpdate

logger = logging.getLogger(__name__)


class ClientProfileService:
    """Manage client profiles for MCP server filtering.

    Profiles are stored as individual JSON files in a directory,
    one file per profile (e.g., `cli-default.json`).
    """

    def __init__(self, profiles_dir: Path) -> None:
        self._dir = profiles_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._cache: dict[str, ClientProfile] = {}
        self._load_all()

    def _profile_path(self, profile_id: str) -> Path:
        """Return the file path for a profile."""
        return self._dir / f"{profile_id}.json"

    def _load_all(self) -> None:
        """Load all profiles from disk into cache."""
        self._cache.clear()
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                profile = ClientProfile.model_validate(data)
                self._cache[profile.profile_id] = profile
            except Exception as exc:
                logger.warning("Failed to load profile from %s: %s", path, exc)

    def _save_profile(self, profile: ClientProfile) -> None:
        """Save a profile to disk."""
        path = self._profile_path(profile.profile_id)
        data = profile.model_dump(mode="json")
        path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    async def list_profiles(self) -> list[ClientProfile]:
        """Return all profiles sorted by ID."""
        async with self._lock:
            return sorted(self._cache.values(), key=lambda p: p.profile_id)

    async def get_profile(self, profile_id: str) -> ClientProfile | None:
        """Get a profile by ID, or None if not found."""
        async with self._lock:
            return self._cache.get(profile_id)

    async def get_profile_or_default(
        self,
        profile_id: str,
        default_servers: Sequence[str] | None = None,
    ) -> ClientProfile:
        """Get a profile by ID, creating a default if not found."""
        async with self._lock:
            profile = self._cache.get(profile_id)
            if profile is not None:
                return profile

            # Create a default profile
            profile = ClientProfile(
                profile_id=profile_id,
                enabled_servers=list(default_servers or []),
                description=f"Auto-created profile for {profile_id}",
            )
            self._cache[profile_id] = profile
            self._save_profile(profile)
            logger.info("Created default profile '%s'", profile_id)
            return profile

    async def create_profile(self, profile: ClientProfile) -> ClientProfile:
        """Create a new profile. Raises KeyError if already exists."""
        async with self._lock:
            if profile.profile_id in self._cache:
                raise KeyError(f"Profile already exists: {profile.profile_id}")

            self._cache[profile.profile_id] = profile
            self._save_profile(profile)
            logger.info("Created profile '%s'", profile.profile_id)
            return profile

    async def update_profile(
        self,
        profile_id: str,
        updates: ClientProfileUpdate,
    ) -> ClientProfile:
        """Update an existing profile. Raises KeyError if not found."""
        async with self._lock:
            existing = self._cache.get(profile_id)
            if existing is None:
                raise KeyError(f"Profile not found: {profile_id}")

            data = existing.model_dump()
            update_data = updates.model_dump(exclude_none=True)
            data.update(update_data)

            updated = ClientProfile.model_validate(data)
            self._cache[profile_id] = updated
            self._save_profile(updated)
            logger.info("Updated profile '%s'", profile_id)
            return updated

    async def delete_profile(self, profile_id: str) -> bool:
        """Delete a profile. Returns True if deleted, False if not found."""
        async with self._lock:
            if profile_id not in self._cache:
                return False

            del self._cache[profile_id]
            path = self._profile_path(profile_id)
            if path.exists():
                path.unlink()
            logger.info("Deleted profile '%s'", profile_id)
            return True

    async def add_server(self, profile_id: str, server_id: str) -> ClientProfile:
        """Add a server to a profile's enabled list."""
        async with self._lock:
            existing = self._cache.get(profile_id)
            if existing is None:
                raise KeyError(f"Profile not found: {profile_id}")

            if server_id in existing.enabled_servers:
                return existing  # Already enabled

            updated = ClientProfile(
                profile_id=profile_id,
                enabled_servers=[*existing.enabled_servers, server_id],
                description=existing.description,
            )
            self._cache[profile_id] = updated
            self._save_profile(updated)
            logger.info("Added server '%s' to profile '%s'", server_id, profile_id)
            return updated

    async def remove_server(self, profile_id: str, server_id: str) -> ClientProfile:
        """Remove a server from a profile's enabled list."""
        async with self._lock:
            existing = self._cache.get(profile_id)
            if existing is None:
                raise KeyError(f"Profile not found: {profile_id}")

            if server_id not in existing.enabled_servers:
                return existing  # Not in list

            updated = ClientProfile(
                profile_id=profile_id,
                enabled_servers=[s for s in existing.enabled_servers if s != server_id],
                description=existing.description,
            )
            self._cache[profile_id] = updated
            self._save_profile(updated)
            logger.info("Removed server '%s' from profile '%s'", server_id, profile_id)
            return updated

    async def reload(self) -> None:
        """Reload all profiles from disk."""
        async with self._lock:
            self._load_all()
            logger.info("Reloaded %d profile(s)", len(self._cache))


__all__ = ["ClientProfileService"]
