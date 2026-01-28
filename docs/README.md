# Jarvis Mode Documentation

Complete documentation for the Jarvis Mode home intelligence system.

## Getting Started

- [Main README](../README.md) - Overview, setup, and quick start
- [SKILL.md](../SKILL.md) - Skill definition and capabilities

## User Guides

- [WHATS_NEW.md](WHATS_NEW.md) - What's new in the enhanced system (Phases 1-4)
- [TESTING_GUIDE.md](TESTING_GUIDE.md) - Comprehensive testing guide for all features

## Implementation Documentation

### Phase Completion Reports

- [PHASE_1_COMPLETE.md](PHASE_1_COMPLETE.md) - Foundation (State, Logging, Config, Testing)
- [PHASE4_COMPLETION.md](PHASE4_COMPLETION.md) - Observability (Metrics, Health Checks)

### Technical Deep Dives

- [STATE_ACCURACY_FIX.md](STATE_ACCURACY_FIX.md) - How we fixed UI monitoring accuracy
- [TESTING.md](TESTING.md) - Testing infrastructure and strategies

## Testing

- [tests/README.md](../tests/README.md) - How to run tests and test organization

## Architecture

### Core Components

**Phase 1: Foundation**
- State Management: Thread-safe operations with file locking
- Configuration: Type-safe config with validation
- Logging: Structured JSON logging with trace IDs
- Testing: Comprehensive test suite (63+ tests)

**Phase 2: Intelligence**
- Context Inference: Understands what's happening in each room
- Pattern Learning: Learns from user behavior over time
- Silence Logic: Knows when to speak vs stay silent
- Temporal Reasoning: Time-aware context

**Phase 3: Integration**
- Enriched Agent Context: Full intelligence in agent payload
- Proactive Suggestions: Based on learned patterns
- Decision Audit Trail: Every decision logged with reasoning

**Phase 4: Observability**
- Structured Logging: JSON logs for analysis
- Decision Tracking: See why agent spoke or stayed silent
- Cost Tracking: Monitor API usage
- Metrics Collection: System performance and quality

## Key Features

### Intelligence

- **Context Inference**: "cooking", "winding_down", "working" (not just "motion detected")
- **Pattern Learning**: Learns what you accept/reject over time
- **Silence Logic**: 70-80% of checks → NO_REPLY (appropriate restraint)
- **Proactive Suggestions**: Anticipates needs based on learned patterns

### Integration

- **Clawdbot Hooks**: Full integration with enhanced prompts
- **Voice Commands**: "Hey Jarvis" wake word detection
- **Web UI**: Real-time monitoring and control
- **Home Assistant**: Seamless integration

### Reliability

- **Thread-Safe**: File locking prevents race conditions
- **Well-Tested**: 87%+ coverage on critical modules
- **Properly Logged**: All operations tracked with context
- **Atomic Operations**: State updates never corrupt

## Quick Links

### Testing
```bash
# Run intelligence test suite
./test_intelligence.sh

# Run pytest
python3 -m pytest tests/ -v

# With coverage
python3 -m pytest tests/ --cov=scripts --cov-report=term-missing
```

### Monitoring
```bash
# Check system status
python3 scripts/jarvis.py status

# View recent decisions
python3 scripts/jarvis.py decisions --limit 10

# Check logs
tail -f logs/jarvis.log | jq '.'

# View learned patterns
cat patterns.json | jq '.learned_patterns.patterns'
```

### Development
```bash
# Test context inference
python3 scripts/jarvis.py context kitchen --manual

# Poll for transitions
python3 scripts/jarvis.py poll

# Record observation
python3 scripts/jarvis.py record kitchen '{"activity":"test"}'

# View decision audit trail
python3 scripts/jarvis.py decisions --room kitchen --limit 5
```

## Documentation Standards

All documentation follows these principles:

- **Accurate**: Reflects actual implementation
- **Complete**: Covers all major features
- **Practical**: Includes examples and commands
- **Up-to-date**: Updated with each phase completion

## Contributing

When adding features, update relevant documentation:

1. Update main [README.md](../README.md) if user-facing
2. Add technical details to appropriate doc in `docs/`
3. Update [WHATS_NEW.md](WHATS_NEW.md) for significant changes
4. Add test documentation to [tests/README.md](../tests/README.md)

## Support

For issues or questions:
- Check [TESTING_GUIDE.md](TESTING_GUIDE.md) for common issues
- Review [STATE_ACCURACY_FIX.md](STATE_ACCURACY_FIX.md) for troubleshooting patterns
- See phase completion docs for implementation details

---

**Your Jarvis is truly magical!** 🤖✨
