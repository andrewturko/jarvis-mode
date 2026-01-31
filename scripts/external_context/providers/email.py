"""
Gmail provider — pulls important unread emails via ``gog gmail messages search``.

Signals emitted:
    email_important_unread  — one or more non-promo/social unread messages
    email_delivery          — package / shipping notification
    email_travel            — flight, hotel, itinerary
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Dict, List, Optional

from external_context.base_provider import ExternalContextProvider

GOG_ACCOUNT = os.environ.get("GOG_ACCOUNT", "andrewpturko@gmail.com")
GOG_TIMEOUT = 15

# ---------------------------------------------------------------------------
# Classification keywords
# ---------------------------------------------------------------------------

DELIVERY_KEYWORDS = [
    "delivered", "delivery", "shipped", "tracking", "package",
    "ups", "fedex", "usps", "amazon", "out for delivery",
    "arriving today", "informed delivery",
]
TRAVEL_KEYWORDS = [
    "flight", "boarding pass", "itinerary", "check-in",
    "reservation confirm", "hotel", "airline", "booking",
    "trip", "travel",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_gog(args: List[str]) -> Optional[object]:
    """Run a gog CLI command with timeout, return parsed JSON or None."""
    cmd = ["gog"] + args + ["--json", "--account", GOG_ACCOUNT]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=GOG_TIMEOUT,
        )
        if result.returncode != 0:
            print(f"[email] gog error: {result.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(result.stdout)
    except FileNotFoundError:
        print("[email] gog CLI not found in PATH", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("[email] gog timed out", file=sys.stderr)
        return None
    except json.JSONDecodeError as exc:
        print(f"[email] gog output not valid JSON: {exc}", file=sys.stderr)
        return None


def _classify_email(subject: str, from_addr: str, snippet: str = "") -> List[str]:
    text = f"{subject} {from_addr} {snippet}".lower()
    tags: List[str] = []
    if any(kw in text for kw in DELIVERY_KEYWORDS):
        tags.append("email_delivery")
    if any(kw in text for kw in TRAVEL_KEYWORDS):
        tags.append("email_travel")
    return tags


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class EmailProvider(ExternalContextProvider):
    name = "email"
    stale_after_minutes = 5

    def refresh(self) -> Dict:
        raw = _run_gog([
            "gmail", "messages", "search",
            "in:inbox is:unread -category:promotions -category:social",
            "--max", "5",
        ])

        messages: List[Dict] = []
        if raw:
            if isinstance(raw, dict):
                messages = raw.get("messages", [])
            elif isinstance(raw, list):
                messages = raw

        data: Dict = {"important_unread": [], "flags": []}
        flags_set = set()

        for msg in messages:
            subject = msg.get("subject", "")
            from_addr = msg.get("from", "")
            snippet = msg.get("snippet", "")

            data["important_unread"].append({
                "from": from_addr,
                "subject": subject,
                "snippet": snippet,
            })

            msg_flags = _classify_email(subject, from_addr, snippet)
            flags_set.update(msg_flags)

        data["flags"] = sorted(flags_set)
        return data

    def signals(self, data: Dict) -> List[str]:
        sigs: List[str] = []
        if data.get("important_unread"):
            sigs.append("email_important_unread")
            sigs.extend(data.get("flags", []))
        # Deduplicate preserving order
        seen = set()
        deduped: List[str] = []
        for s in sigs:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped

    def narrative(self, data: Dict) -> str:
        count = len(data.get("important_unread", []))
        if count == 0:
            return "No important unread emails."
        return f"{count} unread email{'s' if count != 1 else ''}."
