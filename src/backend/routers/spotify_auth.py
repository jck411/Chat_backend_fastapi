"""Router for Spotify OAuth operations."""

from __future__ import annotations

import json
from html import escape
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.config import get_settings
from backend.services.spotify_auth.auth import (
    DEFAULT_USER_EMAIL,
    get_auth_url,
    get_credentials,
    process_auth_callback,
)

router = APIRouter()


class SpotifyAuthStatusResponse(BaseModel):
    """Authorization status for Spotify."""

    authorized: bool
    user_email: str


class SpotifyAuthAuthorizeRequest(BaseModel):
    """Request payload to start the OAuth flow."""

    user_email: Optional[str] = None


class SpotifyAuthAuthorizeResponse(BaseModel):
    """Response payload containing the consent screen URL."""

    auth_url: str
    user_email: str


def _resolve_frontend_origin() -> str:
    settings = get_settings()
    parsed = urlparse(str(settings.frontend_url))
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "*"


def _render_callback_page(
    *,
    success: bool,
    user_email: str,
    message: str,
) -> HTMLResponse:
    status = "success" if success else "error"
    target_origin = _resolve_frontend_origin()
    payload = {
        "source": "spotify-auth",
        "status": status,
        "userEmail": user_email,
        "message": message,
    }
    payload_json = json.dumps(payload)
    title = (
        "Spotify authorization complete" if success else "Spotify authorization failed"
    )
    safe_message = escape(message)
    safe_email = escape(user_email)

    html = f"""
    <!DOCTYPE html>
    <html lang=\"en\">
      <head>
        <meta charset=\"utf-8\" />
        <title>{escape(title)}</title>
        <style>
          :root {{
            color-scheme: only light;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
            background: #0f172a;
            color: #f8fafc;
          }}
          body {{
            margin: 0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: radial-gradient(circle at top, rgba(30, 215, 96, 0.25), transparent 65%),
              #0f172a;
          }}
          .card {{
            background: rgba(15, 23, 42, 0.92);
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 16px;
            padding: 32px 36px;
            max-width: 420px;
            box-shadow: 0 18px 36px rgba(15, 23, 42, 0.35);
            text-align: center;
          }}
          .card h1 {{
            margin: 0 0 12px;
            font-size: 1.35rem;
          }}
          .card p {{
            margin: 0 0 16px;
            line-height: 1.4;
            color: rgba(226, 232, 240, 0.9);
          }}
          .card code {{
            font-family: inherit;
            font-weight: 600;
            background: rgba(30, 215, 96, 0.15);
            color: #1ed760;
            padding: 0.2rem 0.45rem;
            border-radius: 999px;
          }}
          .card button {{
            background: linear-gradient(135deg, rgba(30, 215, 96, 0.85), rgba(25, 185, 82, 0.85));
            border: none;
            border-radius: 999px;
            color: #0b1220;
            padding: 0.55rem 1.4rem;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            box-shadow: 0 10px 28px rgba(30, 215, 96, 0.35);
          }}
          .card button:hover {{
            transform: translateY(-1px);
          }}
          .status-icon {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 48px;
            height: 48px;
            border-radius: 50%;
            margin-bottom: 18px;
            font-size: 1.4rem;
          }}
          .status-success {{
            background: rgba(30, 215, 96, 0.2);
            color: #1ed760;
          }}
          .status-error {{
            background: rgba(248, 113, 113, 0.18);
            color: #f87171;
          }}
        </style>
      </head>
      <body>
        <div class=\"card\">
          <div class=\"status-icon {"status-success" if success else "status-error"}\">{"✓" if success else "!"}</div>
          <h1>{escape(title)}</h1>
          <p>{safe_message}</p>
          <p><code>{safe_email}</code></p>
          <button type=\"button\" onclick=\"window.close()\">Close window</button>
        </div>
        <script>
          (function() {{
            const payload = {payload_json};
            const targetOrigin = {json.dumps(target_origin)};
            try {{
              if (window.opener && !window.opener.closed) {{
                window.opener.postMessage(payload, targetOrigin);
              }}
            }} catch (err) {{
              console.warn('Failed to notify parent window about Spotify auth result.', err);
            }}
          }})();
        </script>
      </body>
    </html>
    """

    status_code = 200 if success else 400
    return HTMLResponse(content=html, status_code=status_code)


@router.get("/status", response_model=SpotifyAuthStatusResponse)
async def check_auth_status(
    user_email: str = Query(
        DEFAULT_USER_EMAIL, description="Email for stored credentials"
    ),
) -> SpotifyAuthStatusResponse:
    """Return credential status for the requested user."""

    try:
        credentials = get_credentials(user_email)
    except FileNotFoundError:
        credentials = None
    except Exception as exc:  # pragma: no cover - unexpected failure
        raise HTTPException(
            status_code=500, detail=f"Failed to load credentials: {exc}"
        )

    return SpotifyAuthStatusResponse(
        authorized=credentials is not None,
        user_email=user_email,
    )


@router.post("/authorize", response_model=SpotifyAuthAuthorizeResponse)
async def start_authorization(
    request: SpotifyAuthAuthorizeRequest,
) -> SpotifyAuthAuthorizeResponse:
    """Create an authorization URL for the Spotify OAuth consent flow."""

    user_email = request.user_email or DEFAULT_USER_EMAIL

    try:
        auth_url = get_auth_url(user_email)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # pragma: no cover - unexpected failure
        raise HTTPException(
            status_code=500, detail=f"Failed to create authorization URL: {exc}"
        )

    return SpotifyAuthAuthorizeResponse(auth_url=auth_url, user_email=user_email)


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str = Query(..., description="Authorization code from Spotify"),
    state: Optional[str] = Query(
        None, description="Opaque state containing the user email"
    ),
    error: Optional[str] = Query(None, description="Error returned by Spotify"),
) -> HTMLResponse:
    """Handle the OAuth redirect back from Spotify.

    Note: This endpoint receives the callback on port 8888 from Spotify's OAuth flow.
    """

    user_email = state or DEFAULT_USER_EMAIL

    if error:
        return _render_callback_page(
            success=False,
            user_email=user_email,
            message=f"Authorization error: {error}",
        )

    try:
        process_auth_callback(code, user_email)
    except Exception as exc:  # pragma: no cover - unexpected failure
        return _render_callback_page(
            success=False,
            user_email=user_email,
            message=f"Failed to complete authorization: {exc}",
        )

    return _render_callback_page(
        success=True,
        user_email=user_email,
        message="Spotify is now connected. You can close this window.",
    )
