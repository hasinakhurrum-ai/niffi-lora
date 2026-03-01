"""TUI state: three top-level states only. No hidden states."""

from enum import Enum
from typing import Optional


class StreamState(str, Enum):
    MAIN_STREAM = "main"
    PROJECT_STREAM = "project"
    PROMPT_MODE = "prompt"


# Verbosity cycle: SUMMARY -> VERBOSE -> RAW -> SUMMARY
class Verbosity(str, Enum):
    SUMMARY = "SUMMARY"
    VERBOSE = "VERBOSE"
    RAW = "RAW"


def next_verbosity(v: Verbosity) -> Verbosity:
    if v == Verbosity.SUMMARY:
        return Verbosity.VERBOSE
    if v == Verbosity.VERBOSE:
        return Verbosity.RAW
    return Verbosity.SUMMARY


# Lifecycle event types (canonical)
EVENT_TYPES = (
    "THINK", "PLAN", "CODE", "APPLY", "EXEC", "TEST",
    "DEPLOY", "ENHANCE", "BUILD", "ERROR", "INFO",
)
