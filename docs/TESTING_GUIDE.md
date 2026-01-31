# Testing Guide: Full Jarvis Mode System

**Status**: Phase 1-4 Complete ✅
**Date**: 2026-01-27

This guide walks you through testing your newly enhanced Jarvis Mode system with all intelligence, integration, and observability features.

## Quick Start: Test the Full System

### 1. Verify System Health

```bash
cd ~/clawd/skills/jarvis-mode

# Check status
python3 scripts/jarvis.py status

# Verify all services are working
python3 -m pytest tests/ -v
```

### 2. Test Intelligence Features (Phase 2)

#### Test Context Inference

```bash
# Get enriched context for a room
python3 scripts/jarvis.py context kitchen --manual

# This returns a rich JSON payload with:
# - inferred_context: What's happening (cooking, winding_down, etc.)
# - confidence: How sure the system is
# - signals: What led to this inference
# - suggestions: Ranked, actionable suggestions
# - decision_context: Whether agent should speak (with reasoning)
# - learned_patterns: What you typically prefer in this situation
```

**What to Look For**:
- ✅ Context is inferred (not just "unknown")
- ✅ Confidence > 0.5 when there's clear activity
- ✅ Signals list shows what led to inference
- ✅ Suggestions are contextually relevant

#### Test Silence Logic

The system should **NOT** speak for most checks. Silence is golden!

```bash
# Run context check multiple times in a row
python3 scripts/jarvis.py context kitchen --manual
python3 scripts/jarvis.py context kitchen --manual
python3 scripts/jarvis.py context kitchen --manual
```

**Expected**: `decision_context.should_speak` should be `false` for most checks with reasons like:
- "same context, no new suggestions"
- "suggestion recently offered"
- "low confidence"
- "focus context (working, sleeping)"

**Should Speak When**:
- Context just changed (e.g., kitchen → living_room transition)
- New actionable suggestion available
- Efficiency issue (lights on in empty room)
- Unusual pattern detected

#### Test Pattern Learning

```bash
# View current learned patterns
cat patterns.json | jq '.learned_patterns.patterns'

# Record some observations
python3 scripts/jarvis.py record kitchen '{"activity": "at counter", "person_detected": true}'

# After multiple observations over time, patterns should populate
# Check for typical usage times, preference acceptance rates, etc.
```

**Expected After 3-5 Days**:
```json
{
  "cooking+music": {
    "action": "play_background_music",
    "acceptance_rate": 0.75,
    "typical_times": ["18:00", "19:00"],
    "last_offered": "2026-01-27T19:15:00Z"
  }
}
```

#### Test Feedback Loop

```bash
# When agent makes a suggestion, record feedback
python3 scripts/jarvis.py feedback '{"type":"music","action":"play_background_music","context":"cooking"}' accepted

# Check that acceptance rate increased
cat patterns.json | jq '.learned_patterns.patterns."cooking+music".acceptance_rate'
```

### 3. Test Integration with OpenClaw (Phase 3)

#### Verify Hooks Registration

```bash
# Self-register with OpenClaw (if not done already)
python3 scripts/jarvis.py setup

# Check hooks.json is registered
cat ~/.claude/hooks.json | grep jarvis
```

#### Test Motion Event via OpenClaw

**Method 1: Simulate via webhook**
```bash
# Trigger motion event (replace with your setup)
curl -X POST http://localhost:8080/jarvis/motion?room=kitchen
```

**Expected Flow**:
1. OpenClaw receives motion event
2. Runs `jarvis.py context kitchen --manual`
3. Gets enriched context payload
4. Agent analyzes with full intelligence
5. Decides whether to speak based on `decision_context.should_speak`
6. If speaking: References learned patterns naturally

**Example Agent Response (Good)**:
> "Settling in for the evening. Want me to dim the lights and queue up Chill House Radio?"

**Example Agent Response (Good - Silence)**:
> (NO_REPLY - respects silence_reason: "same context, no new suggestions")

#### Test Voice Commands

If you have voice setup:
```bash
# Voice command should work naturally
# Speak: "What time is it?"
# Expected: Agent responds conversationally via Telegram
```

#### Test Poll Mode

```bash
# Poll for room transitions
python3 scripts/jarvis.py poll

# If transitions detected:
# - Check decision log for what happened
python3 scripts/jarvis.py decisions --limit 5
```

### 4. Test Observability (Phase 4)

