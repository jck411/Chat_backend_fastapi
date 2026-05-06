#!/usr/bin/env python3
"""
Cloudflare Website Management Script
Manage your Cloudflare website settings from VS Code
"""

import os
from typing import Dict, List, Optional

import requests


class CloudflareManager:
    """Manage Cloudflare zones, DNS, and settings via API"""

    BASE_URL = "https://api.cloudflare.com/client/v4"

    def __init__(
        self,
        api_token: Optional[str] = None,
        email: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """
        Initialize with API token or Global API Key.

        For API Token (recommended):
            Get token from: https://dash.cloudflare.com/profile/api-tokens
            Set CLOUDFLARE_API_TOKEN env variable

        For Global API Key (legacy):
            Set CLOUDFLARE_EMAIL and CLOUDFLARE_API_KEY env variables
        """
        # Try API Token first (recommended method)
        self.api_token = api_token or os.getenv("CLOUDFLARE_API_TOKEN")

        # Try Global API Key as fallback
        self.email = email or os.getenv("CLOUDFLARE_EMAIL")
        self.api_key = api_key or os.getenv("CLOUDFLARE_API_KEY")

        # Determine which authentication method to use
        if self.api_token:
            # Use API Token (Bearer auth)
            self.headers = {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            }
        elif self.email and self.api_key:
            # Use Global API Key
            self.headers = {
                "X-Auth-Email": self.email,
                "X-Auth-Key": self.api_key,
                "Content-Type": "application/json",
            }
        else:
            raise ValueError(
                "Authentication required. Set either:\n"
                "  - CLOUDFLARE_API_TOKEN (recommended), or\n"
                "  - CLOUDFLARE_EMAIL + CLOUDFLARE_API_KEY (global key)"
            )

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """Make API request"""
        url = f"{self.BASE_URL}/{endpoint}"
        response = requests.request(method, url, headers=self.headers, **kwargs)

        # Parse JSON response first to get Cloudflare's error message
        try:
            data = response.json()
        except Exception:
            # If JSON parse fails, raise the HTTP error
            response.raise_for_status()
            raise

        # Check if request was successful
        if not data.get("success"):
            errors = data.get("errors", [])
            messages = data.get("messages", [])
            error_details = f"Errors: {errors}, Messages: {messages}"
            print("\n❌ Cloudflare API Error Details:")
            print(f"   URL: {url}")
            print(f"   Status Code: {response.status_code}")
            print(f"   {error_details}")
            raise Exception(f"API Error: {error_details}")

        # Only raise HTTP error if we haven't already handled it
        response.raise_for_status()

        return data.get("result")

    # Zone Management
    def list_zones(self) -> List[Dict]:
        """List all zones (websites)"""
        return self._request("GET", "zones")

    def get_zone(self, zone_name: str) -> Dict:
        """Get zone details by name"""
        zones = self._request("GET", f"zones?name={zone_name}")
        if zones:
            return zones[0]
        raise Exception(f"Zone '{zone_name}' not found")

    def get_zone_settings(self, zone_id: str) -> List[Dict]:
        """Get all settings for a zone"""
        return self._request("GET", f"zones/{zone_id}/settings")

    def update_zone_setting(self, zone_id: str, setting: str, value) -> Dict:
        """Update a specific zone setting"""
        return self._request(
            "PATCH", f"zones/{zone_id}/settings/{setting}", json={"value": value}
        )

    # DNS Management
    def list_dns_records(self, zone_id: str) -> List[Dict]:
        """List all DNS records for a zone"""
        return self._request("GET", f"zones/{zone_id}/dns_records")

    def create_dns_record(
        self,
        zone_id: str,
        record_type: str,
        name: str,
        content: str,
        proxied: bool = True,
        ttl: int = 1,
    ) -> Dict:
        """Create a new DNS record"""
        return self._request(
            "POST",
            f"zones/{zone_id}/dns_records",
            json={
                "type": record_type,
                "name": name,
                "content": content,
                "proxied": proxied,
                "ttl": ttl,
            },
        )

    def update_dns_record(self, zone_id: str, record_id: str, **kwargs) -> Dict:
        """Update a DNS record"""
        return self._request(
            "PUT", f"zones/{zone_id}/dns_records/{record_id}", json=kwargs
        )

    def delete_dns_record(self, zone_id: str, record_id: str) -> Dict:
        """Delete a DNS record"""
        return self._request("DELETE", f"zones/{zone_id}/dns_records/{record_id}")

    # Cache Management
    def purge_cache(self, zone_id: str, purge_everything: bool = True) -> Dict:
        """Purge cache for zone"""
        return self._request(
            "POST",
            f"zones/{zone_id}/purge_cache",
            json={"purge_everything": purge_everything},
        )

    def purge_cache_urls(self, zone_id: str, urls: List[str]) -> Dict:
        """Purge specific URLs from cache"""
        return self._request(
            "POST", f"zones/{zone_id}/purge_cache", json={"files": urls}
        )

    # SSL/TLS Management
    def get_ssl_setting(self, zone_id: str) -> str:
        """Get SSL/TLS encryption mode"""
        settings = self.get_zone_settings(zone_id)
        for setting in settings:
            if setting["id"] == "ssl":
                return setting["value"]
        return "unknown"

    def set_ssl_mode(self, zone_id: str, mode: str) -> Dict:
        """
        Set SSL/TLS mode
        Modes: off, flexible, full, strict
        """
        return self.update_zone_setting(zone_id, "ssl", mode)

    # Development Mode
    def enable_dev_mode(self, zone_id: str) -> Dict:
        """Enable development mode (bypasses cache)"""
        return self.update_zone_setting(zone_id, "development_mode", "on")

    def disable_dev_mode(self, zone_id: str) -> Dict:
        """Disable development mode"""
        return self.update_zone_setting(zone_id, "development_mode", "off")

    # Security Settings
    def set_security_level(self, zone_id: str, level: str) -> Dict:
        """
        Set security level
        Levels: essentially_off, low, medium, high, under_attack
        """
        return self.update_zone_setting(zone_id, "security_level", level)

    def enable_https_redirect(self, zone_id: str) -> Dict:
        """Always use HTTPS"""
        return self.update_zone_setting(zone_id, "always_use_https", "on")

    def disable_https_redirect(self, zone_id: str) -> Dict:
        """Disable always use HTTPS"""
        return self.update_zone_setting(zone_id, "always_use_https", "off")


def main():
    """Example usage"""
    import sys
    from pathlib import Path

    from dotenv import load_dotenv

    # Load environment variables from .env file
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)

    # Check for authentication credentials
    has_token = bool(os.getenv("CLOUDFLARE_API_TOKEN"))
    has_global_key = bool(
        os.getenv("CLOUDFLARE_EMAIL") and os.getenv("CLOUDFLARE_API_KEY")
    )

    if not (has_token or has_global_key):
        print("Error: Cloudflare authentication not configured")
        print("\nOption 1: API Token (Recommended)")
        print("  Get from: https://dash.cloudflare.com/profile/api-tokens")
        print("  Set in .env: CLOUDFLARE_API_TOKEN='your_token_here'")
        print("\nOption 2: Global API Key")
        print(
            "  Get from: https://dash.cloudflare.com/profile/api-tokens (API Keys section)"
        )
        print("  Set in .env:")
        print("    CLOUDFLARE_EMAIL='your_email@example.com'")
        print("    CLOUDFLARE_API_KEY='your_global_api_key'")
        sys.exit(1)

    cf = CloudflareManager()

    # List all zones
    print("📋 Your Cloudflare Zones:")
    print("-" * 50)
    zones = cf.list_zones()
    for zone in zones:
        print(f"  • {zone['name']}")
        print(f"    Status: {zone['status']}")
        print(f"    ID: {zone['id']}")
        print()

    if not zones:
        print("No zones found.")
        return

    # Show DNS records for first zone
    if zones:
        zone = zones[0]
        print(f"\n🌐 DNS Records for {zone['name']}:")
        print("-" * 50)
        records = cf.list_dns_records(zone["id"])
        for record in records[:10]:  # First 10
            proxied = "🟠 Proxied" if record.get("proxied") else "⚪ DNS Only"
            print(
                f"  {record['type']:6} {record['name']:30} → {record['content']:20} {proxied}"
            )


if __name__ == "__main__":
    main()
