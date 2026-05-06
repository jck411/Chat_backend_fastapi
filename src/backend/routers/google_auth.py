"""Router for Google OAuth operations."""

from __future__ import annotations

import json
from html import escape
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.config import get_settings
from backend.services.google_auth.auth import (
    DEFAULT_USER_EMAIL,
    authorize_user,
    get_credentials,
    process_auth_callback,
)

router = APIRouter()

_GOOGLE_SERVICES = [
    "Google Calendar",
    "Google Tasks",
    "Gmail",
    "Google Drive",
]


class GoogleAuthStatusResponse(BaseModel):
    """Authorization status for Google services."""

    authorized: bool
    user_email: str
    expires_at: Optional[str] = None
    services: list[str]


class GoogleAuthAuthorizeRequest(BaseModel):
    """Request payload to start the OAuth flow."""

    user_email: Optional[str] = None
    redirect_uri: Optional[str] = None


class GoogleAuthAuthorizeResponse(BaseModel):
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
        "source": "google-auth",
        "status": status,
        "userEmail": user_email,
        "message": message,
    }
    payload_json = json.dumps(payload)
    title = "Google authorization complete" if success else "Google authorization failed"
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
            background: radial-gradient(circle at top, rgba(56, 189, 248, 0.25), transparent 65%),
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
            background: rgba(15, 118, 110, 0.25);
            color: #5eead4;
            padding: 0.2rem 0.45rem;
            border-radius: 999px;
          }}
          .card button {{
            background: linear-gradient(135deg, rgba(56, 189, 248, 0.85), rgba(59, 130, 246, 0.85));
            border: none;
            border-radius: 999px;
            color: #0b1220;
            padding: 0.55rem 1.4rem;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            box-shadow: 0 10px 28px rgba(59, 130, 246, 0.35);
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
            background: rgba(56, 189, 248, 0.2);
            color: #38bdf8;
          }}
          .status-error {{
            background: rgba(248, 113, 113, 0.18);
            color: #f87171;
          }}
        </style>
      </head>
      <body>
        <div class=\"card\">
          <div class=\"status-icon {'status-success' if success else 'status-error'}\">{ '✓' if success else '!' }</div>
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
              console.warn('Failed to notify parent window about Google auth result.', err);
            }}
          }})();
        </script>
      </body>
    </html>
    """

    status_code = 200 if success else 400
    return HTMLResponse(content=html, status_code=status_code)


@router.get("/status", response_model=GoogleAuthStatusResponse)
async def check_auth_status(
    user_email: str = Query(DEFAULT_USER_EMAIL, description="Email for stored credentials"),
) -> GoogleAuthStatusResponse:
    """Return credential status for the requested user."""

    try:
        credentials = get_credentials(user_email)
    except FileNotFoundError:
        credentials = None
    except Exception as exc:  # pragma: no cover - unexpected failure
        raise HTTPException(status_code=500, detail=f"Failed to load credentials: {exc}")

    if not credentials:
        return GoogleAuthStatusResponse(
            authorized=False,
            user_email=user_email,
            expires_at=None,
            services=list(_GOOGLE_SERVICES),
        )

    expiry = getattr(credentials, "expiry", None)
    return GoogleAuthStatusResponse(
        authorized=True,
        user_email=user_email,
        expires_at=expiry.isoformat() if expiry else None,
        services=list(_GOOGLE_SERVICES),
    )


@router.post("/authorize", response_model=GoogleAuthAuthorizeResponse)
async def start_authorization(request: GoogleAuthAuthorizeRequest) -> GoogleAuthAuthorizeResponse:
    """Create an authorization URL for the Google OAuth consent flow."""

    settings = get_settings()

    user_email = request.user_email or DEFAULT_USER_EMAIL
    redirect_uri = request.redirect_uri or settings.google_oauth_redirect_uri

    try:
        auth_url = authorize_user(user_email, redirect_uri)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # pragma: no cover - unexpected failure
        raise HTTPException(
            status_code=500, detail=f"Failed to create authorization URL: {exc}"
        )

    return GoogleAuthAuthorizeResponse(auth_url=auth_url, user_email=user_email)


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: Optional[str] = Query(None, description="Opaque state containing the user email"),
    error: Optional[str] = Query(None, description="Error returned by Google"),
) -> HTMLResponse:
    """Handle the OAuth redirect back from Google."""

    user_email = state or DEFAULT_USER_EMAIL

    if error:
        return _render_callback_page(
            success=False,
            user_email=user_email,
            message=f"Authorization error: {error}",
        )

    settings = get_settings()

    try:
        process_auth_callback(code, user_email, settings.google_oauth_redirect_uri)
    except Exception as exc:  # pragma: no cover - unexpected failure
        return _render_callback_page(
            success=False,
            user_email=user_email,
            message=f"Failed to complete authorization: {exc}",
        )

    return _render_callback_page(
        success=True,
        user_email=user_email,
        message="Google services are now connected. You can close this window.",
    )
