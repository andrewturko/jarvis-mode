"""
Reddit provider — fetches top posts from configured subreddits.

Uses the public JSON API (no auth needed for public subreddits).

Signals emitted:
    content_reddit_new  — fresh posts available
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

from external_context.base_provider import ExternalContextProvider

REDDIT_HOT_URL = "https://www.reddit.com/r/{}/hot.json?limit={}"

# Defaults (overridden by interest-profile.json)
DEFAULT_MIN_UPVOTES = 100
DEFAULT_FETCH_COUNT = 20


def _load_reddit_config() -> Dict:
    """Load Reddit config from interest-profile.json."""
    try:
        from core.paths import INTEREST_PROFILE_FILE
        with open(INTEREST_PROFILE_FILE) as f:
            profile = json.load(f)
        return profile.get("source_configs", {}).get("reddit", {})
    except Exception:
        return {}


def _fetch_subreddit(subreddit: str, limit: int) -> List[Dict]:
    """Fetch hot posts from a subreddit via public JSON API."""
    url = REDDIT_HOT_URL.format(subreddit, limit)
    req = urllib.request.Request(url, headers={
        "User-Agent": "JarvisMode/1.0 (home-assistant content curation)",
    })

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"[reddit] Failed to fetch r/{subreddit}: {exc}", file=sys.stderr)
        return []

    posts: List[Dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        if post.get("stickied"):
            continue

        # Use the linked URL for link posts, self URL for text posts
        url = post.get("url", "")
        is_self = post.get("is_self", False)
        if is_self:
            url = f"https://www.reddit.com{post.get('permalink', '')}"

        created_utc = post.get("created_utc", 0)
        published_at = ""
        if created_utc:
            published_at = datetime.fromtimestamp(
                created_utc, tz=timezone.utc
            ).isoformat()

        selftext = post.get("selftext", "")
        summary = selftext[:200] if selftext else ""

        posts.append({
            "id": f"reddit_{post.get('id', '')}",
            "title": post.get("title", ""),
            "url": url,
            "source": "reddit",
            "source_label": f"r/{subreddit}",
            "summary": summary,
            "score_hint": post.get("score", 0),
            "published_at": published_at,
            "fetched_at": now,
            "author": post.get("author", ""),
            "tags": [subreddit],
            "ttl_hours": 24,
            "comments": post.get("num_comments", 0),
        })

    return posts


class RedditContentProvider(ExternalContextProvider):
    name = "reddit"
    stale_after_minutes = 60

    def refresh(self) -> Dict:
        config = _load_reddit_config()
        if not config.get("enabled", True):
            return {"content_items": [], "disabled": True}

        subreddits = config.get("subreddits", [])
        min_upvotes = config.get("min_upvotes", DEFAULT_MIN_UPVOTES)
        fetch_count = config.get("fetch_count", DEFAULT_FETCH_COUNT)

        all_items: List[Dict] = []

        for sub in subreddits:
            posts = _fetch_subreddit(sub, fetch_count)
            for post in posts:
                if post.get("score_hint", 0) >= min_upvotes:
                    all_items.append(post)

        return {"content_items": all_items}

    def signals(self, data: Dict) -> List[str]:
        items = data.get("content_items", [])
        if not items:
            return []
        return ["content_reddit_new"]

    def narrative(self, data: Dict) -> str:
        items = data.get("content_items", [])
        if data.get("disabled"):
            return ""
        if not items:
            return "No notable Reddit posts."

        subs = set(i.get("tags", [""])[0] for i in items if i.get("tags"))
        return f"{len(items)} posts across {len(subs)} subreddits."
