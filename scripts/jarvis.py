#!/usr/bin/env python3
"""
Jarvis Mode - Proactive home intelligence system for OpenClaw.

CLI interface for managing Jarvis Mode and interacting with home automation.
"""

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Add scripts directory to path for imports
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

# Core infrastructure
from core import StateManager, JarvisConfig, get_logger, setup_logging

# Services
from services import HAService, SnapshotService, OccupancyService
from services.context_service import ContextService
from services.activity_log import ActivityLog
from services.transition_detector import TransitionDetector

# Handlers
from handlers import EmptyRoomHandler, OccupiedRoomHandler

# Intelligence
import life_context

# Paths
from core.paths import (
    SKILL_DIR, CONFIG_FILE, STATE_FILE, PATTERNS_FILE,
    HOOKS_FILE, SNAPSHOT_DIR,
)

# Initialize logging
setup_logging(log_level="INFO")
logger = get_logger("jarvis.cli")


class JarvisCLI:
    """
    Command-line interface for Jarvis Mode.

    Provides all CLI commands and delegates to service modules.
    """

    def __init__(self):
        """Initialize CLI with all services."""
        try:
            # Load configuration
            self.config = JarvisConfig.load(CONFIG_FILE)

            # Initialize state manager
            self.state_manager = StateManager(STATE_FILE)

            # Initialize HA service
            self.ha_service = HAService()

            # Initialize snapshot service
            self.snapshot_service = SnapshotService(
                ha_url=self.ha_service.ha_url,
                ha_token=self.ha_service.ha_token,
                snapshot_dir=SNAPSHOT_DIR,
                state_manager=self.state_manager
            )

            # Initialize context service (Phase 2)
            self.context_service = ContextService(
                state_manager=self.state_manager,
                confidence_threshold=self.config.confidence_threshold
            )

            # Initialize occupancy service
            self.occupancy_service = OccupancyService(
                config=self.config,
                state_manager=self.state_manager,
                ha_service=self.ha_service,
                context_service=self.context_service
            )

            # Initialize handlers
            self.empty_room_handler = EmptyRoomHandler(
                config=self.config,
                ha_service=self.ha_service,
                snapshot_service=self.snapshot_service
            )

            self.occupied_room_handler = OccupiedRoomHandler(
                config=self.config,
                ha_service=self.ha_service,
                context_service=self.context_service
            )

            # Activity log for sharing Jarvis context with main openclaw conversation
            self.activity_log = ActivityLog()

            logger.info("jarvis_cli_initialized")

        except Exception as e:
            logger.error("jarvis_cli_init_failed", error=str(e), exc_info=True)
            print(f"Error initializing Jarvis: {e}", file=sys.stderr)
            sys.exit(1)

    def _generate_prediction_insight(self, predictions: list, room: str, occupied: bool) -> str:
        """
        Generate a natural language insight from behavioral predictions.

        This helps Claude reason naturally about what to suggest.
        """
        if not predictions:
            return None

        # Find highest confidence prediction
        top = max(predictions, key=lambda p: p.get("confidence", 0))

        if top["confidence"] >= 0.7:
            return f"Strong pattern: {top['what']} ({top['confidence']:.0%} confidence). {top['reason']}."
        elif top["confidence"] >= 0.5:
            return f"Likely pattern: {top['what']} ({top['confidence']:.0%} confidence). {top['reason']}."
        elif top["confidence"] >= 0.3:
            return f"Possible pattern: {top['what']} ({top['confidence']:.0%} confidence). Consider mentioning if relevant."
        else:
            return None

    def _get_recommended_message(self, suggestions: list, should_be_silent: bool) -> str:
        """
        Get a recommended message for the agent to send.

        Picks a random example from the best suggestion's message_template
        for variety. Falls back to the static 'message' field if no template.

        Returns None if silent.
        """
        if should_be_silent or not suggestions:
            return None

        import random

        # Suggestions are already sorted by effective_weight from get_suggestions()
        # Just take the first one
        best = suggestions[0]

        # Try message_template examples first for variety
        template = best.get("message_template", {})
        examples = template.get("examples", []) or best.get("examples", [])
        if examples:
            return random.choice(examples)

        return best.get("message") or best.get("reason", "")

    def _build_message_generation_context(self, now: datetime, hour: int) -> dict:
        """
        Build context for AI-generated dynamic messaging.

        Provides tone, style, and environmental cues so the agent generates
        varied, natural-sounding messages instead of repeating static strings.
        """
        # Natural time description
        if hour < 5:
            time_natural = "late at night"
        elif hour < 8:
            time_natural = "early morning"
        elif hour < 12:
            time_natural = "this morning"
        elif hour < 14:
            time_natural = "around midday"
        elif hour < 17:
            time_natural = "this afternoon"
        elif hour < 21:
            time_natural = "this evening"
        else:
            time_natural = "tonight"

        day_type = "weekend" if now.weekday() >= 5 else "weekday"

        # Pull engagement data from fatigue tracker
        interactions_today = 0
        responsiveness = "normal"
        try:
            from services.fatigue_tracker import _get_fatigue_state
            fatigue = _get_fatigue_state()
            interactions_today = fatigue.get("suggestions_today", 0)
            accepted = fatigue.get("accepted_today", 0)
            sent = fatigue.get("suggestions_today", 0)
            if sent >= 3:
                rate = accepted / sent
                if rate < 0.1:
                    responsiveness = "low"
                elif rate < 0.3:
                    responsiveness = "moderate"
                else:
                    responsiveness = "high"
        except ImportError:
            pass

        # Style guide adapts to responsiveness
        if responsiveness == "low":
            style = "Ultra-brief, one sentence max. Don't push — the user hasn't been engaging today."
        elif responsiveness == "moderate":
            style = "Brief and warm. One casual sentence."
        else:
            style = "Warm, conversational. Like a thoughtful roommate — natural, not robotic."

        return {
            "time_natural": time_natural,
            "day_type": day_type,
            "day_name": now.strftime("%A"),
            "interactions_today": interactions_today,
            "responsiveness": responsiveness,
            "style": style,
            "instructions": (
                "Generate a UNIQUE message each time. Use the examples in message_template "
                "as inspiration for tone and intent, but NEVER copy them verbatim. "
                "Examples encode ACTIVITY context only. Use time_natural as the sole "
                "source for time-of-day — never double up with time references from examples. "
                "Keep it short — one sentence, maybe two if needed."
            )
        }

    def _occupancy_note(self, source: str, has_snapshot: bool) -> str:
        """Generate context-aware note about occupancy verification status."""
        if source == "motion_on":
            return None  # Verified, no note needed
        elif source == "snapshot_pending":
            return "VERIFY from snapshot — motion off but check image for stationary person."
        elif source == "last_known":
            return "Using last known state (cooldown active, no snapshot). May be stale."
        else:
            return "Occupancy unverified."

    def _get_external_context(self, home_state: dict) -> dict:
        """
        Query external plugins for compound context.

        Returns insights from calendar, weather, etc. that can inform suggestions.
        """
        try:
            from services.external_context import ExternalContext
            ext = ExternalContext()

            # Get raw external context
            external = ext.get_all_context()

            # Generate compound insights
            insights = ext.get_insights({
                "occupied_rooms": [r for r, s in self.state_manager.read_state().get("rooms", {}).items()
                                   if s.get("occupancy", {}).get("current")],
                "lights_on": home_state.get("lights_on", [])
            })

            return {
                "sources": external,
                "compound_insights": insights if insights else None
            }
        except Exception:
            return None

    def run(self, args):
        """
        Run CLI command.

        Args:
            args: sys.argv (including script name)
        """
        if len(args) < 2:
            self.print_usage()
            sys.exit(1)

        cmd = args[1]

        try:
            # Route command
            if cmd == "status":
                self.cmd_status()
            elif cmd == "enable":
                self.cmd_enable()
            elif cmd == "disable":
                self.cmd_disable()
            elif cmd == "snapshot":
                self.cmd_snapshot(args)
            elif cmd == "context":
                self.cmd_context(args)
            elif cmd == "poll":
                self.cmd_poll()
            elif cmd == "verify-empty":
                self.cmd_verify_empty(args)
            elif cmd == "handle-empty":
                self.cmd_handle_empty(args)
            elif cmd == "handle-occupied":
                self.cmd_handle_occupied(args)
            elif cmd == "occupancy":
                self.cmd_occupancy()
            elif cmd == "home-state":
                self.cmd_home_state()
            elif cmd == "room-lights":
                self.cmd_room_lights(args)
            elif cmd == "record":
                self.cmd_record(args)
            elif cmd == "cleanup":
                self.cmd_cleanup()
            elif cmd == "setup":
                self.cmd_setup()
            elif cmd == "feedback":
                self.cmd_feedback(args)
            elif cmd == "sent":
                self.cmd_sent(args)
            elif cmd == "respond":
                self.cmd_respond(args)
            elif cmd == "decisions":
                self.cmd_decisions(args)
            elif cmd == "patterns":
                self.cmd_patterns(args)
            elif cmd == "events":
                self.cmd_events(args)
            elif cmd == "activity":
                self.cmd_activity()
            else:
                print(f"Unknown command: {cmd}", file=sys.stderr)
                self.print_usage()
                sys.exit(1)

        except Exception as e:
            logger.error("command_failed", command=cmd, error=str(e), exc_info=True)
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)

    def print_usage(self):
        """Print usage help."""
        print("Usage: jarvis.py <command> [args]")
        print("\nCommands:")
        print("  status                      Get full Jarvis status")
        print("  enable                      Enable Jarvis mode")
        print("  disable                     Disable Jarvis mode")
        print("  snapshot <room> [--manual]  Capture camera snapshot")
        print("  context <room> [--manual]   Get full room context")
        print("  poll                        Poll occupancy and detect transitions")
        print("  verify-empty <room>         Get snapshot for visual verification before lights-off")
        print("  handle-empty <room>         Handle empty room event")
        print("  handle-occupied <room>      Handle occupied room event")
        print("  occupancy                   Get current occupancy for all rooms")
        print("  home-state                  Get current home state")
        print("  room-lights <room>          Get lights on in room")
        print("  record <room> <json|activity>  Record observation")
        print("  feedback <suggestion_json> <accepted|rejected>  Record suggestion feedback")
        print("  sent <room> <suggestion_json> [message]  Record that suggestion was sent")
        print("  respond <user_response>     Process yes/no response to last suggestion")
        print("  decisions [--limit N] [--room ROOM]  View decision audit trail")
        print("  patterns [--analyze]        View learned behavior patterns")
        print("  events [--hours N]          View collected HA events")
        print("  cleanup                     Delete old snapshots")
        print("  setup                       Self-register with OpenClaw")

    def cmd_status(self):
        """Get full Jarvis status."""
        state = self.state_manager.read_state()

        # Build room states
        room_states = {}
        for room_name in self.config.get_enabled_cameras().keys():
            room_state = self.state_manager.get_room_state(room_name)
            check_result = self.occupancy_service.should_check_room(room_name)

            if room_state:
                occupancy = room_state.get("occupancy", {})
                room_states[room_name] = {
                    "occupancy": occupancy.get("current"),
                    "changed_at": occupancy.get("changed_at"),
                    "should_check": check_result["should_check"],
                    "reason": check_result["reason"],
                    "motion_detected": check_result["motion_state"]
                }

        status = {
            "enabled": self.config.enabled,
            "active_hours": self.config.active_hours.is_active(datetime.now().hour),
            "active_hours_config": {
                "start": self.config.active_hours.start,
                "end": self.config.active_hours.end
            },
            "check_interval": self.config.check_interval_minutes,
            "cooldown": self.config.cooldown_minutes,
            "motion_cooldown": self.config.motion_cooldown_minutes,
            "motion_aware": self.config.motion_aware,
            "instant_alerts": self.config.instant_alerts,
            "quiet_mode": self.config.quiet_mode,
            "auto_actions": {
                "enabled": self.config.auto_actions.enabled,
                "announce": self.config.auto_actions.announce_actions
            },
            "last_poll": state.get("last_poll"),
            "cameras": list(self.config.get_enabled_cameras().keys()),
            "room_states": room_states
        }

        print(json.dumps(status, indent=2))

    def cmd_enable(self):
        """Enable Jarvis mode."""
        self.config.enabled = True
        self.config.save(CONFIG_FILE)
        logger.info("jarvis_enabled")
        print(json.dumps({"enabled": True, "message": "Jarvis mode enabled"}))

    def cmd_disable(self):
        """Disable Jarvis mode."""
        self.config.enabled = False
        self.config.save(CONFIG_FILE)
        logger.info("jarvis_disabled")
        print(json.dumps({"enabled": False, "message": "Jarvis mode disabled"}))

    def cmd_snapshot(self, args):
        """Capture camera snapshot."""
        if len(args) < 3:
            print("Usage: jarvis.py snapshot <room> [--manual]", file=sys.stderr)
            sys.exit(1)

        room = args[2]
        manual = "--manual" in args

        camera_config = self.config.cameras.get(room)
        if not camera_config:
            print(json.dumps({"error": f"Unknown room: {room}"}), file=sys.stderr)
            sys.exit(1)

        path = self.snapshot_service.get_snapshot(
            room_name=room,
            camera_entity_id=camera_config.entity_id,
            cooldown_minutes=self.config.cooldown_minutes,
            manual=manual
        )

        if path:
            print(json.dumps({"room": room, "snapshot": path, "manual": manual}))
        else:
            print(json.dumps({"room": room, "error": "Failed or blocked by cooldown", "manual": manual}))
            sys.exit(1)

    def cmd_context(self, args):
        """Get full room context."""
        if len(args) < 3:
            print("Usage: jarvis.py context <room> [--manual]", file=sys.stderr)
            sys.exit(1)

        room = args[2]
        manual = "--manual" in args

        # Generate trace ID for this request — threads through logs and output payload
        trace_id = uuid.uuid4().hex[:8]
        trace_logger = get_logger("jarvis.context", trace_id=trace_id)

        camera_config = self.config.cameras.get(room)
        if not camera_config:
            print(json.dumps({"error": f"Unknown room: {room}"}), file=sys.stderr)
            sys.exit(1)

        # Get current timestamp
        now = datetime.now()

        # Get snapshot
        snapshot = self.snapshot_service.get_snapshot(
            room_name=room,
            camera_entity_id=camera_config.entity_id,
            cooldown_minutes=self.config.cooldown_minutes,
            manual=manual
        )

        # Get home state
        home_state = self.ha_service.get_home_state()

        # Get motion state (fallback only)
        motion_detected = self.ha_service.is_motion_detected(camera_config.motion_sensor) if camera_config.motion_sensor else None

        # OCCUPANCY LOGIC:
        # Motion sensors only detect movement, not presence — someone sitting still won't trigger
        #
        # - Motion ON → definitely occupied (verified)
        # - Motion OFF + snapshot → unverified, Claude checks visually
        # - Motion OFF + no snapshot (cooldown) → use LAST KNOWN state as best guess
        #
        # Cooldown is from UI settings (config.cooldown_minutes)

        # Get last known state from storage
        last_known_state = self.state_manager.get_room_state(room)
        last_known_occupied = last_known_state.get('occupancy', {}).get('current') if last_known_state else None

        # Detect arrival: distinguish HOME arrival (confidence bypass) from ROOM arrival (normal rules)
        # Home arrival: no motion in ANY room for 30+ min → user was away
        # Room arrival: no motion in THIS room for 10+ min → user entered room
        # Only home arrival bypasses the confidence threshold (Rule 0 in should_stay_silent)
        is_arrival = False
        if motion_detected:
            # Check this room's last motion
            last_motion_str = last_known_state.get('last_motion_at') if last_known_state else None
            is_room_arrival = False
            if last_motion_str:
                try:
                    minutes_since_motion = (now - datetime.fromisoformat(last_motion_str)).total_seconds() / 60
                    is_room_arrival = minutes_since_motion >= 10
                except (ValueError, TypeError):
                    is_room_arrival = True
            else:
                is_room_arrival = True  # No previous motion record
            if not last_known_occupied:
                is_room_arrival = True  # Room was empty

            # Check ALL rooms' last motion to detect home-level arrival
            # If no room had motion for 30+ min, this is a home arrival (was away)
            HOME_AWAY_THRESHOLD_MIN = 30
            full_state = self.state_manager.read_state()
            all_rooms = full_state.get('rooms', {})
            most_recent_any_motion = None
            for r_name, r_state in all_rooms.items():
                r_motion = r_state.get('last_motion_at')
                if r_motion:
                    try:
                        r_dt = datetime.fromisoformat(r_motion)
                        if most_recent_any_motion is None or r_dt > most_recent_any_motion:
                            most_recent_any_motion = r_dt
                    except (ValueError, TypeError):
                        pass

            is_home_arrival = False
            if most_recent_any_motion:
                minutes_since_any = (now - most_recent_any_motion).total_seconds() / 60
                is_home_arrival = minutes_since_any >= HOME_AWAY_THRESHOLD_MIN
            elif is_room_arrival:
                is_home_arrival = True  # No motion records at all = first ever

            # Only home arrival gets the Rule 0 confidence bypass
            # Room arrival follows normal confidence rules
            is_arrival = is_home_arrival

            # Detect settling period via TransitionDetector
            try:
                transition = TransitionDetector(self.state_manager).detect(room, motion_detected)
                is_settling = transition.settling_period_active
                # If TransitionDetector detects home arrival, use it (may override above)
                if transition.is_home_arrival:
                    is_arrival = True
            except Exception:
                is_settling = False

        if motion_detected:
            # Motion ON → definitely occupied
            person_detected = True
            occupancy_verified = True
            occupancy_source = "motion_on"
        elif snapshot:
            # Agent will verify occupancy visually from the snapshot
            person_detected = last_known_occupied if last_known_occupied is not None else False
            occupancy_verified = False
            occupancy_source = "snapshot_pending"
        else:
            # Motion OFF and no snapshot (cooldown) → trust last known state
            person_detected = last_known_occupied if last_known_occupied is not None else False
            occupancy_verified = False
            occupancy_source = "last_known"

        # Only update state if we have verified info (motion ON)
        # Don't overwrite with unverified guesses
        if occupancy_verified and person_detected is not None:
            self.state_manager.update_occupancy(room, person_detected)

        # Update last check timestamp for this room
        self.state_manager.update_room(room, {
            "last_check": now.isoformat()
        })

        # Get room lights
        from handlers.empty_room_handler import ROOM_LIGHTS_MAP
        room_lights = self.ha_service.get_room_lights(room, ROOM_LIGHTS_MAP)

        # Get temporal context
        hour = now.hour
        time_of_day = (
            "morning" if 6 <= hour < 10 else
            "daytime" if 10 <= hour < 17 else
            "evening" if 17 <= hour < 22 else
            "night"
        )

        # Get room state and observations
        room_state = self.state_manager.get_room_state(room)
        recent_observations = self.state_manager.get_recent_observations(room, hours=2)
        occupancy_duration = self.state_manager.get_occupancy_duration(room)

        # Build room observations for life context inference
        import life_context
        room_observations = {
            room: {
                "person_detected": person_detected,
                "activity_duration": occupancy_duration or 0,
            }
        }

        # Infer life context
        context_inference = life_context.infer_context(room_observations, home_state)

        # Get suggestions based on inferred context + current home state
        capabilities = life_context.get_capabilities()
        speaker_entity_ids = life_context.get_speaker_entity_ids(capabilities)
        # Pass home_state so suggestions are state-aware (skip music if playing, etc.)
        suggestion_home_state = {
            "music_playing": any(e in speaker_entity_ids for e in home_state.get("media_playing", [])),
            "lights_on": room_lights,  # Room-specific lights, not whole-house
            "media_playing": home_state.get("media_playing", [])
        }
        suggestions = life_context.get_suggestions(
            context_inference, capabilities, home_state=suggestion_home_state
        )

        # Filter out suggestions already sent recently (prevents duplicate messages)
        recently_sent = life_context.get_recently_sent_suggestions(hours=2)
        sent_actions = {entry.get("suggestion", {}).get("action") for entry in recently_sent}
        suggestions_not_yet_sent = [s for s in suggestions if s.get("action") not in sent_actions]

        # If some were filtered, note it
        filtered_count = len(suggestions) - len(suggestions_not_yet_sent)
        suggestions = suggestions_not_yet_sent

        # Get learned patterns (file-based legacy)
        patterns = life_context.get_patterns()
        learned_patterns = patterns.get("learned_patterns", {}).get("patterns", {})

        # Get behavioral predictions from PatternAnalyzer (data-driven)
        behavioral_predictions = []
        try:
            from services.pattern_analyzer import PatternAnalyzer
            analyzer = PatternAnalyzer()

            current_context = {
                "hour": hour,
                "is_weekend": now.weekday() >= 5,
                "recent_actions": []  # Could populate from recent observations
            }

            predictions = analyzer.get_predictions(current_context)
            for pred in predictions:
                behavioral_predictions.append({
                    "what": f"{pred['entity_id']} → {pred['predicted_state']}",
                    "confidence": pred["confidence"],
                    "reason": pred["reason"],
                    "source": pred.get("source", "learned")
                })
        except Exception:
            pass  # PatternAnalyzer not ready or no data yet

        # Get recent decision log
        recent_decisions = self.state_manager.get_decision_log(limit=5)
        last_decision = recent_decisions[0] if recent_decisions else None

        # Determine silence logic
        should_be_silent, silence_reason = life_context.should_stay_silent(
            context_inference,
            suggestions,
            recent_decisions,
            confidence_threshold=self.config.confidence_threshold,
            is_arrival=is_arrival,
            is_settling=is_settling if motion_detected else False
        )

        # Build activity timeline from recent observations
        activity_timeline = []
        for obs in recent_observations:
            activity_timeline.append({
                "timestamp": obs.get("timestamp", ""),
                "activity": obs.get("activity", obs.get("summary", "unknown"))
            })

        # Extract context-specific patterns
        context_patterns = {}
        ctx_name = context_inference.get("context", "unknown")
        for pattern_key, pattern_data in learned_patterns.items():
            if pattern_key.startswith(f"{ctx_name}+"):
                context_patterns[pattern_key] = pattern_data

        # Build enriched context payload (Phase 3.1)
        context = {
            "trace_id": trace_id,
            "room": room,
            "snapshot": snapshot,

            "vision_instructions": "Read the snapshot image above to understand what's happening. Use the needs taxonomy (comfort, entertainment, background_entertainment, cleanliness, focus, transition, security, efficiency, ambiance, quiet, hospitality) to reason about which suggestion fits best." if snapshot else None,

            "temporal": {
                "time": now.strftime("%I:%M %p"),
                "time_of_day": time_of_day,
                "day_of_week": now.strftime("%A"),
                "hour": hour,
                "is_weekend": now.weekday() >= 5
            },

            "inferred_context": {
                "context": context_inference.get("context", "unknown"),
                "confidence": context_inference.get("confidence", 0),
                "signals": context_inference.get("signals", []),
                "previous_context": context_inference.get("previous_context"),
                "duration_minutes": occupancy_duration or 0,
                "typical_duration_minutes": None
            },

            "room_state": {
                "occupancy": "occupied" if person_detected else "empty",
                "occupancy_verified": occupancy_verified,
                "occupancy_source": occupancy_source,
                "occupancy_note": self._occupancy_note(occupancy_source, snapshot),
                "motion_sensor_says": "motion" if motion_detected else "no_motion",
                "occupancy_duration_minutes": occupancy_duration or 0,
                "lights_on": room_lights,
                "recent_activity": activity_timeline[:5]
            },

            "home_state": {
                "lights_on": home_state.get("lights_on", []),
                "lights_off": home_state.get("lights_off", []),
                "music_playing": any(e in speaker_entity_ids for e in home_state.get("media_playing", [])),
                "media_players": home_state.get("media_playing", [])
            },

            "learned_patterns": {
                "patterns": learned_patterns,
                "context_specific": context_patterns
            },

            "behavioral_predictions": {
                "summary": f"Based on {len(behavioral_predictions)} learned patterns for this time/context",
                "predictions": behavioral_predictions[:5],  # Top 5 most confident
                "insight": self._generate_prediction_insight(behavioral_predictions, room, person_detected)
            } if behavioral_predictions else None,

            "external_context": self._get_external_context(home_state),

            "suggestions": suggestions,

            # Raw home capabilities - Claude reasons about what's relevant
            # This is the live home inventory from capabilities.json
            "capabilities": capabilities,

            "decision_context": {
                "should_speak": not should_be_silent,
                "silence_reason": silence_reason if should_be_silent else None,
                "suggestions_filtered": filtered_count,
                "filtered_note": f"{filtered_count} suggestions already sent recently" if filtered_count > 0 else None,
                "last_decision_time": last_decision.get("timestamp") if last_decision else None,
                "last_decision": last_decision.get("decision") if last_decision else None,
                "quiet_mode": self.config.quiet_mode,
                "auto_actions_enabled": self.config.auto_actions.enabled,
                "recommended_message": self._get_recommended_message(suggestions, should_be_silent),
                "recommended_action": suggestions[0].get("action") if suggestions and not should_be_silent else None
            },

            "message_generation_context": self._build_message_generation_context(now, hour)
        }

        # Log this decision to the audit trail (Phase 3.4)
        trigger = "manual" if manual else "auto_check"
        decision_entry = {
            "trace_id": trace_id,
            "timestamp": now.isoformat(),
            "room": room,
            "trigger": trigger,
            "context_inferred": context_inference.get("context", "unknown"),
            "confidence": context_inference.get("confidence", 0),
            "suggestions_generated": len(suggestions),
            "suggestions_filtered": filtered_count,
            "decision": "should_speak" if not should_be_silent else "silent",
            "reason": silence_reason if should_be_silent else "Context suggests speaking",
            "suggestions_offered": [s.get("action") for s in suggestions] if suggestions else []
        }

        self.state_manager.log_decision(decision_entry)

        # Save last inferred context to state for learning loop
        # The 'sent' command uses this to associate feedback with the correct context
        self.state_manager.update_room(room, {
            "last_context": {
                "inferred": context_inference.get("context", "unknown"),
                "confidence": context_inference.get("confidence", 0),
                "timestamp": now.isoformat()
            }
        })

        # Record observation to replace pending "analyzing..." state
        # This gives the UI immediate feedback about what was detected
        occupancy_str = "occupied" if person_detected else "empty"
        inferred_ctx = context_inference.get("context", "unknown")
        observation = {
            "activity": f"{inferred_ctx} - {occupancy_str}",
            "summary": f"Room {occupancy_str}, context: {inferred_ctx} (confidence: {context_inference.get('confidence', 0):.0%})",
            "pending": False  # Explicitly mark as not pending
        }
        self.state_manager.record_observation(room, observation)

        trace_logger.info(
            "context_decision",
            room=room,
            context=context_inference.get("context"),
            confidence=context_inference.get("confidence"),
            decision=decision_entry["decision"],
            suggestions_count=len(suggestions),
            trigger=trigger
        )

        # Print concise action directive BEFORE JSON so the agent sees it first
        recommended_msg = context.get("decision_context", {}).get("recommended_message")
        should_speak = context.get("decision_context", {}).get("should_speak", False)

        if should_speak and recommended_msg and suggestions:
            best_suggestion = suggestions[0]
            print("=" * 60)
            print("ACTION REQUIRED: SEND MESSAGE TO USER")
            print(f"Room: {room}")
            print(f"Message: {recommended_msg}")
            print(f"Action: {best_suggestion.get('action', 'unknown')}")
            print(f"Priority: {best_suggestion.get('priority', 'medium')}")
            print()
            sent_json = json.dumps({
                "action": best_suggestion.get("action"),
                "type": best_suggestion.get("type", "comfort")
            })
            print(f'After sending, record: jarvis.py sent {room} \'{sent_json}\' "{recommended_msg}"')
            print("=" * 60)
            print()
        elif should_speak and not suggestions:
            print("=" * 60)
            print(f"CONTEXT: Room {room} - should speak but no actionable suggestions.")
            print("You may compose a brief, friendly observation if appropriate.")
            print("=" * 60)
            print()
        else:
            print("=" * 60)
            silence_reason = context.get("decision_context", {}).get("silence_reason", "unknown")
            print(f"NO ACTION NEEDED: {silence_reason}")
            print("=" * 60)
            print()

        print(json.dumps(context, indent=2))

    def cmd_poll(self):
        """Poll occupancy and detect transitions."""
        if not self.config.enabled:
            print(json.dumps({"polled": False, "reason": "jarvis disabled"}))
            sys.exit(0)

        if not self.config.active_hours.is_active(datetime.now().hour):
            print(json.dumps({"polled": False, "reason": "outside active hours"}))
            sys.exit(0)

        # Process ignored suggestions (soft negative signals for fatigue tracking)
        try:
            from services.fatigue_tracker import process_ignored_suggestions
            process_ignored_suggestions()
        except ImportError:
            pass

        # Check HA health and notify if down
        try:
            health = self.ha_service.check_health_with_tracking()
            ha_notification = self.ha_service.should_notify_ha_down()
            if ha_notification:
                print(json.dumps({"ha_alert": True, "message": ha_notification}))
        except Exception:
            pass

        result = self.occupancy_service.poll_occupancy()
        print(json.dumps(result, indent=2))

        # Run periodic maintenance (snapshot cleanup, DB pruning)
        self._run_maintenance()

    def _run_maintenance(self):
        """Run periodic data cleanup — snapshots older than 1 day, events older than 90 days."""
        try:
            self.snapshot_service.cleanup_old_snapshots(days=1)
        except Exception as e:
            logger.error("maintenance_snapshot_cleanup_failed", error=str(e))

        try:
            from services.event_collector import EventCollector
            collector = EventCollector()
            collector.prune_old_events(days=90)
        except Exception as e:
            logger.error("maintenance_event_prune_failed", error=str(e))

    def cmd_verify_empty(self, args):
        """
        Get snapshot for visual verification before suggesting lights-off.
        
        Used to double-check motion sensor before acting on "empty" state.
        Respects cooldown to limit vision API costs.
        
        Returns:
            JSON with snapshot_path (for vision analysis), sensor state, and lights info.
            If snapshot_path is null, cooldown is active - don't suggest lights off.
        """
        if len(args) < 3:
            print("Usage: jarvis.py verify-empty <room>", file=sys.stderr)
            sys.exit(1)

        room = args[2]
        camera_config = self.config.cameras.get(room)
        
        if not camera_config:
            print(json.dumps({"error": f"Room '{room}' not found in config"}))
            sys.exit(1)

        # Get current sensor state
        person_detected = None
        if camera_config.motion_sensor:
            person_detected = self.ha_service.is_motion_detected(camera_config.motion_sensor)

        # Update state with current occupancy
        if person_detected is not None:
            self.state_manager.update_occupancy(room, person_detected)

        # Take snapshot (respects cooldown - limits vision API calls)
        snapshot_path = self.snapshot_service.get_snapshot(
            room_name=room,
            camera_entity_id=camera_config.entity_id,
            cooldown_minutes=self.config.cooldown_minutes,
            manual=False  # Respect cooldown
        )

        # Update last check timestamp
        self.state_manager.update_room(room, {
            "last_check": datetime.now().isoformat()
        })

        # Get lights that would be affected
        from handlers.empty_room_handler import ROOM_LIGHTS_MAP
        lights_on = self.ha_service.get_room_lights(room, ROOM_LIGHTS_MAP)

        result = {
            "room": room,
            "snapshot_path": snapshot_path,
            "sensor_says_empty": person_detected == False,
            "lights_on": lights_on,
            "verification_prompt": f"Is this room actually empty? Check if there are any people visible. The motion sensor says {'no one is there' if person_detected == False else 'someone might be there' if person_detected else 'unknown'}."
        }
        
        print(json.dumps(result, indent=2))

    def cmd_handle_empty(self, args):
        """Handle empty room event."""
        if len(args) < 3:
            print("Usage: jarvis.py handle-empty <room> [--dry-run] [--verified]", file=sys.stderr)
            sys.exit(1)

        room = args[2]
        dry_run = "--dry-run" in args
        skip_verify = "--verified" in args

        result = self.empty_room_handler.handle(room, dry_run=dry_run, skip_verify=skip_verify)
        print(json.dumps(result, indent=2))

    def cmd_handle_occupied(self, args):
        """Handle occupied room event."""
        if len(args) < 3:
            print("Usage: jarvis.py handle-occupied <room>", file=sys.stderr)
            sys.exit(1)

        room = args[2]
        result = self.occupied_room_handler.handle(room)
        print(json.dumps(result, indent=2))

    def cmd_occupancy(self):
        """Get current occupancy for all rooms."""
        occupancy = {}
        for room_name, camera_config in self.config.get_enabled_cameras().items():
            if camera_config.motion_sensor:
                occupancy[room_name] = self.ha_service.is_motion_detected(camera_config.motion_sensor)

        print(json.dumps({"occupancy": occupancy}))

    def cmd_home_state(self):
        """Get current home state."""
        state = self.ha_service.get_home_state()
        print(json.dumps(state, indent=2))

    def cmd_room_lights(self, args):
        """Get lights on in room."""
        if len(args) < 3:
            print("Usage: jarvis.py room-lights <room>", file=sys.stderr)
            sys.exit(1)

        room = args[2]
        from handlers.empty_room_handler import ROOM_LIGHTS_MAP
        lights = self.ha_service.get_room_lights(room, ROOM_LIGHTS_MAP)
        print(json.dumps({"room": room, "lights_on": lights}))

    def cmd_record(self, args):
        """Record observation for a room."""
        if len(args) < 4:
            print("Usage: jarvis.py record <room> <observation_json>", file=sys.stderr)
            print("   or: jarvis.py record <room> <activity> [summary]", file=sys.stderr)
            sys.exit(1)

        room = args[2]

        # Try JSON first, fall back to activity/summary args
        try:
            observation = json.loads(args[3])
        except (json.JSONDecodeError, ValueError):
            activity = args[3]
            summary = args[4] if len(args) > 4 else activity
            observation = {"activity": activity, "summary": summary}

        self.state_manager.record_observation(room, observation)
        print(json.dumps({"recorded": True, "room": room}))

    def cmd_feedback(self, args):
        """
        Record suggestion feedback for pattern learning.

        Usage: jarvis.py feedback <suggestion_json> <accepted|rejected>

        The agent can call this after making a suggestion to record whether
        the user accepted or rejected it, which updates learned patterns.
        """
        if len(args) < 4:
            print("Usage: jarvis.py feedback <suggestion_json> <accepted|rejected>", file=sys.stderr)
            sys.exit(1)

        # Parse suggestion JSON
        try:
            suggestion = json.loads(args[2])
        except json.JSONDecodeError as e:
            print(f"Invalid suggestion JSON: {e}", file=sys.stderr)
            sys.exit(1)

        # Parse acceptance
        accepted_str = args[3].lower()
        accepted = accepted_str in ["accepted", "yes", "true", "1"]

        # Record the feedback
        life_context.record_suggestion_response(suggestion, accepted)

        logger.info(
            "feedback_recorded",
            suggestion_type=suggestion.get("type"),
            action=suggestion.get("action"),
            accepted=accepted
        )

        print(json.dumps({
            "recorded": True,
            "suggestion": suggestion.get("action"),
            "accepted": accepted
        }))

    def cmd_sent(self, args):
        """
        Record that a suggestion was SENT to the user.

        Usage: jarvis.py sent <room> <suggestion_json> [message_text]

        Call this AFTER using the message tool to send a suggestion.
        This prevents duplicate suggestions from being sent.

        Example:
            jarvis.py sent living_room '{"action":"play_morning_music","type":"ambiance"}' "Morning! Want some music?"
        """
        if len(args) < 4:
            print("Usage: jarvis.py sent <room> <suggestion_json> [message_text]", file=sys.stderr)
            sys.exit(1)

        room = args[2]

        # Parse suggestion JSON
        try:
            suggestion = json.loads(args[3])
        except json.JSONDecodeError as e:
            print(f"Invalid suggestion JSON: {e}", file=sys.stderr)
            sys.exit(1)

        # Optional message text
        message_text = args[4] if len(args) > 4 else None

        # Record the sent suggestion
        life_context.record_sent_suggestion(room, suggestion, message_text)

        # Write to daily activity log (for main openclaw context sharing)
        self.activity_log.log_message(
            room=room,
            message=message_text or "",
            action=suggestion.get("action"),
            context=suggestion.get("type")
        )

        logger.info(
            "suggestion_sent_recorded",
            room=room,
            action=suggestion.get("action"),
            message=message_text[:50] if message_text else None
        )

        print(json.dumps({
            "recorded": True,
            "room": room,
            "suggestion": suggestion.get("action"),
            "awaiting_feedback": True
        }))

    def cmd_respond(self, args):
        """
        Process user's yes/no response to a recent suggestion.

        Usage: jarvis.py respond <user_response>

        The user_response should be something like "yes", "no", "sure", "nah".
        This will find the most recent suggestion awaiting feedback and record
        the user's response for learning.

        Returns JSON with the processed feedback or null if no suggestion awaiting.
        """
        if len(args) < 3:
            print("Usage: jarvis.py respond <user_response>", file=sys.stderr)
            sys.exit(1)

        response = " ".join(args[2:])  # Join in case response has spaces

        result = life_context.process_user_feedback(response)

        if result:
            logger.info(
                "user_feedback_processed",
                suggestion=result.get("suggestion"),
                accepted=result.get("accepted"),
                room=result.get("room")
            )
            print(json.dumps(result))
        else:
            print(json.dumps({"processed": False, "reason": "No suggestion awaiting feedback or couldn't parse response"}))

    def cmd_decisions(self, args):
        """
        View recent decision audit trail.

        Usage: jarvis.py decisions [--limit N] [--room ROOM]
        """
        limit = 20
        room_filter = None

        # Parse arguments
        i = 2
        while i < len(args):
            if args[i] == "--limit" and i + 1 < len(args):
                try:
                    limit = int(args[i + 1])
                    i += 2
                except ValueError:
                    print("Invalid limit value", file=sys.stderr)
                    sys.exit(1)
            elif args[i] == "--room" and i + 1 < len(args):
                room_filter = args[i + 1]
                i += 2
            else:
                i += 1

        # Get decision log
        decisions = self.state_manager.get_decision_log(limit=limit)

        # Filter by room if specified
        if room_filter:
            decisions = [d for d in decisions if d.get("room") == room_filter]

        print(json.dumps({
            "count": len(decisions),
            "decisions": decisions
        }, indent=2))

    def cmd_patterns(self, args):
        """
        View learned behavior patterns.

        Usage: jarvis.py patterns [--analyze] [--predict]
        """
        try:
            from services.pattern_analyzer import PatternAnalyzer
            analyzer = PatternAnalyzer()

            if "--analyze" in args:
                # Run full analysis and save patterns
                count = analyzer.save_patterns_to_db()
                print(json.dumps({
                    "analyzed": True,
                    "patterns_saved": count
                }, indent=2))
            elif "--predict" in args:
                # Get predictions for current time
                predictions = analyzer.get_predictions({})
                print(json.dumps({
                    "predictions": predictions
                }, indent=2))
            else:
                # Show pattern summary
                summary = analyzer.get_summary()
                time_patterns = analyzer.analyze_time_patterns()
                sequence_patterns = analyzer.analyze_sequence_patterns()

                print(json.dumps({
                    "summary": summary,
                    "time_patterns": time_patterns.get("time_patterns", [])[:10],
                    "sequence_patterns": sequence_patterns.get("sequence_patterns", [])[:10]
                }, indent=2))

        except Exception as e:
            print(json.dumps({
                "error": str(e),
                "hint": "Pattern analyzer requires event collection. Is the collector running?"
            }, indent=2))

    def cmd_events(self, args):
        """
        View collected HA events.

        Usage: jarvis.py events [--hours N] [--entity FILTER]
        """
        try:
            from services.event_collector import EventCollector
            collector = EventCollector()

            hours = 24
            entity_filter = None

            # Parse arguments
            i = 2
            while i < len(args):
                if args[i] == "--hours" and i + 1 < len(args):
                    try:
                        hours = int(args[i + 1])
                        i += 2
                    except ValueError:
                        i += 1
                elif args[i] == "--entity" and i + 1 < len(args):
                    entity_filter = args[i + 1]
                    i += 2
                else:
                    i += 1

            # Get stats first
            stats = collector.get_stats()

            # Get recent events
            events = collector.get_recent_events(hours=hours, entity_filter=entity_filter)

            print(json.dumps({
                "stats": stats,
                "events_in_window": len(events),
                "recent_events": events[:50]
            }, indent=2))

        except Exception as e:
            print(json.dumps({
                "error": str(e),
                "hint": "Event collector might not be running. Check launchctl list | grep jarvis-collector"
            }, indent=2))

    def cmd_activity(self):
        """Show today's Jarvis activity log.

        Usage: jarvis.py activity

        Returns JSON array of messages Jarvis sent today, for context sharing
        with the main openclaw conversation.
        """
        entries = self.activity_log.get_today()
        # Filter to messages only (skip silence entries for brevity)
        messages = [e for e in entries if e.get("type") == "message"]
        print(json.dumps({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "message_count": len(messages),
            "messages": messages
        }, indent=2))

    def cmd_cleanup(self):
        """Delete old snapshots and activity logs."""
        self.snapshot_service.cleanup_old_snapshots(days=1)
        removed_logs = self.activity_log.cleanup(keep_days=1)
        print(json.dumps({"cleaned": True, "activity_logs_removed": removed_logs}))

    def cmd_setup(self):
        """Self-register with OpenClaw."""
        # Load hooks definition
        try:
            with open(HOOKS_FILE) as f:
                hooks_def = json.load(f)
        except Exception as e:
            print(json.dumps({"success": False, "error": f"Failed to load hooks.json: {e}"}))
            sys.exit(1)

        # Note: We no longer add channel to mappings - the agent uses the message tool
        # directly to send to Telegram (target: 8208227354). This prevents accidental
        # delivery of agent text output that isn't meant for the user.

        # Patch hooks into openclaw.json
        openclaw_config_path = Path.home() / ".openclaw" / "openclaw.json"

        try:
            with open(openclaw_config_path) as f:
                openclaw_config = json.load(f)

            # Ensure hooks section exists
            if "hooks" not in openclaw_config:
                openclaw_config["hooks"] = {"enabled": True, "mappings": []}

            openclaw_config["hooks"]["enabled"] = True

            # Merge mappings (replace existing jarvis hooks)
            existing_mappings = [m for m in openclaw_config["hooks"].get("mappings", [])
                               if not m.get("id", "").startswith("jarvis-")]

            openclaw_config["hooks"]["mappings"] = existing_mappings + hooks_def["hooks"]["mappings"]

            # Write back
            with open(openclaw_config_path, "w") as f:
                json.dump(openclaw_config, f, indent=2)

            logger.info("setup_hooks_registered")

            print(json.dumps({
                "success": True,
                "hooks": {"registered": True},
                "message": "Jarvis hooks registered. Restart OpenClaw gateway to apply."
            }, indent=2))

        except Exception as e:
            logger.error("setup_failed", error=str(e), exc_info=True)
            print(json.dumps({"success": False, "error": str(e)}))
            sys.exit(1)