#### Check Structured Logs

```bash
# View recent logs
tail -f logs/jarvis.log | jq '.'

# Filter for decisions
cat logs/jarvis.log | jq 'select(.operation == "decision_made")'

# Filter for context inferences
cat logs/jarvis.log | jq 'select(.operation == "context_inferred")'

# Find high-cost operations
cat logs/jarvis.log | jq 'select(.cost.vision_tokens > 2000)'
```

#### Check Decision Audit Trail

```bash
# View recent decisions with reasoning
python3 scripts/jarvis.py decisions --limit 10

# View decisions for specific room
python3 scripts/jarvis.py decisions --room kitchen --limit 5
```

**What to Look For**:
```json
{
  "timestamp": "2026-01-27T19:15:00Z",
  "room": "kitchen",
  "trigger": "motion",
  "context_inferred": "cooking",
  "confidence": 0.85,
  "decision": "spoke",
  "reason": "Context transition + high-value suggestion",
  "suggestions_offered": 1,
  "agent_response": "Want some background music while you cook?",
  "cost": {
    "vision_tokens": 1250,
    "output_tokens": 45
  }
}
```

#### Check Metrics

```bash
# If metrics endpoint is available (jarvis_server.py)
curl http://localhost:8081/api/metrics | jq '.'
```

**Expected Metrics**:
- Total decisions (spoke vs silent)
- Silence rate (should be 70-80%)
- Suggestion acceptance rate
- Context inference confidence (avg)
- API costs

### 5. Real-World Scenarios to Test

#### Scenario 1: Morning Routine
1. Walk into kitchen at 7:00 AM
2. Motion triggers context check
3. System infers "morning_routine"
4. Suggests coffee-related actions (if learned)

**Test**:
```bash
python3 scripts/jarvis.py context kitchen --manual
```

**Expected**: Context = "morning_routine", suggestions related to morning preferences

#### Scenario 2: Context Transition
1. Finish cooking in kitchen (20 minutes)
2. Move to living room
3. Motion in living room triggers

**Expected**: Agent notices transition, suggests post-meal actions (dim lights, music)

#### Scenario 3: Efficiency Issue
1. Leave kitchen with lights on
2. Room becomes empty
3. Poll detects transition

**Expected**:
```bash
python3 scripts/jarvis.py handle-empty kitchen
```
Agent suggests turning off lights

#### Scenario 4: Repetition Suppression
1. Agent suggests dimming lights
2. You ignore it
3. 5 minutes later, same situation

**Expected**: Agent stays silent (suggestion recently offered)

#### Scenario 5: Pattern Recognition
Over multiple days:
1. You accept "play music" 4 out of 5 times while cooking
2. Pattern learning updates acceptance_rate to 0.8
3. Next time: Agent proactively suggests music with high priority

### 6. Verify OpenClaw Integration

#### Test Complete Flow

1. **Trigger motion event** (walk into room or simulate)
2. **OpenClaw receives webhook** at `/jarvis/motion?room=kitchen`
3. **Hook executes** from hooks.json
4. **Jarvis returns context** with full intelligence
5. **Agent analyzes** with enriched payload
6. **Agent responds** naturally or NO_REPLY

**Check OpenClaw Logs**:
```bash
# If running openclaw locally
tail -f ~/.claude/logs/openclaw.log | grep jarvis
```

**Verify Prompt Quality**:
- Agent receives full context payload ✅
- Agent references learned patterns ("You usually...") ✅
- Agent respects silence logic ✅
- Agent speaks conversationally (1-2 sentences) ✅
- Agent never dumps debug info ✅

### 7. Performance & Cost Tracking

#### Monitor API Costs

```bash
# Check decision log for costs
python3 scripts/jarvis.py decisions --limit 50 | jq '[.[] | .cost.vision_tokens] | add'

# Expected: ~1000-1500 tokens per snapshot analysis
```

#### Check Response Times

```bash
# Time a context check
time python3 scripts/jarvis.py context kitchen --manual

# Expected: < 3 seconds for snapshot + analysis
```

#### Verify Cooldowns

```bash
# Check snapshot cooldown working
python3 scripts/jarvis.py context kitchen --manual
python3 scripts/jarvis.py context kitchen --manual  # Should reuse cached snapshot

# Check logs for "snapshot_reused"
cat logs/jarvis.log | jq 'select(.operation == "snapshot_reused")'
```

