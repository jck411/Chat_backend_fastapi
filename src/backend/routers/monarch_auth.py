from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from monarchmoney import LoginFailedException, MonarchMoney, RequireMFAException
from pydantic import BaseModel

from backend.services.monarch_auth import (
    MonarchCredentials,
    delete_monarch_credentials,
    get_monarch_credentials,
    save_monarch_credentials,
)

router = APIRouter()

# Define session file path to match monarch_server.py
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOKEN_DIR = PROJECT_ROOT / "data" / "tokens"
SESSION_FILE = TOKEN_DIR / "monarch_session.pickle"


class MonarchStatusResponse(BaseModel):
    configured: bool
    email: str | None


@router.get("/status", response_model=MonarchStatusResponse)
async def get_status() -> MonarchStatusResponse:
    """Check if Monarch Money credentials are configured."""
    creds = get_monarch_credentials()
    return MonarchStatusResponse(
        configured=creds is not None,
        email=creds.email if creds else None,
    )


@router.post("/credentials", response_model=MonarchStatusResponse)
async def save_credentials(creds: MonarchCredentials) -> MonarchStatusResponse:
    """Save Monarch Money credentials."""
    try:
        # Verify credentials before saving
        mm = MonarchMoney(session_file=str(SESSION_FILE))

        # Ensure token directory exists
        TOKEN_DIR.mkdir(parents=True, exist_ok=True)

        # Clean MFA secret if present
        mfa_secret = (
            creds.mfa_secret.strip().replace(" ", "") if creds.mfa_secret else None
        )

        await mm.login(
            email=creds.email,
            password=creds.password,
            mfa_secret_key=mfa_secret,
            save_session=True,
        )

        # Save cleaned credentials
        if mfa_secret:
            creds.mfa_secret = mfa_secret

        save_monarch_credentials(creds)
        return MonarchStatusResponse(configured=True, email=creds.email)
    except LoginFailedException as e:
        error_msg = str(e)
        print(f"Monarch Login Failed: {error_msg}")
        if "429" in error_msg:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Monarch has temporarily blocked access. Please wait 30 minutes before trying again.",
            )
        raise HTTPException(status_code=401, detail=f"Login failed: {error_msg}")
    except RequireMFAException as e:
        print(f"Monarch MFA Required: {str(e)}")
        # If MFA is required but user didn't provide a secret, we can't proceed automatically.
        # However, if they just disabled it, maybe the session is stale or the API is confused.
        # But fundamentally, if the API raises RequireMFAException, it means the account DOES have MFA enabled.
        raise HTTPException(
            status_code=400,
            detail="Monarch says MFA is still enabled for this account. Please enter your MFA Secret, or double-check that you disabled it in Monarch Settings.",
        )
    except Exception as e:
        print(f"Monarch Unexpected Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")


@router.delete("/credentials", response_model=MonarchStatusResponse)
async def delete_credentials() -> MonarchStatusResponse:
    """Delete Monarch Money credentials."""
    try:
        delete_monarch_credentials()
        return MonarchStatusResponse(configured=False, email=None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
