from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

# Resolve project root relative to this file: src/backend/services/monarch_auth.py
# parents[0] = services
# parents[1] = backend
# parents[2] = src
# parents[3] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CREDENTIALS_PATH = PROJECT_ROOT / "credentials"
MONARCH_CREDENTIALS_FILE = CREDENTIALS_PATH / "monarch_credentials.json"


class MonarchCredentials(BaseModel):
    email: str
    password: str
    mfa_secret: Optional[str] = None


def get_monarch_credentials() -> Optional[MonarchCredentials]:
    """Load Monarch credentials from disk if they exist."""
    if not MONARCH_CREDENTIALS_FILE.exists():
        return None

    try:
        with open(MONARCH_CREDENTIALS_FILE, "r") as f:
            data = json.load(f)
            return MonarchCredentials(**data)
    except Exception:
        return None


def save_monarch_credentials(creds: MonarchCredentials) -> None:
    """Save Monarch credentials to disk."""
    CREDENTIALS_PATH.mkdir(exist_ok=True)
    with open(MONARCH_CREDENTIALS_FILE, "w") as f:
        f.write(creds.model_dump_json(indent=2))


def delete_monarch_credentials() -> None:
    """Remove Monarch credentials from disk."""
    if MONARCH_CREDENTIALS_FILE.exists():
        MONARCH_CREDENTIALS_FILE.unlink()
