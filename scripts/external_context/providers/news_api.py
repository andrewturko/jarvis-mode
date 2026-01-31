"""
NewsAPI.org provider — fetches articles from configured domains.

Disabled by default — requires NEWS_API_KEY environment variable.

Signals emitted:
    content_news_new  — fresh articles available
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from hashlib import md5
from typing import Dict, List

from external_context.base_provider import ExternalContextProvider

NEWS_API_URL = "https://newsapi.org/v2/everything"


def _load_news_config() -> Dict:
    """Load News API config from interest-profile.json."""
    try:
        from core.paths import INTEREST_PROFILE_FILE
        with open(INTEREST_PROFILE_FILE) as f:
            profile = json.load(f)
        return profile.get("source_configs", {}).get("news_api", {})
    except Exception:
        return {}


class NewsAPIProvider(ExternalContextProvider):
    name = "news_api"
    stale_after_minutes = 120

    def refresh(self) -> Dict:
        config = _load_news_config()
        if not config.get("enabled", False):
            return {"content_items": [], "disabled": True}

        api_key_env = config.get("api_key_env", "NEWS_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            print(f"[news_api] {api_key_env} not set", file=sys.stderr)
            return {"content_items": []}

        domains = config.get("domains", [])
        fetch_count = config.get("fetch_count", 15)

        if not domains:
            return {"content_items": []}

        params = urllib.parse.urlencode({
            "domains": ",".join(domains),
            "pageSize": str(fetch_count),
            "sortBy": "publishedAt",
            "language": "en",
        })
        url = f"{NEWS_API_URL}?{params}"
        req = urllib.request.Request(url, headers={
            "X-Api-Key": api_key,
            "User-Agent": "JarvisMode/1.0",
        })

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            print(f"[news_api] Failed to fetch: {exc}", file=sys.stderr)
            return {"content_items": []}

        items: List[Dict] = []
        now = datetime.now(timezone.utc).isoformat()

        for article in data.get("articles", []):
            article_url = article.get("url", "")
            if not article_url:
                continue

            published_at = ""
            raw_date = article.get("publishedAt", "")
            if raw_date:
                try:
                    published_at = datetime.fromisoformat(
                        raw_date.replace("Z", "+00:00")
                    ).isoformat()
                except Exception:
                    pass

            source_name = article.get("source", {}).get("name", "News")
            item_id = f"news_{md5(article_url.encode()).hexdigest()[:12]}"

            items.append({
                "id": item_id,
                "title": article.get("title", ""),
                "url": article_url,
                "source": "news_api",
                "source_label": source_name,
                "summary": (article.get("description") or "")[:200],
                "score_hint": 0,
                "published_at": published_at,
                "fetched_at": now,
                "author": article.get("author") or "",
                "tags": [],
                "ttl_hours": 24,
            })

        return {"content_items": items}

    def signals(self, data: Dict) -> List[str]:
        items = data.get("content_items", [])
        if not items:
            return []
        return ["content_news_new"]

    def narrative(self, data: Dict) -> str:
        items = data.get("content_items", [])
        if data.get("disabled"):
            return ""
        if not items:
            return ""
        sources = set(i.get("source_label", "") for i in items)
        return f"{len(items)} articles from {', '.join(sorted(sources))}."
