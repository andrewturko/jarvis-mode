"""
Hacker News provider — fetches top stories via Firebase API.

Signals emitted:
    content_hacker_news_new  — fresh stories available since last check
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

from external_context.base_provider import ExternalContextProvider

HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"

# Defaults (overridden by interest-profile.json if available)
DEFAULT_MIN_POINTS = 50
DEFAULT_FETCH_COUNT = 30


def _load_hn_config() -> Dict:
    """Load HN config from interest-profile.json."""
    try:
        from core.paths import INTEREST_PROFILE_FILE
        with open(INTEREST_PROFILE_FILE) as f:
            profile = json.load(f)
        return profile.get("source_configs", {}).get("hacker_news", {})
    except Exception:
        return {}


def _fetch_json(url: str, timeout: int = 10):
    """Fetch and parse JSON from a URL."""
    req = urllib.request.Request(url, headers={"User-Agent": "JarvisMode/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class HackerNewsProvider(ExternalContextProvider):
    name = "hacker_news"
    stale_after_minutes = 30

    def refresh(self) -> Dict:
        config = _load_hn_config()
        if not config.get("enabled", True):
            return {"content_items": [], "disabled": True}

        min_points = config.get("min_points", DEFAULT_MIN_POINTS)
        fetch_count = config.get("fetch_count", DEFAULT_FETCH_COUNT)

        try:
            story_ids = _fetch_json(HN_TOP_URL)
        except Exception as exc:
            print(f"[hacker_news] Failed to fetch top stories: {exc}", file=sys.stderr)
            return {"content_items": []}

        items: List[Dict] = []
        now = datetime.now(timezone.utc).isoformat()

        for story_id in story_ids[:fetch_count]:
            try:
                item = _fetch_json(HN_ITEM_URL.format(story_id), timeout=5)
            except Exception:
                continue

            if not item or item.get("type") != "story":
                continue

            score = item.get("score", 0)
            if score < min_points:
                continue

            url = item.get("url", "")
            if not url:
                # Self-post (Ask HN, Show HN) — use HN discussion link
                url = f"https://news.ycombinator.com/item?id={story_id}"

            published_ts = item.get("time", 0)
            published_at = ""
            if published_ts:
                published_at = datetime.fromtimestamp(
                    published_ts, tz=timezone.utc
                ).isoformat()

            items.append({
                "id": f"hn_{story_id}",
                "title": item.get("title", ""),
                "url": url,
                "source": "hacker_news",
                "source_label": "Hacker News",
                "summary": "",
                "score_hint": score,
                "published_at": published_at,
                "fetched_at": now,
                "author": item.get("by", ""),
                "tags": [],
                "ttl_hours": 24,
                "comments": item.get("descendants", 0),
            })

        return {"content_items": items}

    def signals(self, data: Dict) -> List[str]:
        items = data.get("content_items", [])
        if not items:
            return []
        return ["content_hacker_news_new"]

    def narrative(self, data: Dict) -> str:
        items = data.get("content_items", [])
        if data.get("disabled"):
            return ""
        if not items:
            return "No notable Hacker News stories."
        return f"{len(items)} interesting stories on Hacker News."
