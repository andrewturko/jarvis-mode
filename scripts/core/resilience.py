#!/usr/bin/env python3
"""
Resilience and error recovery utilities for Jarvis Mode.

Provides retry decorators, circuit breakers, and graceful degradation
for handling transient failures in external services (HA, Vision API, etc.).
"""

import time
import functools
from typing import Callable, Optional, Type, Tuple, Any
from datetime import datetime, timedelta
from enum import Enum

from core.logger import get_logger

logger = get_logger("jarvis.resilience")


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Circuit is open, calls fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    Prevents cascading failures by failing fast when a service is unavailable.

    States:
    - CLOSED: Normal operation, all calls go through
    - OPEN: Service is down, calls fail immediately without trying
    - HALF_OPEN: Testing recovery, allow one test call

    Transitions:
    - CLOSED -> OPEN: After failure_threshold consecutive failures
    - OPEN -> HALF_OPEN: After timeout_seconds elapsed
    - HALF_OPEN -> CLOSED: If test call succeeds
    - HALF_OPEN -> OPEN: If test call fails
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        timeout_seconds: int = 60
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Circuit breaker name (for logging)
            failure_threshold: Number of failures before opening circuit
            timeout_seconds: Time to wait before attempting recovery
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.last_success_time: Optional[datetime] = None

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker.

        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to function

        Returns:
            Function result

        Raises:
            Exception: If circuit is open or function fails
        """
        # Check if we should attempt recovery
        if self.state == CircuitState.OPEN:
            if self._should_attempt_recovery():
                self.state = CircuitState.HALF_OPEN
                logger.info(
                    "circuit_breaker_half_open",
                    circuit=self.name,
                    message="Attempting recovery"
                )
            else:
                logger.warning(
                    "circuit_breaker_open",
                    circuit=self.name,
                    message="Circuit is open, failing fast"
                )
                raise Exception(f"Circuit breaker {self.name} is OPEN")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if self.last_failure_time is None:
            return True

        elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
        return elapsed >= self.timeout_seconds

    def _on_success(self):
        """Handle successful call."""
        if self.state == CircuitState.HALF_OPEN:
            logger.info(
                "circuit_breaker_closed",
                circuit=self.name,
                message="Service recovered, closing circuit"
            )

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_success_time = datetime.utcnow()

    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        if self.state == CircuitState.HALF_OPEN:
            # Test call failed, back to OPEN
            self.state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker_opened",
                circuit=self.name,
                message="Recovery attempt failed, reopening circuit"
            )
        elif self.failure_count >= self.failure_threshold:
            # Too many failures, open the circuit
            self.state = CircuitState.OPEN
            logger.error(
                "circuit_breaker_opened",
                circuit=self.name,
                failure_count=self.failure_count,
                threshold=self.failure_threshold,
                message="Circuit opened due to failures"
            )

    def reset(self):
        """Reset circuit breaker to closed state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        logger.info("circuit_breaker_reset", circuit=self.name)


# Global circuit breakers for different services
_circuit_breakers = {}


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    timeout_seconds: int = 60
) -> CircuitBreaker:
    """
    Get or create a circuit breaker.

    Args:
        name: Circuit breaker name
        failure_threshold: Failures before opening
        timeout_seconds: Recovery attempt timeout

    Returns:
        CircuitBreaker instance
    """
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(
            name,
            failure_threshold=failure_threshold,
            timeout_seconds=timeout_seconds
        )
    return _circuit_breakers[name]


def retry(
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
    backoff_multiplier: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    circuit_breaker_name: Optional[str] = None
):
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        delay_seconds: Initial delay between retries
        backoff_multiplier: Multiplier for exponential backoff
        exceptions: Tuple of exception types to retry on
        circuit_breaker_name: Optional circuit breaker to use

    Example:
        @retry(max_attempts=3, delay_seconds=1.0)
        def call_ha_api():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay_seconds

            # Get circuit breaker if specified
            breaker = None
            if circuit_breaker_name:
                breaker = get_circuit_breaker(circuit_breaker_name)

            while attempt < max_attempts:
                try:
                    # Use circuit breaker if available
                    if breaker:
                        return breaker.call(func, *args, **kwargs)
                    else:
                        return func(*args, **kwargs)

                except exceptions as e:
                    attempt += 1

                    if attempt >= max_attempts:
                        # Final attempt failed
                        logger.error(
                            "retry_exhausted",
                            function=func.__name__,
                            attempts=attempt,
                            error=str(e),
                            message=f"All {max_attempts} attempts failed"
                        )
                        raise

                    # Log retry
                    logger.warning(
                        "retry_attempt",
                        function=func.__name__,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        delay_seconds=current_delay,
                        error=str(e)
                    )

                    # Wait before retry
                    time.sleep(current_delay)

                    # Exponential backoff
                    current_delay *= backoff_multiplier

            # Should never reach here
            raise Exception(f"Retry logic error in {func.__name__}")

        return wrapper
    return decorator


