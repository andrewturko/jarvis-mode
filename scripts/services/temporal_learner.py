#!/usr/bin/env python3
"""
Temporal Learner — Adaptive time-based context probability system.

Instead of hardcoded rules like "meal time = hours 7-8, 12-13, 18-20",
this module learns WHEN contexts actually occur from real observations.

Core idea:
  - Maintain per-context hourly probability distributions (24 bins)
  - Each observation records: timestamp, polarity (positive/negative), weight
  - Observations decay over time (half-life ~2 weeks) so patterns stay fresh
  - Provides get_time_probability(context, hour) -> float for use in scoring

Bootstrap:
  - Seeded with reasonable priors from the old hardcoded times
  - Priors have low weight so real data quickly overrides them

Graceful degradation:
  - No data? Falls back to uniform prior (every hour equally likely)
  - Missing file? Creates it fresh with defaults
"""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

# --- Configuration ---

# Observations lose half their weight after this many days
HALF_LIFE_DAYS = 14.0

# Minimum probability floor — even at "impossible" hours, we never return 0
# This prevents a single missing hour from completely blocking a context
MIN_PROBABILITY = 0.01

# Weight assigned to bootstrap prior observations (low so real data wins fast)
PRIOR_WEIGHT = 0.5

# Weight for a real positive observation (context confirmed)
POSITIVE_WEIGHT = 1.0

# Weight for a negative observation (suggestion rejected at this time)
NEGATIVE_WEIGHT = -0.5

# Maximum observations to keep per context (old ones get pruned)
MAX_OBSERVATIONS_PER_CONTEXT = 500

# --- File paths ---

from core.paths import TEMPORAL_FILE


