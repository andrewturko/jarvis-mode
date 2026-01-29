#!/usr/bin/env python3
"""
Daily activity log for Jarvis.

Records what Jarvis said/did so the main clawdbot conversation
can reference it when the user asks "what did Jarvis say?"

Writes one JSON file per day: logs/jarvis-activity-YYYY-MM-DD.json
Auto-cleans files older than keep_days.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

LOGS_DIR = Path(__file__).parent.parent.parent / "logs"


class ActivityLog:
    """Daily activity log for Jarvis messages and decisions."""

    def __init__(self, logs_dir: Path = LOGS_DIR):
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _today_file(self) -> Path:
        return self.logs_dir / f"jarvis-activity-{datetime.now().strftime('%Y-%m-%d')}.json"

    def _read(self, path: Path) -> list:
        if not path.exists():
            return []
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _write(self, path: Path, entries: list):
        with open(path, "w") as f:
            json.dump(entries, f, indent=2)

    def log_message(self, room: str, message: str,
                    action: Optional[str] = None,
                    context: Optional[str] = None):
        """Record a message Jarvis sent to the user."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "message",
            "room": room,
            "message": message,
        }
        if action:
            entry["action"] = action
        if context:
            entry["context"] = context

        path = self._today_file()
        entries = self._read(path)
        entries.append(entry)
        self._write(path, entries)

    def log_silence(self, room: str, reason: str,
                    context: Optional[str] = None):
        """Record when Jarvis chose to stay silent."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "silence",
            "room": room,
            "reason": reason,
        }
        if context:
            entry["context"] = context

        path = self._today_file()
        entries = self._read(path)
        entries.append(entry)
        self._write(path, entries)

    def get_today(self) -> list:
        """Get today's activity log entries."""
        return self._read(self._today_file())

    def cleanup(self, keep_days: int = 1):
        """Delete activity log files older than keep_days."""
        cutoff = datetime.now() - timedelta(days=keep_days)
        removed = 0
        for f in self.logs_dir.glob("jarvis-activity-*.json"):
            try:
                date_str = f.stem.replace("jarvis-activity-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    f.unlink()
                    removed += 1
            except (ValueError, OSError):
                continue
        return removed
