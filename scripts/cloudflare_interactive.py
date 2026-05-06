#!/usr/bin/env python3
"""
Interactive Cloudflare Management
Run this to manage your sites easily from VS Code
"""

import os
import sys
from pathlib import Path

from cloudflare_manager import CloudflareManager
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


def print_menu():
    print("\n" + "=" * 60)
    print("🌐 CLOUDFLARE WEBSITE MANAGER")
    print("=" * 60)
    print("\n1. List all websites")
    print("2. View DNS records")
    print("3. View website settings")
    print("4. Purge cache")
    print("5. Toggle development mode")
    print("6. View SSL status")
    print("7. Security settings")
    print("0. Exit")
    print("-" * 60)


def list_websites(cf):
    zones = cf.list_zones()
    if not zones:
        print("\n❌ No websites found")
        return None

    print("\n📋 Your Websites:")
    for i, zone in enumerate(zones, 1):
        status_icon = "✅" if zone["status"] == "active" else "⚠️"
        print(f"{i}. {status_icon} {zone['name']}")
        print(f"   Status: {zone['status']}, ID: {zone['id']}")

    return zones


def view_dns_records(cf, zone_id, zone_name):
    print(f"\n🌐 DNS Records for {zone_name}:")
    print("-" * 60)
    records = cf.list_dns_records(zone_id)

    for record in records:
        proxied = "🟠" if record.get("proxied") else "⚪"
        print(f"{proxied} {record['type']:6} {record['name']}")
        print(f"   → {record['content']}")
        print()


def view_settings(cf, zone_id, zone_name):
    print(f"\n⚙️  Settings for {zone_name}:")
    print("-" * 60)

    settings = cf.get_zone_settings(zone_id)

    # Common settings to display
    common = [
        "ssl",
        "development_mode",
        "security_level",
        "always_use_https",
        "min_tls_version",
        "automatic_https_rewrites",
    ]

    for setting in settings:
        if setting["id"] in common:
            value = setting["value"]
            modified = setting.get("modified_on", "N/A")
            print(f"  {setting['id']:30} = {value}")


def purge_cache(cf, zone_id, zone_name):
    confirm = input(f"\n⚠️  Purge ALL cache for {zone_name}? (yes/no): ")
    if confirm.lower() == "yes":
        result = cf.purge_cache(zone_id)
        print("✅ Cache purged successfully!")
    else:
        print("❌ Cancelled")


def toggle_dev_mode(cf, zone_id, zone_name):
    settings = cf.get_zone_settings(zone_id)
    dev_mode = None
    for s in settings:
        if s["id"] == "development_mode":
            dev_mode = s["value"]
            break

    if dev_mode == "on":
        print(f"\n🔴 Development mode is ON for {zone_name}")
        if input("Disable it? (yes/no): ").lower() == "yes":
            cf.disable_dev_mode(zone_id)
            print("✅ Development mode disabled")
    else:
        print(f"\n🟢 Development mode is OFF for {zone_name}")
        if input("Enable it? (yes/no): ").lower() == "yes":
            cf.enable_dev_mode(zone_id)
            print("✅ Development mode enabled (auto-disables in 3 hours)")


def view_ssl_status(cf, zone_id, zone_name):
    print(f"\n🔒 SSL/TLS Status for {zone_name}:")
    print("-" * 60)

    ssl_mode = cf.get_ssl_setting(zone_id)
    modes = {
        "off": "❌ Off - No encryption",
        "flexible": "⚠️  Flexible - Browser to Cloudflare only",
        "full": "🟡 Full - End-to-end, self-signed OK",
        "strict": "✅ Full (Strict) - End-to-end, valid cert required",
    }

    print(f"Current mode: {modes.get(ssl_mode, ssl_mode)}")
    print("\nAvailable modes:")
    print("  1. off      - No HTTPS")
    print("  2. flexible - Browser → CF encrypted only")
    print("  3. full     - End-to-end (self-signed cert OK)")
    print("  4. strict   - End-to-end (valid cert required)")

    change = input("\nChange SSL mode? (1-4 or Enter to skip): ")
    mode_map = {"1": "off", "2": "flexible", "3": "full", "4": "strict"}

    if change in mode_map:
        cf.set_ssl_mode(zone_id, mode_map[change])
        print(f"✅ SSL mode changed to: {mode_map[change]}")


