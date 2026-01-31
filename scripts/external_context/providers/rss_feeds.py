"""
RSS/Atom feed provider — parses configured feeds for content curation.

Signals emitted:
    content_rss_new  — fresh items available from RSS feeds
"""

from __future__ import annotations

import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional
from hashlib import md5

from external_context.base_provider import ExternalContextProvider


def _load_rss_config() -> Dict:
    """Load RSS config from interest-profile.json."""
    try:
        from core.paths import INTEREST_PROFILE_FILE
        with open(INTEREST_PROFILE_FILE) as f:
            profile = json.load(f)
        return profile.get("source_configs", {}).get("rss", {})
    except Exception:
        return {}


def _fetch_feed(url: str, timeout: int = 15) -> Optional[bytes]:
    """Fetch raw feed XML."""
    req = urllib.request.Request(url, headers={"User-Agent": "JarvisMode/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        print(f"[rss] Failed to fetch {url}: {exc}", file=sys.stderr)
        return None


def _parse_rss_date(date_str: str) -> str:
    """Parse various RSS/Atom date formats to ISO string."""
    if not date_str:
        return ""
    # Try RFC 2822 (RSS 2.0)
    try:
        return parsedate_to_datetime(date_str).isoformat()
    except Exception:
        pass
    # Try ISO 8601 (Atom)
    try:
        # Handle timezone suffixes
        cleaned = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).isoformat()
    except Exception:
        pass
    return ""


def _parse_feed(xml_bytes: bytes, feed_label: str, feed_trust: float) -> List[Dict]:
    """Parse RSS 2.0 or Atom feed XML into normalized content items."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        print(f"[rss] XML parse error for {feed_label}: {exc}", file=sys.stderr)
        return []

    items: List[Dict] = []
    now = datetime.now(timezone.utc).isoformat()

    # Detect Atom namespace
    atom_ns = ""
    if root.tag.startswith("{"):
        atom_ns = root.tag.split("}")[0] + "}"

    if atom_ns and "Atom" in atom_ns or root.tag.endswith("feed"):
        # Atom feed
        ns = {"atom": atom_ns.strip("{}")} if atom_ns else {}
        prefix = atom_ns

        for entry in root.findall(f"{prefix}entry"):
            title_el = entry.find(f"{prefix}title")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            # Get link (prefer alternate)
            url = ""
            for link in entry.findall(f"{prefix}link"):
                rel = link.get("rel", "alternate")
                if rel == "alternate" or not url:
                    url = link.get("href", "")

            summary_el = entry.find(f"{prefix}summary") or entry.find(f"{prefix}content")
            summary = ""
            if summary_el is not None and summary_el.text:
                summary = summary_el.text.strip()[:200]

            published_el = (entry.find(f"{prefix}published")
                           or entry.find(f"{prefix}updated"))
            published_at = ""
            if published_el is not None and published_el.text:
                published_at = _parse_rss_date(published_el.text.strip())

            author_el = entry.find(f"{prefix}author/{prefix}name")
            author = author_el.text.strip() if author_el is not None and author_el.text else ""

            if title and url:
                item_id = f"rss_{md5(url.encode()).hexdigest()[:12]}"
                items.append({
                    "id": item_id,
                    "title": title,
                    "url": url,
                    "source": "rss",
                    "source_label": feed_label,
                    "summary": summary,
                    "score_hint": feed_trust * 100,
                    "published_at": published_at,
                    "fetched_at": now,
                    "author": author,
                    "tags": [],
                    "ttl_hours": 168,  # Blog posts: 1 week TTL
                    "trust": feed_trust,
                })
    else:
        # RSS 2.0 feed
        channel = root.find("channel")
        if channel is None:
            return []

        for item in channel.findall("item"):
            title_el = item.find("title")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            link_el = item.find("link")
            url = link_el.text.strip() if link_el is not None and link_el.text else ""

            desc_el = item.find("description")
            summary = ""
            if desc_el is not None and desc_el.text:
                summary = desc_el.text.strip()[:200]

            pub_el = item.find("pubDate")
            published_at = ""
            if pub_el is not None and pub_el.text:
                published_at = _parse_rss_date(pub_el.text.strip())

            author_el = item.find("author") or item.find("{http://purl.org/dc/elements/1.1/}creator")
            author = ""
            if author_el is not None and author_el.text:
                author = author_el.text.strip()

            if title and url:
                item_id = f"rss_{md5(url.encode()).hexdigest()[:12]}"
                items.append({
                    "id": item_id,
                    "title": title,
                    "url": url,
                    "source": "rss",
                    "source_label": feed_label,
                    "summary": summary,
                    "score_hint": feed_trust * 100,
                    "published_at": published_at,
                    "fetched_at": now,
                    "author": author,
                    "tags": [],
                    "ttl_hours": 168,
                    "trust": feed_trust,
                })

    return items


class RSSFeedsProvider(ExternalContextProvider):
    name = "rss"
    stale_after_minutes = 60

    def refresh(self) -> Dict:
        config = _load_rss_config()
        if not config.get("enabled", True):
            return {"content_items": [], "disabled": True}

        feeds = config.get("feeds", [])
        all_items: List[Dict] = []

        for feed_cfg in feeds:
            url = feed_cfg.get("url", "")
            label = feed_cfg.get("label", url)
            trust = feed_cfg.get("trust", 0.5)

            if not url:
                continue

            raw = _fetch_feed(url)
            if raw:
                items = _parse_feed(raw, label, trust)
                all_items.extend(items)

        return {"content_items": all_items}

    def signals(self, data: Dict) -> List[str]:
        items = data.get("content_items", [])
        if not items:
            return []
        return ["content_rss_new"]

    def narrative(self, data: Dict) -> str:
        items = data.get("content_items", [])
        if data.get("disabled"):
            return ""
        if not items:
            return "No new RSS feed items."

        # Group by source label
        sources = set(i.get("source_label", "") for i in items)
        return f"{len(items)} items from {len(sources)} RSS feeds."
