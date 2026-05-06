"""Spotify authentication service."""

from backend.services.spotify_auth.auth import (
    DEFAULT_USER_EMAIL,
    get_credentials,
    get_spotify_client,
    process_auth_callback,
    retry_on_rate_limit,
    store_credentials,
)

__all__ = [
    "DEFAULT_USER_EMAIL",
    "get_credentials",
    "get_spotify_client",
    "process_auth_callback",
    "retry_on_rate_limit",
    "store_credentials",
]