def with_timeout(timeout_seconds: float, default_value: Any = None):
    """
    Decorator to add timeout to a function.

    If function doesn't complete within timeout, returns default_value.

    Args:
        timeout_seconds: Maximum execution time
        default_value: Value to return on timeout

    Example:
        @with_timeout(10.0, default_value=[])
        def slow_function():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            import signal

            def timeout_handler(signum, frame):
                raise TimeoutError(f"Function {func.__name__} timed out after {timeout_seconds}s")

            # Set timeout handler (Unix only)
            try:
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(int(timeout_seconds))

                try:
                    result = func(*args, **kwargs)
                    signal.alarm(0)  # Cancel alarm
                    return result
                except TimeoutError as e:
                    logger.error(
                        "function_timeout",
                        function=func.__name__,
                        timeout_seconds=timeout_seconds,
                        error=str(e)
                    )
                    return default_value
            except Exception as e:
                # signal.alarm not available (Windows)
                logger.debug(
                    "timeout_not_available",
                    message="Timeout decorator not supported on this platform"
                )
                return func(*args, **kwargs)

        return wrapper
    return decorator


def graceful_degradation(fallback_value: Any = None, log_error: bool = True):
    """
    Decorator for graceful degradation.

    If function fails, return fallback value instead of raising exception.

    Args:
        fallback_value: Value to return on failure
        log_error: Whether to log the error

    Example:
        @graceful_degradation(fallback_value={})
        def get_home_state():
            # If this fails, return {} instead of crashing
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_error:
                    logger.warning(
                        "graceful_degradation",
                        function=func.__name__,
                        error=str(e),
                        fallback_value=fallback_value,
                        message=f"Function failed, returning fallback value"
                    )
                return fallback_value

        return wrapper
    return decorator


class RateLimiter:
    """
    Rate limiter to prevent overwhelming external services.

    Uses token bucket algorithm.
    """

    def __init__(self, max_calls: int, time_window_seconds: float):
        """
        Initialize rate limiter.

        Args:
            max_calls: Maximum calls allowed in time window
            time_window_seconds: Time window in seconds
        """
        self.max_calls = max_calls
        self.time_window = time_window_seconds
        self.calls = []

    def allow_call(self) -> bool:
        """
        Check if a call is allowed under rate limit.

        Returns:
            True if call is allowed, False otherwise
        """
        now = time.time()

        # Remove old calls outside time window
        self.calls = [t for t in self.calls if now - t < self.time_window]

        # Check if we're under limit
        if len(self.calls) < self.max_calls:
            self.calls.append(now)
            return True

        logger.warning(
            "rate_limit_exceeded",
            max_calls=self.max_calls,
            time_window=self.time_window,
            message="Rate limit exceeded"
        )
        return False

    def wait_if_needed(self):
        """Block until a call is allowed."""
        while not self.allow_call():
            time.sleep(0.1)


# Example usage
if __name__ == "__main__":
    # Test retry decorator
    @retry(max_attempts=3, delay_seconds=0.5)
    def flaky_function():
        import random
        if random.random() < 0.7:
            raise Exception("Random failure")
        return "Success"

    # Test circuit breaker
    breaker = get_circuit_breaker("test_service")

    def failing_service():
        raise Exception("Service unavailable")

    # This will open the circuit after threshold failures
    for i in range(10):
        try:
            breaker.call(failing_service)
        except Exception as e:
            print(f"Attempt {i+1}: {e}")

    print(f"Circuit state: {breaker.state}")

    # Test graceful degradation
    @graceful_degradation(fallback_value={"status": "unknown"})
    def get_status():
        raise Exception("API down")

    result = get_status()
    print(f"Degraded result: {result}")
