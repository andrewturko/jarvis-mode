#!/usr/bin/env python3
"""
Test observability features: metrics, logging, resilience, caching.

This test verifies that all Phase 4 observability components work correctly.
"""

import sys
import json
import time
from pathlib import Path

# Add parent directory to path
SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from core.metrics import get_metrics_collector
from core.logger import get_logger, setup_logging
from core.resilience import (
    retry, CircuitBreaker, get_circuit_breaker,
    graceful_degradation, RateLimiter
)
from services.ha_service import HAService, HACache


def test_metrics():
    """Test metrics collection and reporting."""
    print("\n=== Testing Metrics ===")

    metrics = get_metrics_collector()

    # Record sample metrics
    print("Recording sample decision...")
    metrics.record_decision(
        room="kitchen",
        decision="spoke",
        reason="Context transition + suggestion",
        context_confidence=0.85,
        suggestions_count=2
    )

    print("Recording context inference...")
    metrics.record_context_inference(
        room="kitchen",
        context="cooking",
        confidence=0.85
    )

    print("Recording suggestions...")
    metrics.record_suggestions(
        room="kitchen",
        generated_count=3,
        offered_count=2
    )

    print("Recording vision call...")
    metrics.record_vision_call(
        room="kitchen",
        vision_tokens=1250,
        output_tokens=45,
        duration_ms=2340
    )

    print("Recording snapshot...")
    metrics.record_snapshot(
        room="kitchen",
        duration_ms=850,
        success=True
    )

    # Get summary
    summary = metrics.get_summary()
    print("\nMetrics Summary:")
    print(json.dumps(summary, indent=2))

    # Verify metrics were recorded
    assert summary["decisions"]["total"] >= 1, "Decision not recorded"
    assert summary["context"]["total_inferences"] >= 1, "Context not recorded"
    print("✓ Metrics recording works")


def test_logging():
    """Test structured logging."""
    print("\n=== Testing Logging ===")

    # Setup logging
    setup_logging(log_level="DEBUG", log_to_console=False)

    logger = get_logger("jarvis.test")

    # Log various events
    logger.info("test_event", room="kitchen", action="snapshot", duration_ms=850)
    logger.debug("debug_message", detail="test detail", count=42)
    logger.warning("warning_message", threshold=100, actual=150)
    logger.error("error_message", error_type="timeout", room="kitchen")

    # Check log file exists
    log_file = SKILL_DIR / "logs" / "jarvis.log"
    assert log_file.exists(), "Log file not created"

    # Read and verify log entries
    with open(log_file, 'r') as f:
        lines = f.readlines()
        last_lines = lines[-4:]  # Get last 4 entries

        # Verify JSON format
        for line in last_lines:
            try:
                log_entry = json.loads(line)
                assert "timestamp" in log_entry
                assert "level" in log_entry
                assert "component" in log_entry
                print(f"✓ Log entry: {log_entry.get('operation', 'unknown')} - {log_entry.get('message', '')}")
            except json.JSONDecodeError:
                print(f"✗ Invalid JSON in log: {line[:100]}")

    print("✓ Structured logging works")


def test_resilience():
    """Test resilience features: retry, circuit breaker, graceful degradation."""
    print("\n=== Testing Resilience ===")

    # Test retry decorator
    print("\nTesting retry decorator...")
    attempt_count = {"count": 0}

    @retry(max_attempts=3, delay_seconds=0.1, backoff_multiplier=2.0)
    def flaky_function():
        attempt_count["count"] += 1
        if attempt_count["count"] < 3:
            raise Exception("Temporary failure")
        return "Success"

    result = flaky_function()
    assert result == "Success", "Retry failed"
    assert attempt_count["count"] == 3, f"Wrong retry count: {attempt_count['count']}"
    print(f"✓ Retry worked (took {attempt_count['count']} attempts)")

    # Test circuit breaker
    print("\nTesting circuit breaker...")
    breaker = get_circuit_breaker("test_service", failure_threshold=3, timeout_seconds=1)

    def failing_service():
        raise Exception("Service unavailable")

    # Trigger failures to open circuit
    failure_count = 0
    for i in range(5):
        try:
            breaker.call(failing_service)
        except Exception:
            failure_count += 1

    assert breaker.state.value == "open", f"Circuit not opened: {breaker.state}"
    print(f"✓ Circuit breaker opened after {failure_count} failures")

    # Test graceful degradation
    print("\nTesting graceful degradation...")

    @graceful_degradation(fallback_value={"status": "unknown"})
    def unstable_function():
        raise Exception("Random failure")

    result = unstable_function()
    assert result == {"status": "unknown"}, "Graceful degradation failed"
    print("✓ Graceful degradation works")

    # Test rate limiter
    print("\nTesting rate limiter...")
    limiter = RateLimiter(max_calls=3, time_window_seconds=1.0)

    allowed_count = 0
    blocked_count = 0

    for i in range(5):
        if limiter.allow_call():
            allowed_count += 1
        else:
            blocked_count += 1

    assert allowed_count == 3, f"Rate limiter allowed wrong count: {allowed_count}"
    assert blocked_count == 2, f"Rate limiter blocked wrong count: {blocked_count}"
    print(f"✓ Rate limiter works (allowed {allowed_count}, blocked {blocked_count})")


def test_caching():
    """Test HA service caching."""
    print("\n=== Testing Caching ===")

    cache = HACache(ttl_seconds=2)

    # Set and get
    cache.set("test_key", {"data": "test_value"})
    result = cache.get("test_key")
    assert result == {"data": "test_value"}, "Cache get failed"
    print("✓ Cache set/get works")

    # Test expiration
    print("Waiting for cache to expire (2s)...")
    time.sleep(2.1)
    expired = cache.get("test_key")
    assert expired is None, "Cache didn't expire"
    print("✓ Cache expiration works")

    # Test clear
    cache.set("key1", "value1")
    cache.set("key2", "value2")
    cache.clear()
    assert cache.get("key1") is None, "Cache clear failed"
    print("✓ Cache clear works")


def test_ha_service_integration():
    """Test HA service with caching and resilience."""
    print("\n=== Testing HA Service Integration ===")

    # Note: This test won't connect to actual HA, but tests the structure
    ha = HAService(
        ha_url="http://localhost:8123",
        ha_token="fake_token",
        cache_ttl_seconds=30
    )

    # Test health check (will fail but should return proper structure)
    health = ha.check_health()
    assert "status" in health, "Health check missing status"
    print(f"✓ Health check structure correct: {health}")

    # Test graceful degradation
    home_state = ha.get_home_state()
    assert isinstance(home_state, dict), "Home state not dict"
    assert "lights_on" in home_state, "Home state missing lights_on"
    print("✓ Graceful degradation works for HA calls")


def run_all_tests():
    """Run all observability tests."""
    print("=" * 60)
    print("Jarvis Mode - Phase 4 Observability Tests")
    print("=" * 60)

    try:
        test_metrics()
        test_logging()
        test_resilience()
        test_caching()
        test_ha_service_integration()

        print("\n" + "=" * 60)
        print("✅ All observability tests passed!")
        print("=" * 60)

        return True
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
