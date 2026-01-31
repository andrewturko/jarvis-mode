"""
Instapaper Simple API service for saving articles to reading list.

Docs: https://www.instapaper.com/api/simple
Requires only username (email) and password — no OAuth needed.

Environment variables:
    INSTAPAPER_USERNAME - Instapaper account email
    INSTAPAPER_PASSWORD - Instapaper account password
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

INSTAPAPER_ADD_URL = "https://www.instapaper.com/api/add"


class InstapaperService:
    """Save articles to Instapaper reading list via Simple API."""

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        self.username = username or os.environ.get("INSTAPAPER_USERNAME", "")
        self.password = password or os.environ.get("INSTAPAPER_PASSWORD", "")

        # Fallback to openclaw.json (same pattern as HAService)
        if not self.username or not self.password:
            try:
                config_path = Path.home() / ".openclaw" / "openclaw.json"
                with open(config_path) as f:
                    env_vars = json.load(f).get("env", {}).get("vars", {})
                    self.username = self.username or env_vars.get("INSTAPAPER_USERNAME", "")
                    self.password = self.password or env_vars.get("INSTAPAPER_PASSWORD", "")
            except Exception:
                pass

    def save(self, url: str, title: Optional[str] = None,
             selection: Optional[str] = None) -> bool:
        """Save a URL to Instapaper.

        Args:
            url: The URL to save.
            title: Optional title override.
            selection: Optional excerpt/description (max 500 chars).

        Returns:
            True on success (HTTP 200/201), False on failure.
        """
        if not self.is_configured():
            print("[instapaper] Not configured — set INSTAPAPER_USERNAME and INSTAPAPER_PASSWORD",
                  file=sys.stderr)
            return False

        params = {
            "username": self.username,
            "password": self.password,
            "url": url,
        }
        if title:
            params["title"] = title
        if selection:
            params["selection"] = selection[:500]

        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(INSTAPAPER_ADD_URL, data=data)

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201)
        except urllib.error.HTTPError as exc:
            print(f"[instapaper] HTTP {exc.code}: {exc.reason}", file=sys.stderr)
            return False
        except Exception as exc:
            print(f"[instapaper] Failed to save {url}: {exc}", file=sys.stderr)
            return False

    def is_configured(self) -> bool:
        """Check if credentials are available."""
        return bool(self.username and self.password)
