"""
Config profiles: named bundles that override config for dev, quantum, fleet, security, etc.
Set NIFFI_PROFILE env var or config.CONFIG_PROFILE to select. Applied after config load.
"""

import os

try:
    import config as _config
except ImportError:
    _config = None

CONFIG_PROFILE = os.environ.get("NIFFI_PROFILE", getattr(_config, "CONFIG_PROFILE", "dev"))

# Profile overrides: key = config attribute name, value = value for that profile.
# Omit key to leave config unchanged.
PROFILES = {
    "dev": {
        "BOTS_CONCURRENCY": 2,
        "CORE_FIRST_SLOTS": 1,
        "APPLY_PROPOSALS_EVERY_N_CYCLES": 3,
    },
    "quantum": {
        "BOTS_CONCURRENCY": 3,
        "CORE_FIRST_SLOTS": 1,
        "APPLY_PROPOSALS_EVERY_N_CYCLES": 2,
    },
    "fleet": {
        "BOTS_CONCURRENCY": 4,
        "CORE_FIRST_SLOTS": 1,
        "APPLY_PROPOSALS_EVERY_N_CYCLES": 5,
    },
    "security": {
        "BOTS_CONCURRENCY": 2,
        "CORE_FIRST_SLOTS": 1,
        "APPLY_PROPOSALS_EVERY_N_CYCLES": 3,
    },
}


def apply_profile(profile_name: str | None = None) -> None:
    """Apply a profile's overrides to config. Call once at startup after config is loaded."""
    if _config is None:
        return
    name = (profile_name or CONFIG_PROFILE or "dev").strip().lower()
    overrides = PROFILES.get(name, {})
    for key, value in overrides.items():
        if hasattr(_config, key):
            setattr(_config, key, value)
