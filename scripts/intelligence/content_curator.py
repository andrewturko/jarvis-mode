"""
Content curator — scores, ranks, and selects content for delivery.

Parallel to suggestion_engine.py but for articles/content rather than
home automation suggestions. Consults interest-profile.json, deduplicates
against content-history.json, and integrates with Instapaper for saving.

Public API:
    get_curated_content(mode, context_result, max_items)
    record_content_feedback(content_id, action)
    get_content_stats()
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from core.logger import get_logger
from core.paths import INTEREST_PROFILE_FILE, CONTENT_HISTORY_FILE, EXTERNAL_CONTEXT_FILE
from intelligence._helpers import load_json, save_json

logger = get_logger("jarvis.content_curator")

# Scoring weights
W_TOPIC = 0.40
W_SOURCE = 0.15
W_RECENCY = 0.15
W_ENGAGEMENT = 0.15
W_LEARNED = 0.15

# Engagement normalization factors (source-native score / factor → 0..1)
ENGAGEMENT_CALIBRATION = {
    "hacker_news": 300,
    "reddit": 500,
    "rss": 1,        # RSS has no engagement signal; handled separately
    "news_api": 1,
}

# History limits
MAX_HISTORY_ITEMS = 500
MAX_SEEN_URLS_DAYS = 30

# Feedback deltas for learned weights
FEEDBACK_DELTA = {
    "saved": +0.05,
    "clicked": +0.02,
    "dismissed": -0.05,
    "ignored": -0.02,
}

WEIGHT_CAP = 0.3
DECAY_RATE_PER_DAY = 0.01


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_profile() -> Dict:
    return load_json(INTEREST_PROFILE_FILE)


def _load_history() -> Dict:
    data = load_json(CONTENT_HISTORY_FILE)
    if not data:
        data = {"delivered": [], "seen_urls": {}, "daily_stats": {}}
    return data


def _save_history(history: Dict):
    # Prune before save
    history["delivered"] = history.get("delivered", [])[-MAX_HISTORY_ITEMS:]

    # Prune old seen URLs
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_SEEN_URLS_DAYS)).isoformat()
    seen = history.get("seen_urls", {})
    history["seen_urls"] = {
        url: ts for url, ts in seen.items() if ts > cutoff
    }

    save_json(CONTENT_HISTORY_FILE, history)


def _normalize_url(url: str) -> str:
    """Strip tracking params and trailing slashes for dedup."""
    # Remove common tracking params
    url = re.sub(r'[?&](utm_\w+|ref|source|fbclid|gclid)=[^&]*', '', url)
    url = re.sub(r'\?$', '', url)
    return url.rstrip("/").lower()


def _get_all_content_items() -> List[Dict]:
    """Collect content_items from all providers in the external context cache."""
    cache = load_json(EXTERNAL_CONTEXT_FILE)
    items: List[Dict] = []

    for name, provider_data in cache.get("providers", {}).items():
        data = provider_data.get("data", {})
        content_items = data.get("content_items", [])
        items.extend(content_items)

    return items


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_daily_stats(history: Dict) -> Dict:
    key = _today_key()
    stats = history.get("daily_stats", {})
    if key not in stats:
        stats[key] = {
            "digest_count": 0,
            "realtime_count": 0,
            "saved_count": 0,
            "dismissed_count": 0,
            "last_realtime_at": None,
        }
    return stats[key]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_topic_relevance(item: Dict, profile: Dict) -> Tuple[float, List[str]]:
    """Score how well the item matches user's interest topics.

    Returns (score, matched_topics).
    """
    title = item.get("title", "").lower()
    summary = item.get("summary", "").lower()
    text = f"{title} {summary}"

    topics = profile.get("topics", {})
    adjustments = profile.get("learned_weights", {}).get("adjustments", {})

    best_score = 0.0
    matched: List[str] = []

    for topic_name, topic_cfg in topics.items():
        keywords = topic_cfg.get("keywords", [])
        weight = topic_cfg.get("weight", 0.5)
        source_bonus_list = topic_cfg.get("sources_bonus", [])

        # Check keyword matches
        match_count = sum(1 for kw in keywords if kw.lower() in text)
        if match_count == 0:
            continue

        matched.append(topic_name)

        # Base topic score from config weight
        topic_score = weight

        # Apply source bonus
        item_source = item.get("source", "")
        if item_source in source_bonus_list:
            topic_score = min(1.0, topic_score + 0.1)

        # Apply learned adjustment
        adj = adjustments.get(topic_name, 0.0)
        topic_score = max(0.0, min(1.0, topic_score + adj))

        # Boost for multiple keyword matches
        if match_count > 1:
            topic_score = min(1.0, topic_score + 0.05 * (match_count - 1))

        best_score = max(best_score, topic_score)

    return best_score, matched


def _score_source_quality(item: Dict, profile: Dict) -> float:
    """Score based on source trustworthiness and community signals."""
    source = item.get("source", "")

    # RSS feeds carry explicit trust
    if source == "rss":
        return item.get("trust", 0.5)

    # For HN/Reddit, use normalized community score
    calibration = ENGAGEMENT_CALIBRATION.get(source, 1)
    if calibration <= 1:
        return 0.5  # no signal

    score_hint = item.get("score_hint", 0)
    # Source quality is about the source itself, not just this post's score
    # High-score posts on reputable sources get quality boost
    source_adj = profile.get("learned_weights", {}).get("source_adjustments", {}).get(source, 0.0)
    base = min(1.0, score_hint / (calibration * 2))
    return max(0.0, min(1.0, base + 0.5 + source_adj))


def _score_recency(item: Dict) -> float:
    """Exponential decay based on publish time and TTL."""
    published_at = item.get("published_at", "")
    ttl_hours = item.get("ttl_hours", 24)

    if not published_at:
        return 0.3  # unknown age — neutral

    try:
        pub_dt = datetime.fromisoformat(published_at)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        hours_old = max(0, (now - pub_dt).total_seconds() / 3600)
        return max(0.0, 1.0 - hours_old / ttl_hours)
    except Exception:
        return 0.3


def _score_engagement(item: Dict) -> float:
    """Normalized community engagement score."""
    source = item.get("source", "")
    score_hint = item.get("score_hint", 0)
    calibration = ENGAGEMENT_CALIBRATION.get(source, 1)

    if source == "rss":
        return 0.5  # RSS has no engagement signal

    return min(1.0, score_hint / calibration)


def _score_learned(item: Dict, matched_topics: List[str], profile: Dict) -> float:
    """Learned adjustment from feedback history."""
    adjustments = profile.get("learned_weights", {}).get("adjustments", {})
    source_adj = profile.get("learned_weights", {}).get("source_adjustments", {})

    if not matched_topics and not source_adj:
        return 0.5  # neutral baseline

    # Average topic adjustments
    topic_boosts = [adjustments.get(t, 0.0) for t in matched_topics]
    avg_topic = sum(topic_boosts) / len(topic_boosts) if topic_boosts else 0.0

    # Source adjustment
    src = item.get("source", "")
    src_adj = source_adj.get(src, 0.0)

    # Map from [-0.3, 0.3] range to [0.2, 0.8] score
    combined = avg_topic + src_adj * 0.5
    return max(0.2, min(0.8, 0.5 + combined))


def _check_anti_topics(item: Dict, profile: Dict) -> float:
    """Check for anti-topic keyword matches. Returns negative penalty or 0."""
    anti = profile.get("anti_topics", {})
    keywords = anti.get("keywords", [])
    penalty = anti.get("penalty", -0.8)

    title = item.get("title", "").lower()
    summary = item.get("summary", "").lower()
    text = f"{title} {summary}"

    for kw in keywords:
        if kw.lower() in text:
            return penalty

    return 0.0


def score_item(item: Dict, profile: Dict) -> Tuple[float, List[str]]:
    """Score a single content item. Returns (score, matched_topics)."""
    topic_score, matched_topics = _score_topic_relevance(item, profile)
    source_score = _score_source_quality(item, profile)
    recency_score = _score_recency(item)
    engagement_score = _score_engagement(item)
    learned_score = _score_learned(item, matched_topics, profile)
    anti_penalty = _check_anti_topics(item, profile)

    final = (
        topic_score * W_TOPIC
        + source_score * W_SOURCE
        + recency_score * W_RECENCY
        + engagement_score * W_ENGAGEMENT
        + learned_score * W_LEARNED
        + anti_penalty
    )

    return max(0.0, min(1.0, final)), matched_topics


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_curated_content(
    mode: str = "digest",
    context_result: Optional[Dict] = None,
    max_items: int = 5,
) -> List[Dict]:
    """Score and rank content from all providers.

    Args:
        mode: "digest" for morning digest, "realtime" for throughout-the-day drops.
        context_result: From infer_context(), for timing/silence awareness.
        max_items: Maximum items to return.

    Returns:
        List of scored content items, sorted by score descending.
        Each item includes _score and _matched_topics.
    """
    profile = _load_profile()
    if not profile:
        logger.warning("no_interest_profile")
        return []

    history = _load_history()
    seen_urls = history.get("seen_urls", {})
    daily = _get_daily_stats(history)

    # Get all content items from provider cache
    all_items = _get_all_content_items()
    if not all_items:
        return []

    # Filter and score
    scored: List[Dict] = []

    for item in all_items:
        url = item.get("url", "")
        if not url:
            continue

        # Dedup by normalized URL
        norm_url = _normalize_url(url)
        if norm_url in seen_urls:
            continue

        # TTL check
        recency = _score_recency(item)
        if recency <= 0.0:
            continue

        score, matched = score_item(item, profile)

        item_copy = dict(item)
        item_copy["_score"] = round(score, 3)
        item_copy["_matched_topics"] = matched

        scored.append(item_copy)

    # Sort by score descending
    scored.sort(key=lambda x: x["_score"], reverse=True)

    # Mode-specific filtering
    delivery = profile.get("delivery", {})

    if mode == "realtime":
        min_score = delivery.get("realtime_min_score", 0.75)
        max_per_day = delivery.get("realtime_max_per_day", 3)
        min_hours_apart = delivery.get("realtime_min_hours_apart", 2)

        # Budget check
        if daily.get("realtime_count", 0) >= max_per_day:
            return []

        # Timing check
        last_rt = daily.get("last_realtime_at")
        if last_rt:
            try:
                last_dt = datetime.fromisoformat(last_rt)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                hours_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                if hours_since < min_hours_apart:
                    return []
            except Exception:
                pass

        # Context check — respect focus contexts
        if context_result:
            ctx_name = context_result.get("context", "")
            try:
                from intelligence._helpers import get_life_model
                life_model = get_life_model()
                focus = life_model.get("focus_contexts", [])
                if ctx_name in focus:
                    return []
            except Exception:
                pass

        # Filter to items above threshold
        scored = [i for i in scored if i["_score"] >= min_score]

        # Realtime returns at most 1 item
        max_items = 1

    # Take top N
    result = scored[:max_items]

    # Auto-save to Instapaper and record delivery
    if result:
        auto_save_threshold = delivery.get("instapaper_auto_save_above", 0.6)
        _process_delivery(result, mode, history, auto_save_threshold)

    return result


def _process_delivery(items: List[Dict], mode: str, history: Dict, auto_save_threshold: float):
    """Save to Instapaper and record in history."""
    now = datetime.now(timezone.utc).isoformat()
    daily = _get_daily_stats(history)

    try:
        from services.instapaper_service import InstapaperService
        instapaper = InstapaperService()
    except Exception:
        instapaper = None

    for item in items:
        score = item.get("_score", 0)
        url = item.get("url", "")
        title = item.get("title", "")
        norm_url = _normalize_url(url)

        # Save to Instapaper if above threshold
        saved_to_instapaper = False
        if instapaper and instapaper.is_configured() and score >= auto_save_threshold:
            summary = item.get("summary", "")
            saved_to_instapaper = instapaper.save(url, title=title, selection=summary)
            if saved_to_instapaper:
                daily["saved_count"] = daily.get("saved_count", 0) + 1

        # Add to item for the agent to know
        item["instapaper_saved"] = saved_to_instapaper

        # Record delivery
        history.setdefault("delivered", []).append({
            "content_id": item.get("id", ""),
            "url": url,
            "url_normalized": norm_url,
            "title": title,
            "source": item.get("source", ""),
            "score": score,
            "matched_topics": item.get("_matched_topics", []),
            "delivered_at": now,
            "delivery_mode": mode,
            "instapaper_saved": saved_to_instapaper,
            "feedback": None,
            "feedback_at": None,
        })

        # Mark URL as seen
        history.setdefault("seen_urls", {})[norm_url] = now

    # Update daily stats
    if mode == "digest":
        daily["digest_count"] = daily.get("digest_count", 0) + len(items)
    else:
        daily["realtime_count"] = daily.get("realtime_count", 0) + len(items)
        daily["last_realtime_at"] = now

    history.setdefault("daily_stats", {})[_today_key()] = daily
    _save_history(history)


def record_content_feedback(content_id: str, action: str):
    """Update interest weights based on engagement feedback.

    Args:
        content_id: The content item ID (e.g., "hn_12345").
        action: One of "saved", "clicked", "dismissed", "ignored".
    """
    if action not in FEEDBACK_DELTA:
        logger.warning("invalid_feedback_action", action=action)
        return

    history = _load_history()
    now = datetime.now(timezone.utc).isoformat()

    # Find the delivered item
    item = None
    for entry in reversed(history.get("delivered", [])):
        if entry.get("content_id") == content_id:
            item = entry
            break

    if not item:
        logger.warning("content_feedback_not_found", content_id=content_id)
        return

    # Update feedback
    item["feedback"] = action
    item["feedback_at"] = now

    # Update daily stats
    daily = _get_daily_stats(history)
    if action == "dismissed":
        daily["dismissed_count"] = daily.get("dismissed_count", 0) + 1

    # Adjust learned weights in profile
    profile = _load_profile()
    if not profile:
        _save_history(history)
        return

    learned = profile.setdefault("learned_weights", {})
    adjustments = learned.setdefault("adjustments", {})
    source_adj = learned.setdefault("source_adjustments", {})

    delta = FEEDBACK_DELTA[action]
    matched_topics = item.get("matched_topics", [])
    source = item.get("source", "")

    for topic in matched_topics:
        current = adjustments.get(topic, 0.0)
        adjustments[topic] = max(-WEIGHT_CAP, min(WEIGHT_CAP, current + delta))

    if source:
        current_src = source_adj.get(source, 0.0)
        source_adj[source] = max(-WEIGHT_CAP / 2, min(WEIGHT_CAP / 2, current_src + delta * 0.5))

    save_json(INTEREST_PROFILE_FILE, profile)
    _save_history(history)

    logger.info("content_feedback_recorded",
                content_id=content_id, action=action,
                topics=matched_topics, delta=delta)


def decay_learned_weights():
    """Decay learned weights toward neutral (0). Called during maintenance."""
    profile = _load_profile()
    if not profile:
        return

    learned = profile.get("learned_weights", {})
    last_decay = learned.get("last_decay")
    now = datetime.now(timezone.utc)

    if last_decay:
        try:
            last_dt = datetime.fromisoformat(last_decay)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            days_since = (now - last_dt).total_seconds() / 86400
            if days_since < 1.0:
                return  # decay once per day
        except Exception:
            pass

    adjustments = learned.get("adjustments", {})
    source_adj = learned.get("source_adjustments", {})
    changed = False

    for key in list(adjustments.keys()):
        val = adjustments[key]
        if abs(val) < 0.001:
            del adjustments[key]
            changed = True
        else:
            decayed = val * (1 - DECAY_RATE_PER_DAY)
            adjustments[key] = round(decayed, 4)
            changed = True

    for key in list(source_adj.keys()):
        val = source_adj[key]
        if abs(val) < 0.001:
            del source_adj[key]
            changed = True
        else:
            decayed = val * (1 - DECAY_RATE_PER_DAY)
            source_adj[key] = round(decayed, 4)
            changed = True

    if changed:
        learned["last_decay"] = now.isoformat()
        save_json(INTEREST_PROFILE_FILE, profile)


def get_content_stats() -> Dict:
    """Return stats for debugging."""
    history = _load_history()
    daily = _get_daily_stats(history)
    profile = _load_profile()

    all_items = _get_all_content_items()
    delivered = history.get("delivered", [])

    return {
        "total_cached_items": len(all_items),
        "total_delivered": len(delivered),
        "today": daily,
        "learned_adjustments": profile.get("learned_weights", {}).get("adjustments", {}),
        "source_adjustments": profile.get("learned_weights", {}).get("source_adjustments", {}),
    }