# Module-level functions for jarvis_server.py compatibility
def get_status():
    """
    Get full Jarvis status for UI/API.

    Returns status dict compatible with jarvis_server.py and UI.
    """
    config = JarvisConfig.load(CONFIG_FILE)
    state_manager = StateManager(STATE_FILE)
    state = state_manager.read_state()

    # Build room states with occupancy info
    room_states = {}
    all_observations = []

    for room_name, camera_config in config.cameras.items():
        if not camera_config.enabled:
            continue

        room_state = state_manager.get_room_state(room_name) or {}

        # Extract occupancy data (schema v2 - canonical)
        occupancy_data = room_state.get("occupancy", {})
        last_occupancy = occupancy_data.get("current")
        occupancy_changed_at = occupancy_data.get("changed_at")

        # Fall back to legacy fields if canonical is None
        if last_occupancy is None:
            last_occupancy = room_state.get("lastOccupancy")
        if occupancy_changed_at is None:
            occupancy_changed_at = room_state.get("occupancyChangedAt")

        # Get last check time (prefer canonical, fall back to legacy)
        last_check = room_state.get("last_check") or room_state.get("lastCheck")

        # Get last motion time for vacancy verification context
        last_motion_at = room_state.get("last_motion_at")

        room_states[room_name] = {
            "lastCheck": last_check,
            "lastOccupancy": last_occupancy,
            "occupancyChangedAt": occupancy_changed_at,
            "lastMotionAt": last_motion_at
        }

        # Collect observations from canonical field
        observations = list(room_state.get("recent_observations", []))

        # Also check legacy field and merge if present
        legacy_observations = room_state.get("recentObservations", [])
        if legacy_observations:
            existing_timestamps = {obs.get('timestamp') for obs in observations}
            for legacy_obs in legacy_observations:
                if legacy_obs.get('timestamp') not in existing_timestamps:
                    observations.append(legacy_obs)

        # Filter out pending observations if we have real observations
        real_observations = [obs for obs in observations if not obs.get('pending')]
        if real_observations:
            observations = real_observations

        for obs in observations:
            all_observations.append({"room": room_name, **obs})

    # Sort observations by timestamp descending
    all_observations.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    return {
        "enabled": config.enabled,
        "activeHours": config.active_hours.is_active(datetime.now().hour),
        "activeHoursConfig": {
            "start": config.active_hours.start,
            "end": config.active_hours.end
        },
        "checkInterval": config.check_interval_minutes,
        "cooldown": config.cooldown_minutes,
        "motionCooldown": config.motion_cooldown_minutes,
        "motionAware": config.motion_aware,
        "instantAlerts": config.instant_alerts,
        "quietMode": config.quiet_mode,
        "confidenceThreshold": config.confidence_threshold,
        "autoActions": {"enabled": config.auto_actions.enabled},
        "lastPoll": state.get("last_poll"),
        "cameras": list(config.get_enabled_cameras().keys()),
        "roomStates": room_states,
        "recentObservations": all_observations[:10]
    }