def security_settings(cf, zone_id, zone_name):
    print(f"\n🛡️  Security Settings for {zone_name}:")
    print("-" * 60)

    print("\nSecurity Levels:")
    print("  1. essentially_off - No challenges")
    print("  2. low            - Only dangerous visitors")
    print("  3. medium         - Standard (recommended)")
    print("  4. high           - Challenge suspicious visitors")
    print("  5. under_attack   - Maximum protection")

    level = input("\nSet security level (1-5 or Enter to skip): ")
    level_map = {
        "1": "essentially_off",
        "2": "low",
        "3": "medium",
        "4": "high",
        "5": "under_attack",
    }

    if level in level_map:
        cf.set_security_level(zone_id, level_map[level])
        print(f"✅ Security level set to: {level_map[level]}")

    https = input("\nAlways use HTTPS? (yes/no/skip): ").lower()
    if https == "yes":
        cf.enable_https_redirect(zone_id)
        print("✅ HTTPS redirect enabled")
    elif https == "no":
        cf.disable_https_redirect(zone_id)
        print("✅ HTTPS redirect disabled")


def main():
    # Check for authentication credentials
    has_token = bool(os.getenv("CLOUDFLARE_API_TOKEN"))
    has_global_key = bool(
        os.getenv("CLOUDFLARE_EMAIL") and os.getenv("CLOUDFLARE_API_KEY")
    )

    if not (has_token or has_global_key):
        print("\n❌ Error: Cloudflare authentication not configured")
        print("\n📋 Option 1: API Token (Recommended)")
        print("1. Go to: https://dash.cloudflare.com/profile/api-tokens")
        print("2. Click 'Create Token'")
        print("3. Use 'Read all resources' template or create custom token")
        print("4. Add to .env file: CLOUDFLARE_API_TOKEN='your_token_here'")
        print("\n📋 Option 2: Global API Key")
        print("1. Go to: https://dash.cloudflare.com/profile/api-tokens")
        print("2. Scroll to 'API Keys', click 'View' on Global API Key")
        print("3. Add to .env file:")
        print("   CLOUDFLARE_EMAIL='your_email@example.com'")
        print("   CLOUDFLARE_API_KEY='your_global_api_key'")
        sys.exit(1)

    try:
        cf = CloudflareManager()
    except Exception as e:
        print(f"\n❌ Error initializing: {e}")
        sys.exit(1)

    selected_zone = None

    while True:
        print_menu()
        choice = input("\nSelect option: ")

        try:
            if choice == "0":
                print("\n👋 Goodbye!")
                break

            elif choice == "1":
                selected_zone = None
                zones = list_websites(cf)
                if zones and len(zones) == 1:
                    selected_zone = zones[0]
                    print(f"\n✅ Auto-selected: {selected_zone['name']}")
                elif zones and len(zones) > 1:
                    select = input(f"\nSelect website (1-{len(zones)}): ")
                    try:
                        selected_zone = zones[int(select) - 1]
                        print(f"✅ Selected: {selected_zone['name']}")
                    except (ValueError, IndexError):
                        print("❌ Invalid selection")

            elif choice in ["2", "3", "4", "5", "6", "7"]:
                if not selected_zone:
                    print("\n⚠️  Please select a website first (option 1)")
                    continue

                zone_id = selected_zone["id"]
                zone_name = selected_zone["name"]

                if choice == "2":
                    view_dns_records(cf, zone_id, zone_name)
                elif choice == "3":
                    view_settings(cf, zone_id, zone_name)
                elif choice == "4":
                    purge_cache(cf, zone_id, zone_name)
                elif choice == "5":
                    toggle_dev_mode(cf, zone_id, zone_name)
                elif choice == "6":
                    view_ssl_status(cf, zone_id, zone_name)
                elif choice == "7":
                    security_settings(cf, zone_id, zone_name)

            else:
                print("❌ Invalid option")

        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback

            traceback.print_exc()

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    main()
