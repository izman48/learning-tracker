"""Configuration from LT_* environment variables only.

Use `load_settings(service, os.environ)` once at startup and pass the result down.
"""

from learning_tracker.config.catalogue import SETTINGS, Settings
from learning_tracker.config.loader import PLACEHOLDER, ConfigError, load_settings
from learning_tracker.config.values import Secret

__all__ = ["PLACEHOLDER", "SETTINGS", "ConfigError", "Secret", "Settings", "load_settings"]
