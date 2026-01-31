#!/usr/bin/env python3
"""
Event Collector Service - Real-time HA state change logging for pattern learning.

Connects to Home Assistant websocket API and logs all state changes to SQLite
for later pattern analysis and prediction.
"""

import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    os.system(f"{sys.executable} -m pip install websockets")
    import websockets


class EventCollector:
    """
    Collects and stores Home Assistant state change events.

    Uses websocket subscription for real-time events.
    Stores to SQLite for efficient querying and pattern analysis.

    Now inventory-aware: loads capabilities.json to identify priority entities.
    """

    # Entity domains worth tracking for behavior patterns
    TRACKED_DOMAINS = [
        'light', 'switch', 'media_player', 'climate', 'cover',
        'binary_sensor', 'sensor', 'person', 'input_boolean',
        'scene', 'script', 'automation', 'button', 'vacuum'
    ]

    # Noisy sensors to exclude (update too frequently)
    EXCLUDED_PATTERNS = [
        '_temperature', '_humidity', '_battery', '_signal',
        '_linkquality', '_voltage', '_power_consumption',
        'cpu_', 'memory_', 'disk_'
    ]

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize event collector with database path."""
        if db_path is None:
            from core.paths import EVENTS_DB
            db_path = EVENTS_DB

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # HA connection details from environment or openclaw config
        self.ha_url = os.environ.get("HA_URL", "").replace("http://", "ws://").replace("https://", "wss://")
        self.ha_token = os.environ.get("HA_TOKEN", "")

        if not self.ha_url or not self.ha_token:
            self._load_from_openclaw_config()

        # Convert to websocket URL
        if self.ha_url and not self.ha_url.startswith("ws"):
            self.ha_url = self.ha_url.replace("http://", "ws://").replace("https://", "wss://")
        if self.ha_url and not self.ha_url.endswith("/api/websocket"):
            self.ha_url = self.ha_url.rstrip("/") + "/api/websocket"

        # Load inventory from capabilities.json
        self.priority_entities = self._load_capabilities()

        self._init_database()

    def _load_capabilities(self) -> set:
        """Load capabilities.json and extract all known entity IDs."""
        from core.paths import CAPABILITIES_FILE
        capabilities_path = CAPABILITIES_FILE
        priority = set()

        if not capabilities_path.exists():
            return priority

        try:
            with open(capabilities_path) as f:
                caps = json.load(f)

            # Extract entities from all capability sections
            def extract_entities(obj):
                """Recursively extract entity IDs from nested dicts/lists."""
                if isinstance(obj, str):
                    # Looks like an entity ID (domain.name format)
                    if '.' in obj and not obj.startswith('_') and not obj.startswith('http'):
                        priority.add(obj)
                elif isinstance(obj, dict):
                    for v in obj.values():
                        extract_entities(v)
                elif isinstance(obj, list):
                    for item in obj:
                        extract_entities(item)

            extract_entities(caps)
            print(f"Loaded {len(priority)} priority entities from capabilities.json")

        except Exception as e:
            print(f"Failed to load capabilities.json: {e}")

        return priority

    def _load_from_openclaw_config(self):
        """Load HA credentials from openclaw.json."""
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)
                env_vars = config.get("env", {}).get("vars", {})
                self.ha_url = env_vars.get("HA_URL", "")
                self.ha_token = env_vars.get("HA_TOKEN", "")
            except Exception as e:
                print(f"Failed to load openclaw config: {e}")

    def _init_database(self):
        """Initialize SQLite database with schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Main events table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                domain TEXT NOT NULL,
                old_state TEXT,
                new_state TEXT,
                attributes TEXT,
                context_user_id TEXT,
                hour INTEGER,
                day_of_week INTEGER,
                is_weekend INTEGER,
                is_priority INTEGER DEFAULT 0
            )
        ''')

        # Migration: add is_priority column if missing
        try:
            cursor.execute('ALTER TABLE events ADD COLUMN is_priority INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Indexes for efficient querying
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_entity ON events(entity_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_domain ON events(domain)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_hour ON events(hour)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_day ON events(day_of_week)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_priority ON events(is_priority)')

        # Patterns table (aggregated from events)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                pattern_key TEXT NOT NULL,
                pattern_data TEXT NOT NULL,
                occurrences INTEGER DEFAULT 1,
                last_seen TEXT,
                confidence REAL DEFAULT 0.0,
                UNIQUE(pattern_type, pattern_key)
            )
        ''')

        # Stats table for storage management
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        conn.commit()
        conn.close()

    def _should_track_entity(self, entity_id: str) -> bool:
        """Determine if entity should be tracked."""
        domain = entity_id.split('.')[0]

        # Check domain
        if domain not in self.TRACKED_DOMAINS:
            return False

        # Check exclusion patterns
        for pattern in self.EXCLUDED_PATTERNS:
            if pattern in entity_id.lower():
                return False

        return True

    def record_event(self, event_data: Dict[str, Any]):
        """Record a single state change event."""
        entity_id = event_data.get("entity_id", "")

        if not self._should_track_entity(entity_id):
            return

        now = datetime.now()
        domain = entity_id.split('.')[0]

        old_state = event_data.get("old_state", {})
        new_state = event_data.get("new_state", {})

        # Extract meaningful state values
        old_val = old_state.get("state") if old_state else None
        new_val = new_state.get("state") if new_state else None

        # Skip if state didn't actually change
        if old_val == new_val:
            return

        # Extract attributes we care about
        attrs = {}
        if new_state and "attributes" in new_state:
            for key in ["brightness", "color_temp", "media_title", "source", "volume_level"]:
                if key in new_state["attributes"]:
                    attrs[key] = new_state["attributes"][key]

        # Check if this is a priority entity from capabilities.json
        is_priority = 1 if entity_id in self.priority_entities else 0

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO events (
                timestamp, entity_id, domain, old_state, new_state,
                attributes, context_user_id, hour, day_of_week, is_weekend, is_priority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            now.isoformat(),
            entity_id,
            domain,
            old_val,
            new_val,
            json.dumps(attrs) if attrs else None,
            event_data.get("context", {}).get("user_id"),
            now.hour,
            now.weekday(),
            1 if now.weekday() >= 5 else 0,
            is_priority
        ))

        conn.commit()
        conn.close()

    async def connect_and_subscribe(self):
        """Connect to HA websocket and subscribe to state changes."""
        if not self.ha_url or not self.ha_token:
            print("Missing HA_URL or HA_TOKEN")
            return

        print(f"Connecting to {self.ha_url}...")

        async with websockets.connect(self.ha_url) as ws:
            # Wait for auth required message
            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth_required":
                print(f"Unexpected message: {msg}")
                return

            # Authenticate
            await ws.send(json.dumps({
                "type": "auth",
                "access_token": self.ha_token
            }))

            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth_ok":
                print(f"Auth failed: {msg}")
                return

            print("Authenticated with Home Assistant")

            # Subscribe to state changes
            await ws.send(json.dumps({
                "id": 1,
                "type": "subscribe_events",
                "event_type": "state_changed"
            }))

            msg = json.loads(await ws.recv())
            if msg.get("type") != "result" or not msg.get("success"):
                print(f"Subscribe failed: {msg}")
                return

            print("Subscribed to state_changed events. Collecting data...")

            # Process events forever
            event_count = 0
            while True:
                try:
                    msg = json.loads(await ws.recv())

                    if msg.get("type") == "event":
                        event_data = msg.get("event", {}).get("data", {})
                        self.record_event(event_data)
                        event_count += 1

                        if event_count % 100 == 0:
                            print(f"Recorded {event_count} events")

                except Exception as e:
                    print(f"Error processing event: {e}")
                    continue

    def get_stats(self) -> Dict[str, Any]:
        """Get collector statistics."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM events")
        total_events = cursor.fetchone()[0]

        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events")
        row = cursor.fetchone()
        first_event = row[0]
        last_event = row[1]

        cursor.execute("SELECT domain, COUNT(*) FROM events GROUP BY domain ORDER BY COUNT(*) DESC LIMIT 10")
        top_domains = cursor.fetchall()

        cursor.execute("SELECT entity_id, COUNT(*) FROM events GROUP BY entity_id ORDER BY COUNT(*) DESC LIMIT 10")
        top_entities = cursor.fetchall()

        # Database file size
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

        conn.close()

        return {
            "total_events": total_events,
            "first_event": first_event,
            "last_event": last_event,
            "top_domains": dict(top_domains),
            "top_entities": dict(top_entities),
            "database_size_mb": round(db_size / (1024 * 1024), 2)
        }

    def prune_old_events(self, days: int = 90):
        """Remove events older than specified days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM events WHERE timestamp < ?", (cutoff,))
        to_delete = cursor.fetchone()[0]

        cursor.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
        conn.commit()

        # Vacuum to reclaim space
        cursor.execute("VACUUM")
        conn.close()

        return to_delete

    def get_recent_events(self, hours: int = 24, entity_filter: Optional[str] = None) -> List[Dict]:
        """Get recent events for analysis."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if entity_filter:
            cursor.execute(
                "SELECT * FROM events WHERE timestamp > ? AND entity_id LIKE ? ORDER BY timestamp DESC",
                (cutoff, f"%{entity_filter}%")
            )
        else:
            cursor.execute(
                "SELECT * FROM events WHERE timestamp > ? ORDER BY timestamp DESC",
                (cutoff,)
            )

        columns = [desc[0] for desc in cursor.description]
        events = [dict(zip(columns, row)) for row in cursor.fetchall()]

        conn.close()
        return events


def run_collector():
    """Run the event collector as a standalone service."""
    collector = EventCollector()

    print("Event Collector starting...")
    print(f"Database: {collector.db_path}")
    print(f"Tracking domains: {collector.TRACKED_DOMAINS}")

    try:
        asyncio.run(collector.connect_and_subscribe())
    except KeyboardInterrupt:
        print("\nStopping collector...")
        stats = collector.get_stats()
        print(f"Final stats: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HA Event Collector")
    parser.add_argument("--run", action="store_true", help="Run the collector service")
    parser.add_argument("--stats", action="store_true", help="Show collector stats")
    parser.add_argument("--prune", type=int, help="Prune events older than N days")
    parser.add_argument("--recent", type=int, help="Show events from last N hours")

    args = parser.parse_args()

    collector = EventCollector()

    if args.stats:
        stats = collector.get_stats()
        print(json.dumps(stats, indent=2))
    elif args.prune:
        deleted = collector.prune_old_events(args.prune)
        print(f"Deleted {deleted} events older than {args.prune} days")
    elif args.recent:
        events = collector.get_recent_events(args.recent)
        print(f"Found {len(events)} events in last {args.recent} hours")
        for e in events[:20]:
            print(f"  {e['timestamp'][:19]} {e['entity_id']}: {e['old_state']} -> {e['new_state']}")
    elif args.run:
        run_collector()
    else:
        parser.print_help()
