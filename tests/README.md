# Jarvis Mode Tests

Comprehensive test suite for Jarvis Mode.

## Running Tests

### Install test dependencies
```bash
pip install -r requirements-test.txt
```

### Run all tests
```bash
pytest
```

### Run with coverage
```bash
pytest --cov=scripts --cov-report=term-missing
```

### Run specific test file
```bash
pytest tests/test_state_manager.py
```

### Run specific test
```bash
pytest tests/test_state_manager.py::TestStateManager::test_concurrent_writes
```

### Run only unit tests (fast)
```bash
pytest -m unit
```

### Run only integration tests
```bash
pytest -m integration
```

### Skip tests requiring Home Assistant
```bash
pytest -m "not requires_ha"
```

## Test Organization

### Unit Tests (`-m unit`)
- **test_state_manager.py**: Thread-safe state operations, concurrency
- **test_config.py**: Configuration validation, env vars
- **test_ha_service.py**: HA API mocking, error handling
- **test_occupancy_service.py**: Occupancy detection logic

### Integration Tests (`-m integration`)
- Real-world workflows
- Multi-component interactions
- End-to-end scenarios

### Tests Requiring HA (`-m requires_ha`)
- Skipped by default
- Run against real Home Assistant instance
- For manual verification only

## Test Fixtures

Defined in `tests/conftest.py`:
- `temp_dir`: Temporary directory for test files
- `mock_config_data`: Mock configuration
- `mock_config_file`: Mock config.json file
- `mock_state_data`: Mock state data
- `mock_state_file`: Mock state.json file
- `mock_ha_states`: Mock Home Assistant states response
- `mock_ha_entity`: Factory for creating mock HA entities

## Writing New Tests

### Example unit test
```python
def test_my_feature(temp_dir, mock_config_file):
    """Test description."""
    config = JarvisConfig.load(mock_config_file)
    # Test implementation
    assert result == expected
```

### Example integration test
```python
@pytest.mark.integration
def test_real_workflow(temp_dir):
    """Test realistic usage scenario."""
    # Multi-step workflow test
    pass
```

### Example mocking external calls
```python
@patch('subprocess.run')
def test_ha_call(mock_run, ha_service):
    """Test HA API call."""
    mock_run.return_value = Mock(returncode=0, stdout='{"state": "on"}')
    result = ha_service.get_entity_state("light.kitchen")
    assert result["state"] == "on"
```

## Coverage Goals

- **Core modules** (state_manager, config, logger): 90%+
- **Services** (ha_service, snapshot_service, occupancy_service): 80%+
- **Handlers** (empty_room, occupied_room): 70%+
- **Overall**: 80%+

## Continuous Testing

For development:
```bash
# Watch mode (requires pytest-watch)
ptw

# Or use pytest's built-in
pytest --looponfail
```

## Troubleshooting

### Import errors
Make sure you're running from the project root:
```bash
cd /Users/andrewturko/clawd/skills/jarvis-mode
pytest
```

### Tests fail due to file permissions
Check that test temp directories are writable:
```bash
ls -la /tmp
```

### Mock not working
Ensure mock path matches actual import path:
```python
# If code does: from services.ha_service import HAService
# Mock should be: @patch('services.ha_service.subprocess.run')
```
