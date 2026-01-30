#!/usr/bin/env python3
"""
Structured logging for Jarvis Mode.

Provides JSON-formatted logs with context for debugging and analysis.
"""

import json
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import uuid

# Log directory
from core.paths import LOG_DIR

LOG_FILE = LOG_DIR / "jarvis.log"

# Ensure log directory exists
LOG_DIR.mkdir(exist_ok=True)


class StructuredFormatter(logging.Formatter):
    """
    Custom formatter that outputs structured JSON logs.

    Each log entry includes:
    - timestamp (ISO 8601)
    - level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - component (e.g., "jarvis.snapshot", "jarvis.occupancy")
    - operation (e.g., "snapshot_taken", "decision_made")
    - message (human-readable)
    - metadata (arbitrary key-value pairs)
    - trace_id (optional, for tracking request flow)
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "component": record.name,
            "operation": getattr(record, 'operation', None),
            "message": record.getMessage(),
        }

        # Add any extra fields from the record
        extras = {}
        for key, value in record.__dict__.items():
            if key not in [
                'name', 'msg', 'args', 'created', 'filename', 'funcName',
                'levelname', 'levelno', 'lineno', 'module', 'msecs',
                'message', 'pathname', 'process', 'processName',
                'relativeCreated', 'thread', 'threadName', 'exc_info',
                'exc_text', 'stack_info', 'operation'
            ]:
                # Serialize complex objects
                try:
                    json.dumps(value)
                    extras[key] = value
                except (TypeError, ValueError):
                    extras[key] = str(value)

        if extras:
            log_entry.update(extras)

        # Add exception info if present
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class JarvisLogger:
    """
    Wrapper around Python logger with structured logging capabilities.

    Usage:
        logger = get_logger("jarvis.snapshot")
        logger.info("snapshot_taken", room="kitchen", path="/tmp/snap.jpg", duration_ms=2340)
        logger.error("snapshot_failed", room="kitchen", error="timeout", trace_id="abc123")
    """

    def __init__(self, name: str, trace_id: Optional[str] = None):
        """
        Initialize logger.

        Args:
            name: Logger name (usually module path, e.g. "jarvis.snapshot")
            trace_id: Optional trace ID for tracking request flow
        """
        self.logger = logging.getLogger(name)
        self.trace_id = trace_id or str(uuid.uuid4())[:8]

    def _log(self, level: int, operation: str, message: str = None, **kwargs):
        """
        Internal log method.

        Args:
            level: Logging level (logging.DEBUG, INFO, etc.)
            operation: Operation name (e.g., "snapshot_taken")
            message: Human-readable message (optional, defaults to operation)
            **kwargs: Additional metadata fields
        """
        if message is None:
            # Convert operation to readable message
            message = operation.replace('_', ' ').capitalize()

        # Add trace_id to kwargs
        kwargs['trace_id'] = self.trace_id

        # Extract exc_info if present (reserved parameter)
        exc_info = kwargs.pop('exc_info', False)

        # Create LogRecord with extra fields
        self.logger.log(
            level,
            message,
            exc_info=exc_info,
            extra={'operation': operation, **kwargs}
        )

    def debug(self, operation: str, message: str = None, **kwargs):
        """Log debug message."""
        self._log(logging.DEBUG, operation, message, **kwargs)

    def info(self, operation: str, message: str = None, **kwargs):
        """Log info message."""
        self._log(logging.INFO, operation, message, **kwargs)

    def warning(self, operation: str, message: str = None, **kwargs):
        """Log warning message."""
        self._log(logging.WARNING, operation, message, **kwargs)

    def error(self, operation: str, message: str = None, **kwargs):
        """Log error message."""
        self._log(logging.ERROR, operation, message, **kwargs)

    def critical(self, operation: str, message: str = None, **kwargs):
        """Log critical message."""
        self._log(logging.CRITICAL, operation, message, **kwargs)


# Global logger configuration
_configured = False


def setup_logging(log_level: str = "INFO", log_to_console: bool = False):
    """
    Configure logging globally.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_to_console: Also log to console (stdout) in addition to file
    """
    global _configured

    if _configured:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # File handler with rotation (10MB max, keep 7 files)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=7
    )
    file_handler.setFormatter(StructuredFormatter())
    root_logger.addHandler(file_handler)

    # Console handler (optional, for debugging)
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(StructuredFormatter())
        root_logger.addHandler(console_handler)

    _configured = True


def get_logger(name: str, trace_id: Optional[str] = None) -> JarvisLogger:
    """
    Get a logger instance.

    Args:
        name: Logger name (usually module path)
        trace_id: Optional trace ID for tracking request flow

    Returns:
        JarvisLogger instance
    """
    if not _configured:
        setup_logging()

    return JarvisLogger(name, trace_id=trace_id)


def with_trace_id(trace_id: str):
    """
    Context manager for setting trace ID for a block of operations.

    Usage:
        with with_trace_id("abc123"):
            logger = get_logger("jarvis.snapshot")
            logger.info("snapshot_taken", room="kitchen")
            # trace_id="abc123" automatically added
    """
    class TraceContext:
        def __enter__(self):
            self.logger = get_logger("jarvis", trace_id=trace_id)
            return self.logger

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    return TraceContext()


# Example usage
if __name__ == "__main__":
    # Configure logging
    setup_logging(log_level="DEBUG", log_to_console=True)

    # Create logger
    logger = get_logger("jarvis.test")

    # Log examples
    logger.info("system_started", version="2.0", components=["state", "logger", "config"])
    logger.debug("debug_info", key="value", count=42)
    logger.warning("high_latency", latency_ms=5000, threshold_ms=3000, target="snapshot")
    logger.error("operation_failed", error="timeout", room="kitchen", target="turn_off_lights")

    try:
        raise ValueError("Test exception")
    except Exception as e:
        logger.error("exception_caught", error_msg=str(e), target="test_operation", exc_info=True)

    print(f"\nLogs written to: {LOG_FILE}")