def _load_temporal_data() -> dict:
    """Load temporal patterns file, or return empty structure."""
    try:
        with open(TEMPORAL_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty_data()


def _save_temporal_data(data: dict):
    """Save temporal patterns to disk."""
    with open(TEMPORAL_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _empty_data() -> dict:
    """Return empty temporal data structure."""
    return {
        "_description": "Learned temporal patterns. Auto-updated by temporal_learner.py.",
        "_version": 1,
        "contexts": {}
    }


def _decay_weight(observation_time: str, base_weight: float, now: Optional[datetime] = None) -> float:
    """
    Compute decayed weight for an observation.

    Uses exponential decay: weight * 2^(-days_elapsed / half_life)

    Args:
        observation_time: ISO timestamp of when the observation was recorded
        base_weight: Original weight of the observation
        now: Current time (defaults to datetime.now())

    Returns:
        Decayed weight (always >= 0 for positive, <= 0 for negative)
    """
    if now is None:
        now = datetime.now()

    try:
        obs_dt = datetime.fromisoformat(observation_time)
    except (ValueError, TypeError):
        return 0.0

    days_elapsed = (now - obs_dt).total_seconds() / 86400.0
    if days_elapsed < 0:
        days_elapsed = 0  # Future timestamps treated as now

    decay_factor = math.pow(2, -days_elapsed / HALF_LIFE_DAYS)
    return base_weight * decay_factor


def _build_hourly_distribution(observations: list, now: Optional[datetime] = None) -> list:
    """
    Build a 24-element hourly probability distribution from observations.

    Each observation contributes its decayed weight to the hour it occurred.
    Negative observations reduce the count (but can't go below 0).

    Returns list of 24 floats (one per hour), normalized to sum to 1.0.
    """
    if now is None:
        now = datetime.now()

    # Accumulate decayed weights per hour
    hourly = [0.0] * 24

    for obs in observations:
        ts = obs.get("timestamp", "")
        weight = obs.get("weight", POSITIVE_WEIGHT)
        hour = obs.get("hour")

        # If hour not stored, extract from timestamp
        if hour is None:
            try:
                hour = datetime.fromisoformat(ts).hour
            except (ValueError, TypeError):
                continue

        if not (0 <= hour <= 23):
            continue

        decayed = _decay_weight(ts, weight, now)
        hourly[hour] += decayed

    # Clamp negatives to 0 (negative observations can suppress but not go below zero)
    hourly = [max(0.0, h) for h in hourly]

    # Normalize to probability distribution
    total = sum(hourly)
    if total <= 0:
        # No data — return uniform
        return [1.0 / 24.0] * 24

    # Apply minimum probability floor and re-normalize
    distribution = []
    for h in hourly:
        p = h / total
        distribution.append(max(p, MIN_PROBABILITY))

    # Re-normalize after applying floor
    total_p = sum(distribution)
    distribution = [p / total_p for p in distribution]

    return distribution


# --- Public API ---

def get_time_probability(context: str, hour: Optional[int] = None) -> float:
    """
    Get the probability of a context occurring at a given hour.

    This is the main function used by the context inference engine.
    Replaces hardcoded time checks like is_meal_time.

    Args:
        context: Context name (e.g., "eating", "cooking", "winding_down")
        hour: Hour of day (0-23). Defaults to current hour.

    Returns:
        Probability float (0.0 to 1.0). Higher = more likely at this hour.
        Returns ~0.042 (1/24) if no data exists (uniform prior).
    """
    if hour is None:
        hour = datetime.now().hour

    hour = max(0, min(23, hour))

    data = _load_temporal_data()
    ctx_data = data.get("contexts", {}).get(context, {})
    observations = ctx_data.get("observations", [])

    if not observations:
        return 1.0 / 24.0  # Uniform — no opinion

    distribution = _build_hourly_distribution(observations)
    return distribution[hour]


def get_full_distribution(context: str) -> list:
    """
    Get the full 24-hour probability distribution for a context.

    Useful for debugging and visualization.

    Args:
        context: Context name

    Returns:
        List of 24 floats (probabilities for hours 0-23)
    """
    data = _load_temporal_data()
    ctx_data = data.get("contexts", {}).get(context, {})
    observations = ctx_data.get("observations", [])

    if not observations:
        return [1.0 / 24.0] * 24

    return _build_hourly_distribution(observations)


def record_observation(context: str, positive: bool = True, timestamp: Optional[str] = None):
    """
    Record a temporal observation for a context.

    Called when:
    - A context is inferred with high confidence and confirmed (positive=True)
    - A suggestion for a context is rejected (positive=False)

    Args:
        context: Context name (e.g., "eating", "cooking")
        positive: True if context was confirmed, False if rejected
        timestamp: ISO timestamp (defaults to now)
    """
    if timestamp is None:
        timestamp = datetime.now().isoformat()

    try:
        hour = datetime.fromisoformat(timestamp).hour
    except (ValueError, TypeError):
        hour = datetime.now().hour
        timestamp = datetime.now().isoformat()

    weight = POSITIVE_WEIGHT if positive else NEGATIVE_WEIGHT

    data = _load_temporal_data()
    contexts = data.setdefault("contexts", {})
    ctx_data = contexts.setdefault(context, {"observations": []})
    observations = ctx_data.setdefault("observations", [])

    observations.append({
        "timestamp": timestamp,
        "hour": hour,
        "weight": weight,
        "positive": positive
    })

    # Prune old observations if too many
    if len(observations) > MAX_OBSERVATIONS_PER_CONTEXT:
        # Keep the most recent ones
        observations.sort(key=lambda o: o.get("timestamp", ""), reverse=True)
        ctx_data["observations"] = observations[:MAX_OBSERVATIONS_PER_CONTEXT]

    _save_temporal_data(data)


def get_temporal_score(context: str, hour: Optional[int] = None) -> float:
    """
    Get a temporal score suitable for multiplying with context confidence.

    Unlike raw probability, this is scaled so that:
    - Peak hours return values > 1.0 (boost)
    - Off-peak hours return values < 1.0 (dampen)
    - No-data hours return 1.0 (neutral — don't interfere)

    The scaling uses: score = probability / uniform_prior
    So if a context is 3x more likely than average at this hour, score = 3.0

    Args:
        context: Context name
        hour: Hour (0-23), defaults to current

    Returns:
        Float multiplier. 1.0 = neutral, >1 = boost, <1 = dampen
    """
    probability = get_time_probability(context, hour)
    uniform = 1.0 / 24.0

    # Ratio vs uniform prior gives us a natural multiplier
    # Capped to prevent extreme values
    score = probability / uniform
    return min(score, 5.0)  # Cap at 5x boost


def get_stats() -> dict:
    """
    Get summary statistics for all temporal patterns.

    Returns dict with per-context stats for debugging/display.
    """
    data = _load_temporal_data()
    stats = {}

    for context, ctx_data in data.get("contexts", {}).items():
        observations = ctx_data.get("observations", [])
        if not observations:
            continue

        positive_count = sum(1 for o in observations if o.get("positive", True))
        negative_count = len(observations) - positive_count

        distribution = _build_hourly_distribution(observations)
        peak_hour = distribution.index(max(distribution))
        trough_hour = distribution.index(min(distribution))

        stats[context] = {
            "total_observations": len(observations),
            "positive": positive_count,
            "negative": negative_count,
            "peak_hour": peak_hour,
            "peak_probability": round(max(distribution), 4),
            "trough_hour": trough_hour,
            "trough_probability": round(min(distribution), 4),
        }

    return stats


# --- Bootstrap / Seeding ---

def seed_priors():
    """
    Seed temporal-patterns.json with reasonable priors based on the
    previously hardcoded time rules.

    Only seeds if the file doesn't exist or has no contexts.
    Priors use low weight so real observations override quickly.
    """
    data = _load_temporal_data()

    # Don't re-seed if already has real data
    if data.get("contexts"):
        existing_obs = sum(
            len(ctx.get("observations", []))
            for ctx in data["contexts"].values()
        )
        if existing_obs > 0:
            return  # Already has data, don't overwrite

    # Define priors based on the old hardcoded time rules
    # Format: context -> list of (hour, relative_weight) tuples
    # Higher weight = more likely at that hour
    priors = {
        "waking_up": [
            (6, 0.5), (7, 1.0), (8, 0.8), (9, 0.3)
        ],
        "morning_routine": [
            (6, 0.3), (7, 1.0), (8, 0.8), (9, 0.5), (10, 0.2)
        ],
        "cooking": [
            (7, 0.5), (8, 0.3),           # Breakfast prep
            (11, 0.3), (12, 0.5),          # Lunch prep
            (17, 0.5), (18, 1.0), (19, 0.7)  # Dinner prep (peak)
        ],
        "eating": [
            (7, 0.5), (8, 0.5),           # Breakfast
            (12, 0.7), (13, 0.5),          # Lunch
            (18, 0.5), (19, 1.0), (20, 0.7)  # Dinner (peak)
        ],
        "post_meal": [
            (8, 0.3), (9, 0.3),
            (13, 0.5), (14, 0.3),
            (19, 0.3), (20, 0.7), (21, 0.5)
        ],
        "working": [
            (9, 0.7), (10, 1.0), (11, 1.0), (12, 0.5),
            (13, 0.7), (14, 1.0), (15, 1.0), (16, 0.8), (17, 0.3)
        ],
        "winding_down": [
            (19, 0.3), (20, 0.7), (21, 1.0), (22, 0.8)
        ],
        "going_to_bed": [
            (21, 0.2), (22, 0.5), (23, 1.0), (0, 0.3)
        ],
        "sleeping": [
            (23, 0.5), (0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0),
            (4, 1.0), (5, 0.8), (6, 0.3)
        ],
        "away": [
            (9, 0.5), (10, 0.7), (11, 0.8), (12, 0.8),
            (13, 0.8), (14, 0.8), (15, 0.7), (16, 0.5)
        ],
        "arriving_home": [
            (17, 0.7), (18, 1.0), (19, 0.5)
        ],
        "leaving_home": [
            (7, 0.5), (8, 1.0), (9, 0.7)
        ],
    }

    # Use a fixed past timestamp for priors so they decay naturally
    # Set them as if observed "1 week ago" — gives them some starting weight
    # but real observations will outpace them quickly
    prior_timestamp = datetime(2026, 1, 21, 12, 0, 0).isoformat()  # ~1 week before system started

    data = _empty_data()

    for context, hour_weights in priors.items():
        observations = []
        for hour, rel_weight in hour_weights:
            # Scale by PRIOR_WEIGHT
            weight = PRIOR_WEIGHT * rel_weight

            # Create a synthetic observation at this hour
            synthetic_ts = datetime(2026, 1, 21, hour, 30, 0).isoformat()
            observations.append({
                "timestamp": synthetic_ts,
                "hour": hour,
                "weight": weight,
                "positive": True,
                "is_prior": True  # Marker so we can identify bootstrap data
            })

        data["contexts"][context] = {
            "observations": observations
        }

    _save_temporal_data(data)


# --- CLI ---

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: temporal_learner.py <command>")
        print("Commands:")
        print("  seed       - Seed priors (only if no data exists)")
        print("  stats      - Show learning statistics")
        print("  dist <ctx> - Show hourly distribution for a context")
        print("  prob <ctx> [hour] - Get probability for context at hour")
        print("  score <ctx> [hour] - Get temporal score (multiplier)")
        print("  record <ctx> <pos|neg> - Record an observation")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "seed":
        seed_priors()
        print("Seeded temporal priors.")
        stats = get_stats()
        for ctx, s in stats.items():
            print(f"  {ctx}: {s['total_observations']} obs, peak={s['peak_hour']}:00")

    elif cmd == "stats":
        stats = get_stats()
        if not stats:
            print("No temporal data yet. Run 'seed' first.")
        else:
            for ctx, s in sorted(stats.items()):
                print(f"{ctx}:")
                print(f"  observations: {s['total_observations']} ({s['positive']}+ / {s['negative']}-)")
                print(f"  peak: {s['peak_hour']}:00 ({s['peak_probability']:.3f})")
                print(f"  trough: {s['trough_hour']}:00 ({s['trough_probability']:.3f})")

    elif cmd == "dist":
        if len(sys.argv) < 3:
            print("Usage: temporal_learner.py dist <context>")
            sys.exit(1)
        context = sys.argv[2]
        dist = get_full_distribution(context)
        print(f"Hourly distribution for '{context}':")
        max_p = max(dist)
        for h, p in enumerate(dist):
            bar = "█" * int(p / max_p * 30) if max_p > 0 else ""
            print(f"  {h:2d}:00  {p:.4f}  {bar}")

    elif cmd == "prob":
        if len(sys.argv) < 3:
            print("Usage: temporal_learner.py prob <context> [hour]")
            sys.exit(1)
        context = sys.argv[2]
        hour = int(sys.argv[3]) if len(sys.argv) > 3 else None
        p = get_time_probability(context, hour)
        h = hour if hour is not None else datetime.now().hour
        print(f"P({context} | hour={h}) = {p:.4f}")

    elif cmd == "score":
        if len(sys.argv) < 3:
            print("Usage: temporal_learner.py score <context> [hour]")
            sys.exit(1)
        context = sys.argv[2]
        hour = int(sys.argv[3]) if len(sys.argv) > 3 else None
        s = get_temporal_score(context, hour)
        h = hour if hour is not None else datetime.now().hour
        uniform = 1.0 / 24.0
        raw_p = get_time_probability(context, h)
        print(f"Temporal score for '{context}' at {h}:00:")
        print(f"  probability: {raw_p:.4f}")
        print(f"  uniform:     {uniform:.4f}")
        print(f"  score:       {s:.2f}x {'(boost)' if s > 1 else '(dampen)' if s < 1 else '(neutral)'}")

    elif cmd == "record":
        if len(sys.argv) < 4:
            print("Usage: temporal_learner.py record <context> <pos|neg>")
            sys.exit(1)
        context = sys.argv[2]
        positive = sys.argv[3].lower() in ("pos", "positive", "yes", "true", "1")
        record_observation(context, positive=positive)
        print(f"Recorded {'positive' if positive else 'negative'} observation for '{context}' at {datetime.now().hour}:00")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
