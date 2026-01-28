#!/usr/bin/env python3
"""
Pattern Analyzer - Extracts behavioral patterns from collected events.

Analyzes event sequences to find:
- Time-based patterns (what happens at specific times)
- Sequence patterns (action A is usually followed by action B)
- Context patterns (when in room X, action Y usually follows)
"""

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional


class PatternAnalyzer:
    """
    Analyzes collected events to extract predictive patterns.
    """

    # Minimum occurrences for a pattern to be considered
    MIN_PATTERN_OCCURRENCES = 3

    # Time window for sequence detection (minutes)
    SEQUENCE_WINDOW_MINUTES = 10

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize analyzer with database path."""
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "events.db"
        self.db_path = Path(db_path)

    def _get_conn(self):
        """Get database connection."""
        return sqlite3.connect(self.db_path)

    def analyze_time_patterns(self) -> Dict[str, Any]:
        """
        Find patterns based on time of day and day of week.

        Returns patterns like:
        - "light.living_room turns on at 6pm-7pm on weekdays 85% of the time"
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Group state changes by entity + hour + weekday/weekend
        cursor.execute('''
            SELECT
                entity_id,
                new_state,
                hour,
                is_weekend,
                COUNT(*) as occurrences
            FROM events
            WHERE new_state IS NOT NULL
            GROUP BY entity_id, new_state, hour, is_weekend
            HAVING COUNT(*) >= ?
            ORDER BY occurrences DESC
        ''', (self.MIN_PATTERN_OCCURRENCES,))

        patterns = []
        for row in cursor.fetchall():
            entity_id, state, hour, is_weekend, count = row

            # Calculate what percentage of the time this happens
            cursor.execute('''
                SELECT COUNT(*) FROM events
                WHERE entity_id = ? AND hour = ? AND is_weekend = ?
            ''', (entity_id, hour, is_weekend))
            total_at_time = cursor.fetchone()[0]

            confidence = count / total_at_time if total_at_time > 0 else 0

            if confidence >= 0.3:  # At least 30% of the time
                patterns.append({
                    "type": "time_based",
                    "entity_id": entity_id,
                    "action": state,
                    "hour": hour,
                    "is_weekend": bool(is_weekend),
                    "occurrences": count,
                    "confidence": round(confidence, 2),
                    "description": f"{entity_id} -> {state} at {hour}:00 ({'weekend' if is_weekend else 'weekday'})"
                })

        conn.close()
        return {"time_patterns": patterns[:50]}  # Top 50

    def analyze_sequence_patterns(self) -> Dict[str, Any]:
        """
        Find patterns where one action tends to follow another.

        Returns patterns like:
        - "When light.kitchen turns on, media_player.kitchen often starts playing within 5 min"
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Get all events ordered by time
        cursor.execute('''
            SELECT id, timestamp, entity_id, new_state
            FROM events
            WHERE new_state IS NOT NULL
            ORDER BY timestamp
        ''')

        events = cursor.fetchall()
        conn.close()

        # Track sequences: (entity1, state1) -> (entity2, state2)
        sequences = defaultdict(int)
        total_triggers = defaultdict(int)

        for i, event in enumerate(events):
            _, timestamp, entity1, state1 = event
            event_time = datetime.fromisoformat(timestamp)
            trigger_key = (entity1, state1)
            total_triggers[trigger_key] += 1

            # Look for follow-up events within time window
            for j in range(i + 1, min(i + 50, len(events))):
                _, ts2, entity2, state2 = events[j]
                event2_time = datetime.fromisoformat(ts2)

                # Stop if outside time window
                if (event2_time - event_time).total_seconds() > self.SEQUENCE_WINDOW_MINUTES * 60:
                    break

                # Skip same entity
                if entity1 == entity2:
                    continue

                sequence_key = (entity1, state1, entity2, state2)
                sequences[sequence_key] += 1

        # Convert to patterns with confidence
        patterns = []
        for (e1, s1, e2, s2), count in sequences.items():
            if count < self.MIN_PATTERN_OCCURRENCES:
                continue

            trigger_count = total_triggers[(e1, s1)]
            confidence = count / trigger_count if trigger_count > 0 else 0

            if confidence >= 0.2:  # At least 20% of the time
                patterns.append({
                    "type": "sequence",
                    "trigger_entity": e1,
                    "trigger_state": s1,
                    "follow_entity": e2,
                    "follow_state": s2,
                    "occurrences": count,
                    "confidence": round(confidence, 2),
                    "description": f"When {e1} -> {s1}, then {e2} -> {s2} ({count} times, {confidence:.0%})"
                })

        # Sort by confidence * occurrences (most reliable patterns first)
        patterns.sort(key=lambda p: p["confidence"] * p["occurrences"], reverse=True)

        return {"sequence_patterns": patterns[:50]}

    def analyze_room_patterns(self, room_entity_map: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        """
        Find patterns based on room occupancy.

        Args:
            room_entity_map: Map of room names to entity_ids in that room
        """
        if room_entity_map is None:
            # Default mapping based on common naming
            room_entity_map = {
                "kitchen": ["kitchen", "counter"],
                "living_room": ["living", "living_room"],
                "dining": ["dining"],
                "bedroom": ["bedroom", "bed"],
            }

        conn = self._get_conn()
        cursor = conn.cursor()

        patterns = []

        for room, patterns_list in room_entity_map.items():
            # Find motion/person sensor for this room
            pattern_clause = " OR ".join([f"entity_id LIKE '%{p}%'" for p in patterns_list])

            cursor.execute(f'''
                SELECT entity_id, new_state, hour, COUNT(*) as count
                FROM events
                WHERE domain IN ('light', 'switch', 'media_player')
                AND ({pattern_clause})
                AND new_state = 'on'
                GROUP BY entity_id, new_state, hour
                HAVING COUNT(*) >= ?
            ''', (self.MIN_PATTERN_OCCURRENCES,))

            for row in cursor.fetchall():
                entity_id, state, hour, count = row
                patterns.append({
                    "type": "room_based",
                    "room": room,
                    "entity_id": entity_id,
                    "action": state,
                    "hour": hour,
                    "occurrences": count,
                    "description": f"In {room}, {entity_id} often turns {state} at {hour}:00"
                })

        conn.close()
        return {"room_patterns": patterns}

    def get_predictions(self, current_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get predictions based on current context.

        Args:
            current_context: Dict with keys like 'hour', 'day_of_week', 'is_weekend',
                           'occupied_rooms', 'recent_actions'

        Returns:
            List of predicted actions with confidence
        """
        hour = current_context.get("hour", datetime.now().hour)
        is_weekend = current_context.get("is_weekend", datetime.now().weekday() >= 5)
        recent_actions = current_context.get("recent_actions", [])

        conn = self._get_conn()
        cursor = conn.cursor()

        predictions = []

        # Time-based predictions
        cursor.execute('''
            SELECT entity_id, new_state, COUNT(*) as count
            FROM events
            WHERE hour = ? AND is_weekend = ?
            AND new_state IS NOT NULL
            GROUP BY entity_id, new_state
            HAVING COUNT(*) >= ?
            ORDER BY count DESC
            LIMIT 10
        ''', (hour, 1 if is_weekend else 0, self.MIN_PATTERN_OCCURRENCES))

        for row in cursor.fetchall():
            entity_id, state, count = row

            # Get total events at this time
            cursor.execute('''
                SELECT COUNT(DISTINCT timestamp) FROM events
                WHERE hour = ? AND is_weekend = ?
            ''', (hour, 1 if is_weekend else 0))
            total = cursor.fetchone()[0]

            confidence = count / total if total > 0 else 0

            if confidence >= 0.1:
                predictions.append({
                    "source": "time_pattern",
                    "entity_id": entity_id,
                    "predicted_state": state,
                    "confidence": round(confidence, 2),
                    "reason": f"Usually happens around {hour}:00"
                })

        # Sequence-based predictions (if we have recent actions)
        for action in recent_actions[-5:]:  # Last 5 actions
            entity_id = action.get("entity_id")
            state = action.get("state")

            if not entity_id or not state:
                continue

            cursor.execute('''
                SELECT e2.entity_id, e2.new_state, COUNT(*) as count
                FROM events e1
                JOIN events e2 ON e2.timestamp > e1.timestamp
                    AND datetime(e2.timestamp) <= datetime(e1.timestamp, '+10 minutes')
                WHERE e1.entity_id = ? AND e1.new_state = ?
                AND e2.entity_id != e1.entity_id
                GROUP BY e2.entity_id, e2.new_state
                HAVING COUNT(*) >= ?
                ORDER BY count DESC
                LIMIT 5
            ''', (entity_id, state, self.MIN_PATTERN_OCCURRENCES))

            for row in cursor.fetchall():
                follow_entity, follow_state, count = row

                # Get total times trigger happened
                cursor.execute('''
                    SELECT COUNT(*) FROM events
                    WHERE entity_id = ? AND new_state = ?
                ''', (entity_id, state))
                total = cursor.fetchone()[0]

                confidence = count / total if total > 0 else 0

                if confidence >= 0.15:
                    predictions.append({
                        "source": "sequence_pattern",
                        "entity_id": follow_entity,
                        "predicted_state": follow_state,
                        "confidence": round(confidence, 2),
                        "reason": f"Usually follows {entity_id} -> {state}"
                    })

        conn.close()

        # Dedupe and sort by confidence
        seen = set()
        unique_predictions = []
        for p in sorted(predictions, key=lambda x: x["confidence"], reverse=True):
            key = (p["entity_id"], p["predicted_state"])
            if key not in seen:
                seen.add(key)
                unique_predictions.append(p)

        return unique_predictions[:10]

    def save_patterns_to_db(self):
        """Analyze and save patterns to the patterns table."""
        time_patterns = self.analyze_time_patterns()
        sequence_patterns = self.analyze_sequence_patterns()

        conn = self._get_conn()
        cursor = conn.cursor()

        all_patterns = (
            time_patterns.get("time_patterns", []) +
            sequence_patterns.get("sequence_patterns", [])
        )

        for p in all_patterns:
            pattern_key = f"{p.get('entity_id', '')}|{p.get('action', '')}|{p.get('hour', '')}|{p.get('trigger_entity', '')}"

            cursor.execute('''
                INSERT INTO patterns (pattern_type, pattern_key, pattern_data, occurrences, last_seen, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pattern_type, pattern_key) DO UPDATE SET
                    pattern_data = excluded.pattern_data,
                    occurrences = excluded.occurrences,
                    last_seen = excluded.last_seen,
                    confidence = excluded.confidence
            ''', (
                p["type"],
                pattern_key,
                json.dumps(p),
                p.get("occurrences", 1),
                datetime.now().isoformat(),
                p.get("confidence", 0)
            ))

        conn.commit()
        conn.close()

        return len(all_patterns)

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of learned patterns."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM events")
        total_events = cursor.fetchone()[0]

        cursor.execute("SELECT pattern_type, COUNT(*) FROM patterns GROUP BY pattern_type")
        patterns_by_type = dict(cursor.fetchall())

        cursor.execute('''
            SELECT pattern_data FROM patterns
            WHERE confidence >= 0.5
            ORDER BY confidence DESC
            LIMIT 5
        ''')
        top_patterns = [json.loads(row[0]) for row in cursor.fetchall()]

        conn.close()

        return {
            "total_events": total_events,
            "patterns_by_type": patterns_by_type,
            "high_confidence_patterns": top_patterns
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pattern Analyzer")
    parser.add_argument("--time", action="store_true", help="Analyze time patterns")
    parser.add_argument("--sequence", action="store_true", help="Analyze sequence patterns")
    parser.add_argument("--predict", action="store_true", help="Get predictions for current time")
    parser.add_argument("--save", action="store_true", help="Save patterns to database")
    parser.add_argument("--summary", action="store_true", help="Show pattern summary")

    args = parser.parse_args()

    analyzer = PatternAnalyzer()

    if args.time:
        patterns = analyzer.analyze_time_patterns()
        print(json.dumps(patterns, indent=2))
    elif args.sequence:
        patterns = analyzer.analyze_sequence_patterns()
        print(json.dumps(patterns, indent=2))
    elif args.predict:
        predictions = analyzer.get_predictions({})
        print("Predictions for current context:")
        for p in predictions:
            print(f"  {p['entity_id']} -> {p['predicted_state']} ({p['confidence']:.0%}) - {p['reason']}")
    elif args.save:
        count = analyzer.save_patterns_to_db()
        print(f"Saved {count} patterns")
    elif args.summary:
        summary = analyzer.get_summary()
        print(json.dumps(summary, indent=2))
    else:
        parser.print_help()
