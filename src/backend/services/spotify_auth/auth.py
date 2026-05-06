"""Spotify authentication service.

OAuth 2.0 authentication flow for Spotify Web API using spotipy.
Mirrors the Google Workspace auth pattern for consistency.

Note: Spotify OAuth callback uses port 8888 (http://127.0.0.1:8888/callback).
Ensure this port is available and matches the redirect URI in credentials/spotify_credentials.json.
"""

from __future__ import annotations

import logging
import json
import os
import sys
import time
from contextlib import contextmanager
from functools import wraps
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Default configuration
DEFAULT_USER_EMAIL = "jck411@gmail.com"

# Path settings
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
CREDENTIALS_PATH = PROJECT_ROOT / "credentials"
TOKEN_PATH = PROJECT_ROOT / "data" / "tokens"

# Create token directory if it doesn't exist
os.makedirs(TOKEN_PATH, exist_ok=True)

# ALL Spotify Web API user scopes for complete access
# Documentation: https://developer.spotify.com/documentation/web-api/concepts/scopes
# Note: Spotify Open Access scopes (user-soa-*) require special allowlisting and
# will return "Illegal scope" for standard apps, so they are intentionally omitted.
# SOA service scopes (soa-manage-*, soa-create-*) also cannot be mixed with user
# scopes and are for Client Credentials Flow only.
SCOPES = [
    # Images
    "ugc-image-upload",  # Upload images to user-generated content
    # Spotify Connect / Playback
    "user-read-playback-state",  # Read current playback state
    "user-modify-playback-state",  # Control playback (play, pause, skip, volume)
    "user-read-currently-playing",  # Read currently playing track
    "app-remote-control",  # Remote control playback (iOS/Android SDKs)
    "streaming",  # Play content via Web Playback SDK
    # Playlists
    "playlist-read-private",  # Read private playlists
    "playlist-read-collaborative",  # Read collaborative playlists
    "playlist-modify-public",  # Create/modify public playlists
    "playlist-modify-private",  # Create/modify private playlists
    # Follow
    "user-follow-modify",  # Manage following of artists and users
    "user-follow-read",  # Read following of artists and users
    # Listening History
    "user-read-playback-position",  # Read playback position in podcasts/episodes
    "user-top-read",  # Read top artists and tracks
    "user-read-recently-played",  # Read recently played tracks
    # Library
    "user-library-modify",  # Manage saved tracks and albums
    "user-library-read",  # Read saved tracks and albums
    # User Profile
    "user-read-email",  # Read user email
    "user-read-private",  # Read user profile (country, product subscription)
]

T = TypeVar("T")

SPOTIPY_LOGGER_NAMES = ("spotipy", "spotipy.client", "spotipy.oauth2")


def _silence_spotipy_logging() -> None:
    """Prevent spotipy logs from polluting MCP stdout."""
    for logger_name in SPOTIPY_LOGGER_NAMES:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())


_silence_spotipy_logging()


@contextmanager
def suppress_stdout_stderr():
    """Context manager to suppress stdout/stderr output.

    Necessary for MCP servers to prevent spotipy from polluting the JSON-RPC
    protocol with OAuth prompts and other non-JSON output.
    """
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        yield
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def retry_on_rate_limit(
    max_retries: int = 3,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator to retry Spotify API calls on rate limit (429) errors.

    Implements exponential backoff: 1s, 2s, 4s between retries.
    Works with both sync and async functions.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)

    Returns:
        Decorated function with retry logic
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        # Check if the function is async
        import asyncio
        import inspect

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                for attempt in range(max_retries):
                    try:
                        return await func(*args, **kwargs)
                    except spotipy.exceptions.SpotifyException as e:
                        # Check for rate limit (429 Too Many Requests)
                        if e.http_status == 429 and attempt < max_retries - 1:
                            wait_time = 2**attempt  # 1s, 2s, 4s
                            await asyncio.sleep(wait_time)
                            continue
                        raise
                return await func(*args, **kwargs)  # Final attempt

            return async_wrapper  # type: ignore
        else:

            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> T:
                for attempt in range(max_retries):
                    try:
                        return func(*args, **kwargs)
                    except spotipy.exceptions.SpotifyException as e:
                        # Check for rate limit (429 Too Many Requests)
                        if e.http_status == 429 and attempt < max_retries - 1:
                            wait_time = 2**attempt  # 1s, 2s, 4s
                            time.sleep(wait_time)
                            continue
                        raise
                return func(*args, **kwargs)  # Final attempt

            return sync_wrapper  # type: ignore

    return decorator


def get_spotify_config() -> dict[str, Any]:
    """Load Spotify OAuth configuration from credentials file.

    Returns:
        Dict containing client_id, client_secret, and redirect_uri

    Raises:
        FileNotFoundError: If spotify_credentials.json doesn't exist
    """
    config_path = CREDENTIALS_PATH / "spotify_credentials.json"

    if not config_path.exists():
        raise FileNotFoundError(
            f"Spotify credentials not found at {config_path}. "
            "Please create this file with client_id, client_secret, and redirect_uri. "
            "See docs for setup instructions."
        )

    with open(config_path) as f:
        return json.load(f)