## Common Issues & Troubleshooting

### Issue: Context is always "unknown"

**Diagnosis**:
```bash
# Check life-model.json exists
cat life-model.json | jq '.contexts'

# Check observations are being recorded
python3 scripts/jarvis.py record kitchen '{"activity":"test","person_detected":true}'
cat state.json | jq '.rooms.kitchen.recent_observations'
```

**Fix**: Ensure life-model.json has context definitions

### Issue: Agent always speaks (no silence logic)

**Diagnosis**:
```bash
# Check silence logic is working
python3 scripts/jarvis.py context kitchen --manual | jq '.decision_context'
```

**Expected**: `should_speak` should be `false` most of the time

**Fix**: Check life_context.should_stay_silent() implementation

### Issue: Patterns not populating

**Diagnosis**:
```bash
# Check pattern service is running
cat patterns.json | jq '.learned_patterns'

# Check observations exist
cat state.json | jq '.decision_log | length'
```

**Fix**: Need 5-10 observations before patterns emerge. Record more observations or run pattern analysis manually.

### Issue: OpenClaw not triggering

**Diagnosis**:
```bash
# Check hooks registered
cat ~/.claude/hooks.json | grep jarvis-motion

# Test hook directly
cd ~/clawd/skills/jarvis-mode && python3 scripts/jarvis.py context kitchen --manual
```

**Fix**: Run `python3 scripts/jarvis.py setup` to re-register hooks

### Issue: High API costs

**Diagnosis**:
```bash
# Check decision frequency
python3 scripts/jarvis.py decisions --limit 100 | jq 'length'

# Check if cooldowns are working
python3 scripts/jarvis.py decisions | jq '[.[] | select(.timestamp > "2026-01-27T19:00:00Z")] | length'
```

**Fix**: Increase cooldown_minutes in config.json

## Success Metrics

After 3-5 days of operation, you should see:

### Intelligence Metrics ✅
- Context inference: 80%+ checks result in non-"unknown" context
- Silence rate: 70-80% of checks result in NO_REPLY (appropriate)
- Pattern learning: 5-10 patterns with acceptance_rate > 0.6

### Integration Quality ✅
- Agent references history: "You usually...", "Last time..."
- Agent respects silence logic: Doesn't speak unnecessarily
- Agent speaks naturally: 1-2 sentences, conversational
- Agent is proactive: Suggests before being asked

### Observability ✅
- Logs are structured and parseable
- Decision audit trail is complete
- Cost tracking is accurate
- System recovers from errors gracefully

## What "True Jarvis" Looks Like

**After full integration, you should experience**:

✅ **Observes intelligently**: Understands what you're doing from context
✅ **Anticipates needs**: Learns patterns and suggests proactively
✅ **Speaks appropriately**: Only when helpful, not just to acknowledge
✅ **Learns continuously**: Tracks acceptance, stops offering rejected suggestions
✅ **Acts naturally**: "You usually dim lights at this time"
✅ **Built to last**: Thread-safe, well-tested, properly logged

**Example Interaction**:
```
[7:15 PM - You enter kitchen]
Motion detected → Context inferred: "meal_prep" (confidence: 0.82)
Agent: "Settling in for dinner. Want me to put on some jazz and dim the lights?"
You: "Yes, thanks"
[Feedback recorded → acceptance_rate increases]

[Next evening, 7:10 PM]
Motion detected → Pattern recognized (evening cooking + music accepted 4/5 times)
Agent: "Ready to cook? I'll start the music." [Proactive, based on learned pattern]
```

## Running Tests

```bash
# Full test suite
python3 -m pytest tests/ -v --cov=scripts --cov-report=term-missing

# Test only intelligence features
python3 -m pytest tests/test_context_service.py -v

# Test pattern learning
python3 -m pytest tests/test_pattern_service.py -v

# Integration test
python3 -m pytest tests/ -m integration
```

## Documentation

- [PHASE_1_COMPLETE.md](PHASE_1_COMPLETE.md): Foundation (state, logging, config)
- [tests/README.md](tests/README.md): Testing infrastructure
- [hooks.json](hooks.json): OpenClaw integration
- [Plan Document](~/.claude/plans/ancient-growing-sunrise.md): Full implementation plan

---

**Your Jarvis is now production-ready and truly magical!** 🎉

Test it out, let the patterns learn for a few days, and watch it become more intelligent over time.
