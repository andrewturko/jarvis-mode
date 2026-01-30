#!/usr/bin/env python3
"""
Scenario Evaluation Harness - Replays scripted days through the inference engine.

Each scenario defines an initial state, a timeline of events, and expectations
for each step. The harness mocks time and state, calls infer_context() ->
get_suggestions() -> should_stay_silent(), and asserts against expectations.

Usage:
    pytest tests/test_scenarios.py -v
    pytest tests/test_scenarios.py -k "evening_arrival" -v
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

# Add scripts to path so life_context can be imported
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
SERVICES_DIR = SCRIPTS_DIR / "services"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SERVICES_DIR))

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@dataclass
class StepResult:
    """Result of evaluating one timeline step."""
    step_index: int
    time: str
    room: str
    context: str
    confidence: float
    signals: List[str]
    suggestions: List[Dict[str, Any]]
    should_speak: bool
    silence_reason: Optional[str]
    passed: bool
    failures: List[str] = field(default_factory=list)


def load_scenario(name: str) -> dict:
    """Load a scenario JSON file by name."""
    path = SCENARIOS_DIR / f"{name}.json"
    with open(path) as f:
        return json.load(f)


def _build_time_context(hour: int) -> dict:
    """Build a time_context dict for a given hour, matching life_context.get_time_context()."""
    return {
        "hour": hour,
        "time_of_day": (
            "night" if hour < 6 else
            "early_morning" if hour < 8 else
            "morning" if hour < 11 else
            "midday" if hour < 14 else
            "afternoon" if hour < 17 else
            "evening" if hour < 21 else
            "late_evening" if hour < 23 else
            "night"
        ),
        "is_morning": 6 <= hour < 11,
        "is_meal_time": hour in [7, 8, 12, 13, 18, 19, 20],
        "is_evening": 17 <= hour < 23,
        "is_late_night": hour >= 23 or hour < 6,
        "is_work_hours": 9 <= hour < 17,
        "day_of_week": "Wednesday",
        "is_weekend": False
    }


def run_scenario(scenario: dict) -> List[StepResult]:
    """
    Replay a scenario through the inference engine.

    Mocking strategy:
    - Mock get_time_context() to return the correct time for each step
    - Mock load_json() to return scenario state for state.json/patterns.json
    - Mock datetime in the life_context module for settling period checks
    - Mock fatigue tracker functions when fatigue_state is in scenario
    """
    import life_context

    initial_state = scenario.get("initial_state", {})
    results = []

    for i, step in enumerate(scenario.get("timeline", [])):
        step_time = step["time"]
        room = step.get("room", "kitchen")
        room_obs = step.get("room_observations", {})
        expect = step.get("expect", {})
        is_arrival = step.get("is_arrival", False)
        is_settling = step.get("is_settling", False)

        # Parse step time
        hour = int(step_time.split(":")[0])
        minute = int(step_time.split(":")[1])
        mock_now = datetime(2026, 1, 29, hour, minute, 0)

        # Build state for this step (deep copy)
        state = json.loads(json.dumps(initial_state))

        # Apply home_state override if present
        if "home_state_override" in step:
            override = step["home_state_override"]
            if "settling_until" in override:
                su = str(override["settling_until"])
                if len(su) <= 5:  # Short time like "18:20"
                    override["settling_until"] = f"2026-01-29T{su}:00"
            state.setdefault("home_state", {}).update(override)

        # Inject decision log entries if specified
        if "inject_decision_log" in step:
            state.setdefault("decision_log", []).extend(step["inject_decision_log"])

        # Build mock for load_json
        original_load = life_context.load_json

        def make_load_json(captured_state, sc=scenario):
            def mock_load(path):
                path_str = str(path)
                if "state.json" in path_str:
                    return json.loads(json.dumps(captured_state))
                if "patterns.json" in path_str:
                    patterns = original_load(path)
                    if "fatigue_state" in sc:
                        patterns["fatigue_state"] = sc["fatigue_state"]
                    return patterns
                return original_load(path)
            return mock_load

        mock_load = make_load_json(state)

        # Build mock time context
        time_ctx = _build_time_context(hour)

        # Create a mock datetime class that returns our mock_now for .now()
        # but still allows .fromisoformat() and other class methods
        real_datetime = datetime

        class MockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return mock_now

        # Apply all mocks — patch at the intelligence submodule level where
        # functions are actually called, not on the life_context facade.
        patches = [
            # load_json is imported into each submodule; patch all call sites
            patch('intelligence._helpers.load_json', side_effect=mock_load),
            patch('intelligence.context_inference.load_json', side_effect=mock_load),
            patch('intelligence.suggestion_engine.load_json', side_effect=mock_load),
            patch('intelligence.observation_tracker.load_json', side_effect=mock_load),
            # Time context and activity chain live in context_inference
            patch('intelligence.context_inference.get_time_context', return_value=time_ctx),
            patch('intelligence.context_inference.get_activity_chain', return_value=[]),
            # datetime is imported separately in each module that uses it
            patch('intelligence.context_inference.datetime', MockDatetime),
            patch('intelligence.silence_logic.datetime', MockDatetime),
        ]

        # Mock fatigue tracker if scenario has fatigue_state
        if "fatigue_state" in scenario:
            fs = scenario["fatigue_state"]
            patches.extend([
                patch('intelligence.silence_logic._fatigue_has_budget', return_value=_calc_has_budget(fs)),
                patch('intelligence.silence_logic._fatigue_threshold', return_value=_calc_dynamic_threshold(fs)),
                patch('intelligence.suggestion_engine._fatigue_cooldown', side_effect=lambda a: _calc_cooldown(fs, a)),
                patch('intelligence.silence_logic.FATIGUE_TRACKING_AVAILABLE', True),
                patch('intelligence.suggestion_engine.FATIGUE_TRACKING_AVAILABLE', True),
            ])

        for p in patches:
            p.start()

        try:
            # Build home_state for infer_context
            home_state = {
                "lights_on": [],
                "lights_off": [],
                "media_playing": [],
                "music_playing": False
            }

            # 1. Infer context
            context_result = life_context.infer_context(room_obs, home_state)

            # 2. Get suggestions
            capabilities = life_context.get_capabilities()
            suggestions = life_context.get_suggestions(
                context_result, capabilities, home_state=home_state
            )

            # 3. Check silence logic
            should_silent, silence_reason = life_context.should_stay_silent(
                context_result,
                suggestions,
                recent_history=[],
                confidence_threshold=0.3,
                is_arrival=is_arrival,
                is_settling=is_settling
            )
        finally:
            for p in patches:
                p.stop()

        # Evaluate expectations
        failures = _evaluate_expectations(
            expect, context_result, suggestions, should_silent, silence_reason,
            scenario.get("fatigue_state")
        )

        result = StepResult(
            step_index=i,
            time=step_time,
            room=room,
            context=context_result["context"],
            confidence=context_result["confidence"],
            signals=context_result.get("signals", []),
            suggestions=suggestions,
            should_speak=not should_silent,
            silence_reason=silence_reason,
            passed=len(failures) == 0,
            failures=failures
        )
        results.append(result)

    return results


def _calc_has_budget(fatigue_state: dict) -> bool:
    """Replicate has_budget_remaining logic for mocking."""
    budget = fatigue_state.get("daily_budget", 8)
    sent = fatigue_state.get("suggestions_today", 0)
    accepted = fatigue_state.get("accepted_today", 0)
    if sent >= 3 and accepted == 0:
        budget = max(3, budget // 2)
    return sent < budget


def _calc_dynamic_threshold(fatigue_state: dict) -> float:
    """Replicate get_dynamic_threshold logic for mocking."""
    sent = fatigue_state.get("suggestions_today", 0)
    accepted = fatigue_state.get("accepted_today", 0)
    if sent < 3:
        return 0.3
    rate = accepted / sent
    if rate < 0.1:
        return 0.7
    elif rate < 0.3:
        return 0.5
    return 0.3


def _calc_cooldown(fatigue_state: dict, action: str) -> float:
    """Replicate get_cooldown_hours logic for mocking."""
    multiplier = fatigue_state.get("backoff_multipliers", {}).get(action, 1.0)
    return 2.0 * multiplier


def _evaluate_expectations(
    expect: dict,
    context_result: dict,
    suggestions: list,
    should_silent: bool,
    silence_reason: Optional[str],
    fatigue_state: Optional[dict] = None
) -> List[str]:
    """Evaluate all expectations against actual results."""
    failures = []
    context = context_result["context"]
    confidence = context_result["confidence"]
    signals = context_result.get("signals", [])
    should_speak = not should_silent

    # Context assertions
    if "context" in expect:
        if context != expect["context"]:
            failures.append(
                f"Expected context '{expect['context']}', got '{context}'"
            )

    if "context_not" in expect:
        if context == expect["context_not"]:
            failures.append(
                f"Context should NOT be '{expect['context_not']}', but it is"
            )

    if "context_one_of" in expect:
        if context not in expect["context_one_of"]:
            failures.append(
                f"Expected context in {expect['context_one_of']}, got '{context}'"
            )

    # Confidence assertions
    if "min_confidence" in expect:
        if confidence < expect["min_confidence"]:
            failures.append(
                f"Expected confidence >= {expect['min_confidence']}, got {confidence}"
            )

    if "max_confidence" in expect:
        if confidence > expect["max_confidence"]:
            failures.append(
                f"Expected confidence <= {expect['max_confidence']}, got {confidence}"
            )

    # Signal assertions
    if "signals_contain" in expect:
        sig = expect["signals_contain"]
        if isinstance(sig, str):
            sig = [sig]
        for s in sig:
            if s not in signals:
                failures.append(f"Expected signal '{s}' not found in {signals}")

    # Should speak assertions
    if "should_speak" in expect:
        if should_speak != expect["should_speak"]:
            failures.append(
                f"Expected should_speak={expect['should_speak']}, "
                f"got {should_speak} (reason: {silence_reason})"
            )

    if "silence_reason_contains" in expect and silence_reason:
        if expect["silence_reason_contains"].lower() not in silence_reason.lower():
            failures.append(
                f"Expected silence reason to contain "
                f"'{expect['silence_reason_contains']}', got '{silence_reason}'"
            )

    # Suggestion assertions
    if "has_suggestions" in expect:
        if expect["has_suggestions"] and not suggestions:
            failures.append("Expected suggestions but got none")
        elif not expect["has_suggestions"] and suggestions:
            failures.append(f"Expected no suggestions but got {len(suggestions)}")

    if "suggestion_types_allowed" in expect:
        allowed = set(expect["suggestion_types_allowed"])
        for s in suggestions:
            stype = s.get("type", "unknown")
            if stype not in allowed:
                failures.append(
                    f"Suggestion type '{stype}' not in allowed types {allowed}"
                )

    if "suggestion_types_not" in expect:
        forbidden = set(expect["suggestion_types_not"])
        for s in suggestions:
            stype = s.get("type", "unknown")
            if stype in forbidden:
                failures.append(
                    f"Suggestion type '{stype}' should not be present"
                )

    if "suggestion_diversity" in expect and expect["suggestion_diversity"]:
        actions = [s.get("action") for s in suggestions]
        if len(actions) != len(set(actions)):
            failures.append(
                f"Expected diverse suggestions but found duplicates: {actions}"
            )

    # Fatigue assertions (use pre-calculated values)
    if fatigue_state and "fatigue_dynamic_threshold_above" in expect:
        threshold = _calc_dynamic_threshold(fatigue_state)
        expected_min = expect["fatigue_dynamic_threshold_above"]
        if threshold < expected_min:
            failures.append(
                f"Expected dynamic threshold >= {expected_min}, got {threshold}"
            )

    if fatigue_state and "fatigue_cooldown_elevated" in expect:
        action = expect["fatigue_cooldown_elevated"]
        cooldown = _calc_cooldown(fatigue_state, action)
        if cooldown <= 2.0:
            failures.append(
                f"Expected elevated cooldown for '{action}', got {cooldown}h (base is 2h)"
            )

    return failures


# --- Pytest test functions ---

def _get_scenario_files():
    """Get all scenario JSON files."""
    return sorted(SCENARIOS_DIR.glob("*.json"))


def _scenario_ids():
    """Get scenario names for test IDs."""
    return [f.stem for f in _get_scenario_files()]


@pytest.fixture(params=_get_scenario_files(), ids=_scenario_ids())
def scenario_file(request):
    return request.param


def test_scenario(scenario_file):
    """Run a scenario and assert all steps pass."""
    with open(scenario_file) as f:
        scenario = json.load(f)

    results = run_scenario(scenario)

    # Print detailed results for debugging
    scenario_name = scenario.get("name", scenario_file.stem)
    print(f"\n{'=' * 60}")
    print(f"Scenario: {scenario_name}")
    print(f"{'=' * 60}")

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"\n  Step {r.step_index} [{r.time}] {r.room}: {status}")
        print(f"    Context: {r.context} ({r.confidence:.2f})")
        print(f"    Signals: {', '.join(r.signals[:5])}{'...' if len(r.signals) > 5 else ''}")
        print(f"    Suggestions: {len(r.suggestions)}")
        print(f"    Should speak: {r.should_speak}")
        if r.silence_reason:
            print(f"    Silence reason: {r.silence_reason}")
        if r.failures:
            for f in r.failures:
                print(f"    FAILURE: {f}")

    # Collect all failures
    all_failures = []
    for r in results:
        for f in r.failures:
            all_failures.append(f"Step {r.step_index} [{r.time}] {r.room}: {f}")

    if all_failures:
        pytest.fail(
            f"Scenario '{scenario_name}' had {len(all_failures)} failure(s):\n"
            + "\n".join(f"  - {f}" for f in all_failures)
        )


# --- Individual scenario tests for targeted runs ---

def test_evening_arrival():
    """Arriving home after 8 hours should detect arriving_home, not cooking."""
    scenario = load_scenario("evening_arrival")
    results = run_scenario(scenario)

    # Step 0: Should be arriving_home, not cooking
    assert results[0].context != "cooking", (
        f"Expected arriving_home context when first entering kitchen after being away, "
        f"got '{results[0].context}'"
    )


def test_quick_pass_through():
    """Brief kitchen visit should NOT infer cooking."""
    scenario = load_scenario("quick_pass_through")
    results = run_scenario(scenario)

    assert results[0].context != "cooking", (
        f"Brief kitchen pass-through should not infer cooking, "
        f"got '{results[0].context}'"
    )


def test_confidence_threshold():
    """Low confidence should produce silence."""
    scenario = load_scenario("confidence_threshold")
    results = run_scenario(scenario)

    step0 = results[0]
    if step0.confidence < 0.3:
        assert not step0.should_speak, (
            f"Low confidence ({step0.confidence}) should be silent, "
            f"but should_speak={step0.should_speak}"
        )


def test_sustained_cooking_diversity():
    """Sustained cooking should produce diverse suggestions."""
    scenario = load_scenario("sustained_cooking")
    results = run_scenario(scenario)

    step0 = results[0]
    if step0.suggestions:
        actions = [s.get("action") for s in step0.suggestions]
        assert len(actions) == len(set(actions)), (
            f"Expected unique suggestion actions, got duplicates: {actions}"
        )