def get_token_path(user_email: str) -> Path:
    """Get the path where user's Spotify token should be stored.

    Args:
        user_email: User's email address, used to identify their token file

    Returns:
        Path to the user's token file
    """
    # Create sanitized filename from email (e.g., jck411_at_gmail_com_spotify.json)
    filename = user_email.replace("@", "_at_").replace(".", "_") + "_spotify.json"
    return TOKEN_PATH / filename


def get_credentials(user_email: str) -> Optional[dict[str, Any]]:
    """Get stored Spotify credentials for a user, refreshing if necessary.

    Spotipy handles token refresh automatically, but we check if the token
    file exists and is valid.

    Args:
        user_email: User's email address

    Returns:
        Token data dict if valid credentials exist, None otherwise
    """
    token_path = get_token_path(user_email)

    if not token_path.exists():
        return None

    try:
        with open(token_path) as token_file:
            token_data = json.load(token_file)

        # Basic validation - spotipy will handle refresh if expired
        if "access_token" not in token_data:
            return None

        return token_data
    except (json.JSONDecodeError, OSError):
        return None


def store_credentials(user_email: str, token_info: dict[str, Any]) -> None:
    """Store Spotify token data to file.

    Args:
        user_email: User's email address
        token_info: Token info dict from spotipy (contains access_token, refresh_token, etc.)
    """
    token_path = get_token_path(user_email)

    with open(token_path, "w") as token_file:
        json.dump(token_info, token_file, indent=2)


def get_spotify_client(user_email: str) -> spotipy.Spotify:
    """Get authenticated Spotify client for a user.

    Creates a Spotify client with OAuth authentication. The client will
    automatically refresh tokens when they expire.

    Args:
        user_email: User's email address

    Returns:
        Authenticated Spotify client

    Raises:
        ValueError: If no valid credentials are found for the user
    """
    credentials = get_credentials(user_email)

    if not credentials:
        raise ValueError(
            f"No valid Spotify credentials found for {user_email}. "
            "Click 'Connect Spotify' in Settings to authorize access."
        )

    try:
        config = get_spotify_config()

        # Suppress spotipy's stdout/stderr to prevent MCP protocol pollution
        with suppress_stdout_stderr():
            auth_manager = SpotifyOAuth(
                client_id=config["client_id"],
                client_secret=config["client_secret"],
                redirect_uri=config["redirect_uri"],
                scope=" ".join(SCOPES),
                cache_path=str(get_token_path(user_email)),
                open_browser=False,  # Don't auto-open browser for server context
                show_dialog=False,  # Don't show authorization dialog prompts
            )

            token_info = auth_manager.validate_token(
                auth_manager.cache_handler.get_cached_token()
            )

        if not token_info:
            raise ValueError(
                "Stored Spotify credentials are missing required scopes or expired. "
                "Click 'Connect Spotify' in Settings to authorize this account again."
            )

        # Spotipy handles token refresh automatically once we know the cache is valid
        return spotipy.Spotify(auth_manager=auth_manager)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to create Spotify client for {user_email}: {e}")


def get_auth_url(user_email: str) -> str:
    """Generate Spotify OAuth authorization URL for a user.

    Args:
        user_email: User's email address (stored in state for callback verification)

    Returns:
        Authorization URL to redirect user to
    """
    config = get_spotify_config()

    # Suppress spotipy's stdout/stderr to prevent MCP protocol pollution
    with suppress_stdout_stderr():
        auth_manager = SpotifyOAuth(
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            redirect_uri=config["redirect_uri"],
            scope=" ".join(SCOPES),
            cache_path=str(get_token_path(user_email)),
            open_browser=False,
            show_dialog=True,  # Force re-consent so new scopes are acknowledged
            state=user_email,  # Pass user_email in state for verification
        )

        return auth_manager.get_authorize_url()


def process_auth_callback(code: str, user_email: str) -> dict[str, Any]:
    """Process OAuth callback and exchange code for tokens.

    Args:
        code: Authorization code from Spotify callback
        user_email: User's email address

    Returns:
        Token info dict containing access_token, refresh_token, etc.

    Raises:
        Exception: If token exchange fails
    """
    config = get_spotify_config()

    # Suppress spotipy's stdout/stderr to prevent MCP protocol pollution
    with suppress_stdout_stderr():
        auth_manager = SpotifyOAuth(
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            redirect_uri=config["redirect_uri"],
            scope=" ".join(SCOPES),
            cache_path=str(get_token_path(user_email)),
            open_browser=False,
            show_dialog=True,  # Force re-consent so new scopes are acknowledged
        )

        # Exchange code for token
        token_info = auth_manager.get_access_token(code, as_dict=True)

    if not token_info:
        raise ValueError("Failed to exchange authorization code for tokens")

    # Store credentials
    store_credentials(user_email, token_info)

    return token_info
