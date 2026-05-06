"""Google Authentication service for Calendar API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import google.oauth2.credentials
from google.auth.transport.requests import Request

# Using google.oauth2.credentials.Credentials via the Any type to avoid type errors
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Default configuration
DEFAULT_USER_EMAIL = "jck411@gmail.com"

# Path settings
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
CREDENTIALS_PATH = PROJECT_ROOT / "credentials"
TOKEN_PATH = PROJECT_ROOT / "data" / "tokens"

# Create token directory if it doesn't exist
os.makedirs(TOKEN_PATH, exist_ok=True)

# Define scopes
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
GMAIL_LABELS_SCOPE = "https://www.googleapis.com/auth/gmail.labels"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
SCOPES = [
    CALENDAR_SCOPE,
    TASKS_SCOPE,
    GMAIL_READONLY_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_LABELS_SCOPE,
    DRIVE_SCOPE,
]


def _extract_token_scopes(token_data: Dict[str, Any]) -> set[str]:
    """Extract OAuth scopes from a stored token payload."""

    scopes_field = token_data.get("scopes")
    if isinstance(scopes_field, list):
        return set(scopes_field)

    scope_field = token_data.get("scope")
    if isinstance(scope_field, str):
        return set(scope_field.split())

    return set()


def get_client_config() -> Dict[str, Any]:
    """
    Load the client configuration for OAuth.

    Returns:
        Dict containing the OAuth client configuration.
    """
    # Find the first client_secret file in the credentials directory
    client_secrets = list(CREDENTIALS_PATH.glob("client_secret_*.json"))

    if not client_secrets:
        raise FileNotFoundError(
            "No client_secret file found in credentials directory."
            " Please download it from Google Cloud Console."
        )

    # Use the first file found
    client_config_path = client_secrets[0]

    with open(client_config_path, "r") as f:
        return json.load(f)


def get_token_path(user_email: str) -> Path:
    """
    Get the path where user token should be stored.

    Args:
        user_email: User's email address, used to identify their token file.

    Returns:
        Path to the user's token file.
    """
    # Create sanitized filename from email
    filename = user_email.replace("@", "_at_").replace(".", "_") + ".json"
    return TOKEN_PATH / filename


def get_credentials(user_email: str) -> Optional[Any]:
    """
    Get the stored credentials for a user, refreshing if necessary.

    Args:
        user_email: User's email address.

    Returns:
        Credentials object if valid credentials exist, None otherwise.
    """
    token_path = get_token_path(user_email)

    if not token_path.exists():
        return None

    with open(token_path, "r") as token_file:
        token_data = json.load(token_file)

    required_scopes = set(SCOPES)
    current_scopes = _extract_token_scopes(token_data)

    if not required_scopes.issubset(current_scopes):
        # Existing token is missing scopes (likely created before Tasks support).
        # Remove the token so the caller initiates a fresh consent flow.
        try:
            token_path.unlink()
        except OSError:
            pass
        return None

    creds = google.oauth2.credentials.Credentials.from_authorized_user_info(
        token_data, SCOPES
    )

    # If credentials are expired but have refresh token, refresh them
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        store_credentials(user_email, creds)

    return creds if creds and not creds.expired else None


def store_credentials(user_email: str, credentials: Any) -> None:
    """
    Store user credentials to a file.

    Args:
        user_email: User's email address.
        credentials: Credentials object to store.
    """
    token_path = get_token_path(user_email)

    token_data = credentials.to_json()
    with open(token_path, "w") as token_file:
        token_file.write(token_data)


def get_auth_url(user_email: str, redirect_uri: str) -> str:
    """
    Generate authorization URL for the user.

    Args:
        user_email: User's email address.
        redirect_uri: URI to redirect to after authorization.

    Returns:
        Authorization URL string.
    """
    client_config = get_client_config()

    # Create OAuth flow
    flow = Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=redirect_uri
    )

    # Create state parameter with user email for verification
    # In a real app, you should use a proper state token mechanism
    state = user_email

    # Generate authorization URL
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        state=state,
        prompt="consent",  # Force re-consent to get refresh token
    )

    return auth_url


def process_auth_callback(code: str, user_email: str, redirect_uri: str) -> Any:
    """
    Process OAuth callback and get credentials.

    Args:
        code: Authorization code from callback.
        user_email: User's email address.
        redirect_uri: Redirect URI used in the initial request.

    Returns:
        Credentials object.
    """
    client_config = get_client_config()

    # Create flow object
    flow = Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=redirect_uri
    )

    # Exchange code for credentials
    flow.fetch_token(code=code)
    credentials = flow.credentials

    # Store the credentials
    store_credentials(user_email, credentials)

    return credentials


def authorize_user(user_email: str, redirect_uri: str) -> str:
    """
    Generate an authorization URL for a user.

    Args:
        user_email: User's email address.
        redirect_uri: URI to redirect to after authorization.

    Returns:
        Authorization URL to redirect the user to.
    """
    return get_auth_url(user_email, redirect_uri)


def get_calendar_service(user_email: str) -> Any:
    """
    Get an authenticated Google Calendar API service for a user.

    Args:
        user_email: User's email address.

    Returns:
        Google Calendar service object.

    Raises:
        ValueError: If no valid credentials are found for the user.
    """
    credentials = get_credentials(user_email)

    if not credentials:
        raise ValueError(
            f"No valid credentials found for {user_email}. "
            "Click 'Connect Google Services' in Settings to authorize access."
        )

    return build("calendar", "v3", credentials=credentials)


def get_tasks_service(user_email: str) -> Any:
    """
    Get an authenticated Google Tasks API service for a user.

    Args:
        user_email: User's email address.

    Returns:
        Google Tasks service object.

    Raises:
        ValueError: If no valid credentials are found for the user.
    """
    credentials = get_credentials(user_email)

    if not credentials:
        raise ValueError(
            f"No valid credentials found for {user_email}. "
            "Click 'Connect Google Services' in Settings to authorize access."
        )

    return build("tasks", "v1", credentials=credentials)


def get_gmail_service(user_email: str) -> Any:
    """
    Get an authenticated Gmail API service for a user.

    Args:
        user_email: User's email address.

    Returns:
        Google Gmail service object.

    Raises:
        ValueError: If no valid credentials are found for the user.
    """
    credentials = get_credentials(user_email)

    if not credentials:
        raise ValueError(
            f"No valid credentials found for {user_email}. "
            "Click 'Connect Google Services' in Settings to authorize access."
        )

    return build("gmail", "v1", credentials=credentials)


def get_drive_service(user_email: str) -> Any:
    """
    Get an authenticated Google Drive API service for a user.

    Args:
        user_email: User's email address.

    Returns:
        Google Drive service object.

    Raises:
        ValueError: If no valid credentials are found for the user.
    """
    credentials = get_credentials(user_email)

    if not credentials:
        raise ValueError(
            f"No valid credentials found for {user_email}. "
            "Click 'Connect Google Services' in Settings to authorize access."
        )

    return build("drive", "v3", credentials=credentials)