def get_config():
    """Get config dict (legacy compat)."""
    config = JarvisConfig.load(CONFIG_FILE)
    return {
        "enabled": config.enabled,
        "checkIntervalMinutes": config.check_interval_minutes,
        "cooldownMinutes": config.cooldown_minutes,
        "motionCooldownMinutes": config.motion_cooldown_minutes,
        "motionAware": config.motion_aware,
        "instantAlerts": config.instant_alerts,
        "quietMode": config.quiet_mode,
        "confidenceThreshold": config.confidence_threshold,
        "autoActions": {"enabled": config.auto_actions.enabled},
        "activeHours": {
            "start": config.active_hours.start,
            "end": config.active_hours.end
        },
        "cameras": {
            name: {
                "entity_id": cam.entity_id,
                "enabled": cam.enabled,
                "motionSensor": cam.motion_sensor,
            }
            for name, cam in config.cameras.items()
        }
    }


def save_json(path, data):
    """Save JSON file (legacy compat)."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def is_enabled():
    """Check if Jarvis is enabled (legacy compat)."""
    config = JarvisConfig.load(CONFIG_FILE)
    return config.enabled


def is_active_hours():
    """Check if currently in active hours (legacy compat)."""
    config = JarvisConfig.load(CONFIG_FILE)
    return config.active_hours.is_active(datetime.now().hour)


def should_check_room(room_name, trigger="scheduled"):
    """Legacy compat for checking if room should be checked."""
    try:
        from services.occupancy_service import OccupancyService
        from services.ha_service import HAService

        config = JarvisConfig.load(CONFIG_FILE)
        state_manager = StateManager(STATE_FILE)
        ha_service = HAService()  # Gets HA creds from env/config
        occupancy_service = OccupancyService(config, state_manager, ha_service)

        return occupancy_service.should_check_room(room_name, trigger=trigger)
    except Exception as e:
        return {"should_check": False, "reason": str(e), "motion_state": None}


def record_observation(room_name, observation):
    """Legacy compat for recording observations."""
    state_manager = StateManager(STATE_FILE)
    if "timestamp" not in observation:
        observation["timestamp"] = datetime.now().isoformat()
    state_manager.record_observation(room_name, observation)


def main():
    """Main entry point."""
    cli = JarvisCLI()
    cli.run(sys.argv)


if __name__ == "__main__":
    main()
