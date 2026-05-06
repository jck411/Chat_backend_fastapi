"""Test script to inspect Monarch cashflow response structure."""

import asyncio
import json
from pathlib import Path

from monarchmoney import MonarchMoney

from backend.services.monarch_auth import get_monarch_credentials


async def main():
    """Test cashflow API response."""
    creds = get_monarch_credentials()
    if not creds:
        print("No credentials found")
        return

    session_file = Path("data/tokens/monarch_session.pickle")
    mm = MonarchMoney(session_file=str(session_file))

    # Try to load existing session
    if session_file.exists():
        try:
            mm.load_session(str(session_file))
        except Exception:
            pass

    # Check if logged in
    try:
        await mm.get_subscription_details()
        print("✓ Already logged in")
    except Exception:
        print("Logging in...")
        mfa_secret = (
            creds.mfa_secret.strip().replace(" ", "") if creds.mfa_secret else None
        )
        await mm.login(
            email=creds.email,
            password=creds.password,
            mfa_secret_key=mfa_secret,
            use_saved_session=False,
        )
        mm.save_session(str(session_file))
        print("✓ Logged in")

    # Get cashflow data
    print("\n" + "=" * 80)
    print("Testing get_cashflow()...")
    print("=" * 80)

    data = await mm.get_cashflow(start_date="2025-11-01", end_date="2025-11-19")

    print("\nFull response structure:")
    print(json.dumps(data, indent=2, default=str))

    print("\n" + "=" * 80)
    print("Summary analysis:")
    print("=" * 80)

    print(f"\nTop-level keys: {list(data.keys())}")

    if "summary" in data:
        summary = data["summary"]
        print(f"\nSummary type: {type(summary)}")
        print(f"Summary length: {len(summary) if isinstance(summary, list) else 'N/A'}")

        if isinstance(summary, list) and len(summary) > 0:
            print(f"\nFirst summary item keys: {list(summary[0].keys())}")

            if "categoryGroups" in summary[0]:
                groups = summary[0]["categoryGroups"]
                print(f"\nCategory groups count: {len(groups)}")

                if len(groups) > 0:
                    print(f"\nFirst group keys: {list(groups[0].keys())}")
                    print("\nFirst group sample:")
                    print(json.dumps(groups[0], indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
