"""File I/O utilities for the intelligence package."""

import json

from core.logger import get_logger
from core.paths import (
    LIFE_MODEL_FILE, CAPABILITIES_FILE, PATTERNS_FILE,
    SUGGESTION_CATALOG_FILE, STATE_FILE,
)

logger = get_logger("jarvis.intelligence")


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.warning("load_json_failed", path=str(path), error=str(e))
        return {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_life_model():
    return load_json(LIFE_MODEL_FILE)


def get_capabilities():
    return load_json(CAPABILITIES_FILE)


def get_patterns():
    return load_json(PATTERNS_FILE)


def save_patterns(patterns):
    save_json(PATTERNS_FILE, patterns)
