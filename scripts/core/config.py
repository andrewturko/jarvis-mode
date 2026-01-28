#!/usr/bin/env python3
"""
Type-safe configuration management for Jarvis Mode.

Provides validated configuration with defaults and environment variable support.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional


@dataclass
class CameraConfig:
    """Configuration for a single camera/room."""
    entity_id: str
    enabled: bool = True
    motion_sensor: Optional[str] = None

    def __post_init__(self):
        """Validate configuration after initialization."""
        if not self.entity_id:
            raise ValueError("Camera entity_id is required")


@dataclass
class AutoActionsConfig:
    """Configuration for automatic actions."""
    enabled: bool = False
    announce_actions: bool = True


@dataclass
class ActiveHoursConfig:
    """Configuration for active hours."""
    start: int = 7
    end: int = 23

    def __post_init__(self):
        """Validate hours are in valid range."""
        if not (0 <= self.start <= 23):
            raise ValueError(f"start hour must be 0-23, got {self.start}")
        # Allow end=24 to represent "end of day" (24/7 when start=0)
        if not (0 <= self.end <= 24):
            raise ValueError(f"end hour must be 0-24, got {self.end}")

    def is_active(self, hour: int) -> bool:
        """Check if given hour is within active hours."""
        if self.start <= self.end:
            return self.start <= hour < self.end
        else:
            # Handle wrap-around (e.g., 22:00 to 2:00)
            return hour >= self.start or hour < self.end


@dataclass
class SuggestionsConfig:
    """Configuration for which suggestion types are enabled."""
    music: bool = True
    lighting: bool = True
    tv: bool = True
    climate: bool = True


@dataclass
class JarvisConfig:
    """
    Main Jarvis configuration.

    Validates all settings and provides type-safe access.
    """
    enabled: bool = False
    check_interval_minutes: int = 5
    cooldown_minutes: int = 30
    motion_cooldown_minutes: int = 10
    motion_aware: bool = True
    instant_alerts: bool = True
    quiet_mode: bool = True
    auto_actions: AutoActionsConfig = field(default_factory=AutoActionsConfig)
    active_hours: ActiveHoursConfig = field(default_factory=ActiveHoursConfig)
    cameras: Dict[str, CameraConfig] = field(default_factory=dict)
    suggestions: SuggestionsConfig = field(default_factory=SuggestionsConfig)
    notify_channel: str = "telegram"

    def __post_init__(self):
        """Validate configuration after initialization."""
        # Validate intervals
        if self.check_interval_minutes < 1:
            raise ValueError("check_interval_minutes must be >= 1")
        if self.cooldown_minutes < 1:
            raise ValueError("cooldown_minutes must be >= 1")
        if self.motion_cooldown_minutes < 1:
            raise ValueError("motion_cooldown_minutes must be >= 1")

        # Convert camera dicts to CameraConfig objects
        if self.cameras and not isinstance(list(self.cameras.values())[0], CameraConfig):
            self.cameras = {
                name: CameraConfig(**cam_data) if isinstance(cam_data, dict) else cam_data
                for name, cam_data in self.cameras.items()
            }

        # Convert nested dicts to dataclasses
        if isinstance(self.auto_actions, dict):
            self.auto_actions = AutoActionsConfig(**self.auto_actions)
        if isinstance(self.active_hours, dict):
            self.active_hours = ActiveHoursConfig(**self.active_hours)
        if isinstance(self.suggestions, dict):
            self.suggestions = SuggestionsConfig(**self.suggestions)

    def get_enabled_cameras(self) -> Dict[str, CameraConfig]:
        """Get only enabled cameras."""
        return {
            name: cam for name, cam in self.cameras.items()
            if cam.enabled
        }

    def is_suggestion_enabled(self, suggestion_type: str) -> bool:
        """Check if a suggestion type is enabled."""
        return getattr(self.suggestions, suggestion_type, False)

    @classmethod
    def load(cls, config_path: Path) -> 'JarvisConfig':
        """
        Load configuration from JSON file.

        Supports environment variable interpolation:
        - "${VAR_NAME}" will be replaced with os.environ.get("VAR_NAME")
        - "${VAR_NAME:-default}" will use default if VAR_NAME not set

        Args:
            config_path: Path to config.json

        Returns:
            Validated JarvisConfig instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If configuration is invalid
        """
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r') as f:
            raw_config = json.load(f)

        # Process environment variables
        config_data = cls._interpolate_env_vars(raw_config)

        # Convert camelCase to snake_case for dataclass compatibility
        config_data = cls._to_snake_case(config_data)

        try:
            return cls(**config_data)
        except TypeError as e:
            raise ValueError(f"Invalid configuration: {e}")

    @classmethod
    def _to_snake_case(cls, data):
        """
        Convert camelCase keys to snake_case recursively.

        For compatibility with existing config.json files.
        """
        def camel_to_snake(name):
            import re
            name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
            return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()

        if isinstance(data, dict):
            return {camel_to_snake(k): cls._to_snake_case(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [cls._to_snake_case(item) for item in data]
        else:
            return data

    @classmethod
    def _interpolate_env_vars(cls, data):
        """
        Recursively interpolate environment variables in config.

        Supports:
        - "${VAR}" - replaced with os.environ["VAR"]
        - "${VAR:-default}" - replaced with os.environ.get("VAR", "default")
        """
        if isinstance(data, dict):
            return {k: cls._interpolate_env_vars(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [cls._interpolate_env_vars(item) for item in data]
        elif isinstance(data, str):
            # Simple env var replacement
            if data.startswith("${") and data.endswith("}"):
                var_expr = data[2:-1]

                # Check for default value syntax: ${VAR:-default}
                if ":-" in var_expr:
                    var_name, default = var_expr.split(":-", 1)
                    return os.environ.get(var_name.strip(), default.strip())
                else:
                    var_name = var_expr.strip()
                    if var_name in os.environ:
                        return os.environ[var_name]
                    else:
                        raise ValueError(f"Environment variable not set: {var_name}")

            return data
        else:
            return data

    def save(self, config_path: Path):
        """
        Save configuration to JSON file.

        Args:
            config_path: Path to write config.json
        """
        # Convert to dict, handling nested dataclasses
        config_dict = self._to_dict()

        with open(config_path, 'w') as f:
            json.dump(config_dict, f, indent=2)

    def _to_dict(self) -> dict:
        """Convert config to dictionary, handling nested dataclasses."""
        result = {}
        for key, value in asdict(self).items():
            if isinstance(value, dict):
                # Convert CameraConfig objects
                if key == "cameras":
                    result[key] = {
                        name: self._dataclass_to_dict(cam)
                        for name, cam in value.items()
                    }
                else:
                    result[key] = value
            else:
                result[key] = value
        return result

    def _dataclass_to_dict(self, obj) -> dict:
        """Convert dataclass to dict recursively."""
        if hasattr(obj, '__dataclass_fields__'):
            return asdict(obj)
        return obj


# Example usage and validation
if __name__ == "__main__":
    import tempfile

    # Test basic configuration
    print("Testing configuration management...\n")

    # Create test config
    test_config = {
        "enabled": True,
        "check_interval_minutes": 5,
        "cooldown_minutes": 30,
        "cameras": {
            "kitchen": {
                "entity_id": "camera.kitchen",
                "enabled": True,
                "motion_sensor": "binary_sensor.kitchen_motion"
            },
            "living_room": {
                "entity_id": "camera.living_room",
                "enabled": False
            }
        },
        "active_hours": {
            "start": 7,
            "end": 23
        },
        "suggestions": {
            "music": True,
            "lighting": True,
            "tv": False,
            "climate": True
        }
    }

    # Write test config
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(test_config, f)
        temp_path = Path(f.name)

    try:
        # Load and validate
        config = JarvisConfig.load(temp_path)

        print("✓ Configuration loaded successfully")
        print(f"  Enabled: {config.enabled}")
        print(f"  Check interval: {config.check_interval_minutes} min")
        print(f"  Total cameras: {len(config.cameras)}")
        print(f"  Enabled cameras: {len(config.get_enabled_cameras())}")

        # Test enabled cameras
        enabled_cams = config.get_enabled_cameras()
        print(f"\n✓ Enabled cameras: {', '.join(enabled_cams.keys())}")

        # Test active hours
        print(f"\n✓ Active hours: {config.active_hours.start}:00 - {config.active_hours.end}:00")
        print(f"  Is 14:00 active? {config.active_hours.is_active(14)}")
        print(f"  Is 2:00 active? {config.active_hours.is_active(2)}")

        # Test suggestions
        print(f"\n✓ Enabled suggestions:")
        for suggestion_type in ['music', 'lighting', 'tv', 'climate']:
            enabled = config.is_suggestion_enabled(suggestion_type)
            status = "✓" if enabled else "✗"
            print(f"  {status} {suggestion_type}")

        # Test validation
        print("\n✓ Testing validation...")
        try:
            invalid_config = JarvisConfig(
                check_interval_minutes=0,  # Invalid
                cameras={}
            )
            print("✗ Validation failed to catch invalid config")
        except ValueError as e:
            print(f"✓ Validation caught error: {e}")

        # Test save
        output_path = temp_path.with_suffix('.output.json')
        config.save(output_path)
        print(f"\n✓ Configuration saved to: {output_path}")

        # Clean up
        output_path.unlink()

    finally:
        temp_path.unlink()

    print("\n✓ All tests passed!")
