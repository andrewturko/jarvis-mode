"""Intelligence package — context inference, suggestions, and learning.

Re-exports all public APIs so callers can use:
    from intelligence import infer_context, get_suggestions, ...
"""

from intelligence._helpers import (
    load_json, save_json, get_life_model, get_capabilities,
    get_patterns, save_patterns,
)
from intelligence.activity_chains import get_activity_chain, chain_signals
from intelligence.context_inference import (
    get_time_context, infer_context, infer_context_from_state,
    infer_global_context, get_context_transitions, update_current_context,
)
from intelligence.suggestion_engine import (
    get_suggestions, record_preference, record_correction_from_feedback,
    get_preference_modifiers, should_suppress_suggestion,
)
from intelligence.silence_logic import should_stay_silent
from intelligence.observation_tracker import (
    record_observation, record_suggestion_response,
    record_sent_suggestion, get_last_awaiting_feedback,
    process_user_feedback, get_recently_sent_suggestions,
    was_suggestion_sent_recently, get_recent_observations,
)

__all__ = [
    # File helpers
    'load_json', 'save_json', 'get_life_model', 'get_capabilities',
    'get_patterns', 'save_patterns',
    # Activity chains
    'get_activity_chain', 'chain_signals',
    # Context inference
    'get_time_context', 'infer_context', 'infer_context_from_state',
    'infer_global_context', 'get_context_transitions', 'update_current_context',
    # Suggestions
    'get_suggestions', 'record_preference', 'record_correction_from_feedback',
    'get_preference_modifiers', 'should_suppress_suggestion',
    # Silence logic
    'should_stay_silent',
    # Observation tracking
    'record_observation', 'record_suggestion_response',
    'record_sent_suggestion', 'get_last_awaiting_feedback',
    'process_user_feedback', 'get_recently_sent_suggestions',
    'was_suggestion_sent_recently', 'get_recent_observations',
]
