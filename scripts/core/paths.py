"""Centralized path constants for Jarvis Mode.

Single source of truth for all file and directory locations.
Every module should import paths from here instead of computing its own.
"""

from pathlib import Path

# Project root
SKILL_DIR = Path(__file__).resolve().parent.parent.parent  # jarvis-mode/

# --- Configuration files (checked into git via examples) ---
CONFIG_DIR = SKILL_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "config.json"
LIFE_MODEL_FILE = CONFIG_DIR / "life-model.json"
SUGGESTION_CATALOG_FILE = CONFIG_DIR / "suggestion-catalog.json"
HOOKS_FILE = CONFIG_DIR / "hooks.json"
CAPABILITIES_FILE = CONFIG_DIR / "capabilities.json"
VOICE_CONFIG_FILE = CONFIG_DIR / "voice-config.json"

# --- Runtime data files (gitignored) ---
DATA_DIR = SKILL_DIR / "data"
STATE_FILE = DATA_DIR / "state.json"
PATTERNS_FILE = DATA_DIR / "patterns.json"
PREFERENCES_FILE = DATA_DIR / "preferences.json"
METRICS_FILE = DATA_DIR / "metrics.json"
TEMPORAL_FILE = DATA_DIR / "temporal-patterns.json"
TRIGGERS_FILE = DATA_DIR / "triggers.json"
INVENTORY_FILE = DATA_DIR / "home-inventory.json"
EVENTS_DB = DATA_DIR / "events.db"
GENERATED_SUGGESTIONS_FILE = DATA_DIR / "generated-suggestions.json"
EXTERNAL_CONTEXT_FILE = DATA_DIR / "external_context.json"

# --- Content curation ---
INTEREST_PROFILE_FILE = CONFIG_DIR / "interest-profile.json"
CONTENT_HISTORY_FILE = DATA_DIR / "content-history.json"

# --- Directories ---
SNAPSHOT_DIR = DATA_DIR / "snapshots"
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
UI_DIR = SKILL_DIR / "ui"
TEMPLATES_DIR = SKILL_DIR / "templates"
