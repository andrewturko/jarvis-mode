#!/usr/bin/env python3
"""
Metrics tracking for Jarvis Mode.

Tracks decisions, suggestions, context inference, costs, and performance.
Provides aggregated statistics for monitoring and optimization.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict

from core.logger import get_logger

logger = get_logger("jarvis.metrics")

# Metrics file location
SKILL_DIR = Path(__file__).parent.parent.parent
METRICS_FILE = SKILL_DIR / "metrics.json"


class MetricsCollector:
    """
    Collects and aggregates metrics for Jarvis Mode.

    Tracks:
    - Decisions: total, spoke, silent, silence_rate
    - Suggestions: generated, offered, accepted, acceptance_rate
    - Context inference: total, high_confidence, avg_confidence
    - Costs: vision_api_calls, total_tokens, estimated_usd
    - Performance: avg_snapshot_ms, avg_inference_ms
    """

    def __init__(self, metrics_file: Path = METRICS_FILE):
        """Initialize metrics collector."""
        self.metrics_file = metrics_file
        self._ensure_metrics_file()

    def _ensure_metrics_file(self):
        """Ensure metrics file exists with proper structure."""
        if not self.metrics_file.exists():
            initial_metrics = {
                "schema_version": 1,
                "started_at": datetime.utcnow().isoformat() + "Z",
                "decisions": {
                    "total": 0,
                    "spoke": 0,
                    "silent": 0,
                    "silence_rate": 0.0
                },
                "suggestions": {
                    "generated": 0,
                    "offered": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "acceptance_rate": 0.0
                },
                "context": {
                    "total_inferences": 0,
                    "high_confidence": 0,
                    "avg_confidence": 0.0,
                    "confidence_sum": 0.0
                },
                "costs": {
                    "vision_api_calls": 0,
                    "total_vision_tokens": 0,
                    "total_output_tokens": 0,
                    "estimated_usd": 0.0
                },
                "performance": {
                    "snapshot_count": 0,
                    "snapshot_duration_ms_sum": 0,
                    "avg_snapshot_ms": 0.0,
                    "inference_count": 0,
                    "inference_duration_ms_sum": 0,
                    "avg_inference_ms": 0.0
                },
                "errors": {
                    "ha_errors": 0,
                    "snapshot_errors": 0,
                    "vision_errors": 0,
                    "state_errors": 0
                },
                "recent_events": []
            }
            self._save_metrics(initial_metrics)
            logger.info("metrics_file_created", path=str(self.metrics_file))

    def _load_metrics(self) -> Dict:
        """Load metrics from file."""
        try:
            with open(self.metrics_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error("metrics_load_failed", error=str(e), exc_info=True)
            return {}

    def _save_metrics(self, metrics: Dict):
        """Save metrics to file atomically."""
        try:
            # Write to temp file first
            temp_file = self.metrics_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(metrics, f, indent=2)

            # Atomic rename
            temp_file.replace(self.metrics_file)

            logger.debug("metrics_saved", path=str(self.metrics_file))
        except Exception as e:
            logger.error("metrics_save_failed", error=str(e), exc_info=True)

    def record_decision(
        self,
        room: str,
        decision: str,  # "spoke" or "silent"
        reason: str,
        context_confidence: Optional[float] = None,
        suggestions_count: int = 0
    ):
        """
        Record a decision (spoke or stayed silent).

        Args:
            room: Room name
            decision: "spoke" or "silent"
            reason: Reason for the decision
            context_confidence: Confidence of context inference (0-1)
            suggestions_count: Number of suggestions generated
        """
        metrics = self._load_metrics()

        # Update decision counters
        metrics["decisions"]["total"] += 1
        if decision == "spoke":
            metrics["decisions"]["spoke"] += 1
        elif decision == "silent":
            metrics["decisions"]["silent"] += 1

        # Calculate silence rate
        total = metrics["decisions"]["total"]
        silent = metrics["decisions"]["silent"]
        metrics["decisions"]["silence_rate"] = round(silent / total if total > 0 else 0, 3)

        # Add recent event
        event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "type": "decision",
            "room": room,
            "decision": decision,
            "reason": reason,
            "context_confidence": context_confidence,
            "suggestions_count": suggestions_count
        }
        metrics.setdefault("recent_events", []).append(event)

        # Keep only last 100 events
        metrics["recent_events"] = metrics["recent_events"][-100:]

        self._save_metrics(metrics)

        logger.info(
            "decision_recorded",
            room=room,
            decision=decision,
            reason=reason,
            silence_rate=metrics["decisions"]["silence_rate"]
        )

    def record_context_inference(
        self,
        room: str,
        context: str,
        confidence: float
    ):
        """
        Record a context inference.

        Args:
            room: Room name
            context: Inferred context (e.g., "cooking", "working")
            confidence: Confidence score (0-1)
        """
        metrics = self._load_metrics()

        # Update context counters
        metrics["context"]["total_inferences"] += 1

        if confidence >= 0.7:
            metrics["context"]["high_confidence"] += 1

        # Update average confidence
        metrics["context"]["confidence_sum"] += confidence
        total = metrics["context"]["total_inferences"]
        metrics["context"]["avg_confidence"] = round(
            metrics["context"]["confidence_sum"] / total if total > 0 else 0,
            3
        )

        self._save_metrics(metrics)

        logger.debug(
            "context_inferred",
            room=room,
            context=context,
            confidence=confidence
        )

    def record_suggestions(
        self,
        room: str,
        generated_count: int,
        offered_count: int = 0
    ):
        """
        Record suggestion generation.

        Args:
            room: Room name
            generated_count: Number of suggestions generated
            offered_count: Number of suggestions actually offered to user
        """
        metrics = self._load_metrics()

        metrics["suggestions"]["generated"] += generated_count
        metrics["suggestions"]["offered"] += offered_count

        self._save_metrics(metrics)

        logger.debug(
            "suggestions_recorded",
            room=room,
            generated=generated_count,
            offered=offered_count
        )

    def record_suggestion_feedback(
        self,
        suggestion_id: str,
        accepted: bool
    ):
        """
        Record user feedback on a suggestion.

        Args:
            suggestion_id: Unique suggestion identifier
            accepted: True if user accepted, False if rejected
        """
        metrics = self._load_metrics()

        if accepted:
            metrics["suggestions"]["accepted"] += 1
        else:
            metrics["suggestions"]["rejected"] += 1

        # Calculate acceptance rate
        total_feedback = metrics["suggestions"]["accepted"] + metrics["suggestions"]["rejected"]
        accepted_count = metrics["suggestions"]["accepted"]
        metrics["suggestions"]["acceptance_rate"] = round(
            accepted_count / total_feedback if total_feedback > 0 else 0,
            3
        )

        self._save_metrics(metrics)

        logger.info(
            "suggestion_feedback",
            suggestion_id=suggestion_id,
            accepted=accepted,
            acceptance_rate=metrics["suggestions"]["acceptance_rate"]
        )

    def record_vision_call(
        self,
        room: str,
        vision_tokens: int,
        output_tokens: int,
        duration_ms: int
    ):
        """
        Record a vision API call and associated costs.

        Args:
            room: Room name
            vision_tokens: Number of vision tokens used
            output_tokens: Number of output tokens generated
            duration_ms: API call duration in milliseconds
        """
        metrics = self._load_metrics()

        # Update cost counters
        metrics["costs"]["vision_api_calls"] += 1
        metrics["costs"]["total_vision_tokens"] += vision_tokens
        metrics["costs"]["total_output_tokens"] += output_tokens

        # Estimate cost (Claude 3.5 Sonnet pricing as of 2025)
        # Vision tokens: ~$3 per MTok input
        # Output tokens: ~$15 per MTok output
        vision_cost = (vision_tokens / 1_000_000) * 3.0
        output_cost = (output_tokens / 1_000_000) * 15.0
        metrics["costs"]["estimated_usd"] = round(
            metrics["costs"]["estimated_usd"] + vision_cost + output_cost,
            4
        )

        # Update inference performance
        metrics["performance"]["inference_count"] += 1
        metrics["performance"]["inference_duration_ms_sum"] += duration_ms
        total = metrics["performance"]["inference_count"]
        metrics["performance"]["avg_inference_ms"] = round(
            metrics["performance"]["inference_duration_ms_sum"] / total if total > 0 else 0,
            1
        )

        self._save_metrics(metrics)

        logger.info(
            "vision_call_recorded",
            room=room,
            vision_tokens=vision_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            cost_usd=round(vision_cost + output_cost, 4)
        )

    def record_snapshot(
        self,
        room: str,
        duration_ms: int,
        success: bool = True
    ):
        """
        Record a camera snapshot operation.

        Args:
            room: Room name
            duration_ms: Snapshot duration in milliseconds
            success: Whether snapshot succeeded
        """
        metrics = self._load_metrics()

        if success:
            metrics["performance"]["snapshot_count"] += 1
            metrics["performance"]["snapshot_duration_ms_sum"] += duration_ms
            total = metrics["performance"]["snapshot_count"]
            metrics["performance"]["avg_snapshot_ms"] = round(
                metrics["performance"]["snapshot_duration_ms_sum"] / total if total > 0 else 0,
                1
            )
        else:
            metrics["errors"]["snapshot_errors"] += 1

        self._save_metrics(metrics)

        logger.debug(
            "snapshot_recorded",
            room=room,
            duration_ms=duration_ms,
            success=success
        )

    def record_error(
        self,
        error_type: str,  # "ha", "snapshot", "vision", "state"
        room: Optional[str] = None,
        details: Optional[str] = None
    ):
        """
        Record an error occurrence.

        Args:
            error_type: Type of error (ha, snapshot, vision, state)
            room: Room where error occurred (optional)
            details: Error details (optional)
        """
        metrics = self._load_metrics()

        error_key = f"{error_type}_errors"
        if error_key in metrics["errors"]:
            metrics["errors"][error_key] += 1

        # Add to recent events
        event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "type": "error",
            "error_type": error_type,
            "room": room,
            "details": details
        }
        metrics.setdefault("recent_events", []).append(event)
        metrics["recent_events"] = metrics["recent_events"][-100:]

        self._save_metrics(metrics)

        logger.warning(
            "error_recorded",
            error_type=error_type,
            room=room,
            details=details
        )

    def get_metrics(self) -> Dict:
        """
        Get current metrics snapshot.

        Returns:
            Dict with all current metrics
        """
        return self._load_metrics()

    def get_summary(self) -> Dict:
        """
        Get summarized metrics for dashboard display.

        Returns:
            Dict with key metrics and trends
        """
        metrics = self._load_metrics()

        return {
            "decisions": {
                "total": metrics["decisions"]["total"],
                "spoke": metrics["decisions"]["spoke"],
                "silent": metrics["decisions"]["silent"],
                "silence_rate": f"{metrics['decisions']['silence_rate'] * 100:.1f}%"
            },
            "suggestions": {
                "total_offered": metrics["suggestions"]["offered"],
                "accepted": metrics["suggestions"]["accepted"],
                "acceptance_rate": f"{metrics['suggestions']['acceptance_rate'] * 100:.1f}%"
            },
            "context": {
                "total_inferences": metrics["context"]["total_inferences"],
                "high_confidence_rate": f"{(metrics['context']['high_confidence'] / max(metrics['context']['total_inferences'], 1)) * 100:.1f}%",
                "avg_confidence": f"{metrics['context']['avg_confidence']:.2f}"
            },
            "costs": {
                "vision_api_calls": metrics["costs"]["vision_api_calls"],
                "total_tokens": metrics["costs"]["total_vision_tokens"] + metrics["costs"]["total_output_tokens"],
                "estimated_usd": f"${metrics['costs']['estimated_usd']:.2f}"
            },
            "performance": {
                "avg_snapshot_ms": f"{metrics['performance']['avg_snapshot_ms']:.0f}ms",
                "avg_inference_ms": f"{metrics['performance']['avg_inference_ms']:.0f}ms"
            },
            "errors": {
                "total": sum(metrics["errors"].values()),
                "by_type": metrics["errors"]
            }
        }

    def reset_metrics(self):
        """Reset all metrics (useful for testing or fresh start)."""
        if self.metrics_file.exists():
            # Backup current metrics
            backup_file = self.metrics_file.with_suffix(
                f'.backup.{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json'
            )
            self.metrics_file.rename(backup_file)
            logger.info("metrics_backed_up", backup=str(backup_file))

        # Create fresh metrics file
        self._ensure_metrics_file()
        logger.info("metrics_reset")


# Global metrics collector instance
_metrics = None


def get_metrics_collector() -> MetricsCollector:
    """Get global metrics collector instance."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


# Example usage
if __name__ == "__main__":
    metrics = get_metrics_collector()

    # Record some sample metrics
    metrics.record_decision("kitchen", "spoke", "Context transition + suggestion", 0.85, 2)
    metrics.record_context_inference("kitchen", "cooking", 0.85)
    metrics.record_suggestions("kitchen", generated_count=3, offered_count=2)
    metrics.record_vision_call("kitchen", vision_tokens=1250, output_tokens=45, duration_ms=2340)
    metrics.record_snapshot("kitchen", duration_ms=850, success=True)

    # Get summary
    summary = metrics.get_summary()
    print(json.dumps(summary, indent=2))
