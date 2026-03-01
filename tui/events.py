"""Parse engine stdout into lifecycle event type + tail-format line. Phase 1: line-based mapping."""

import re
from datetime import datetime
from typing import Optional, Tuple

# Match [bot_name] at start of line (alphanumeric + underscore only)
BOT_TAG_RE = re.compile(r"^\[([a-zA-Z0-9_]+)\]\s*")


def parse_project_tag(line: str) -> Tuple[Optional[str], str]:
    """If line starts with [bot_name], return (bot_name, rest). Else return (None, line)."""
    m = BOT_TAG_RE.match(line)
    if m:
        return m.group(1).strip(), line[m.end() :].strip() or line
    return None, line

# Map engine log prefixes to canonical event type
# main.py uses _log("[BACKGROUND] ..."), "[BUILD]", "[SELF-IMPROVE]", "[CREATION]", "[EVOLVE]", "=== ASK ===", "=== RESPONSE ===", "=== CODE ==="
PREFIX_TO_TYPE = [
    (re.compile(r"^\[BUILD\]", re.I), "BUILD"),
    (re.compile(r"^\[SELF-IMPROVE\]", re.I), "ENHANCE"),
    (re.compile(r"^\[EVOLVE\]", re.I), "ENHANCE"),
    (re.compile(r"^\[CREATION\]", re.I), "APPLY"),
    (re.compile(r"^\[BACKGROUND\]\s+Created model", re.I), "APPLY"),
    (re.compile(r"^\[BACKGROUND\]\s+Registered", re.I), "APPLY"),
    (re.compile(r"^\[BACKGROUND\]\s+Applied", re.I), "APPLY"),
    (re.compile(r"^\[BACKGROUND\]\s+CREATE_MODEL", re.I), "ERROR"),
    (re.compile(r"^\[BACKGROUND\]\s+REGISTER_EXTERNAL", re.I), "INFO"),
    (re.compile(r"^\[BACKGROUND\]\s+Waiting for tasks", re.I), "INFO"),
    (re.compile(r"^\[BACKGROUND\]\s+Task \d+ finished", re.I), "EXEC"),
    (re.compile(r"^\[BACKGROUND\]\s+Cycle done", re.I), "INFO"),
    (re.compile(r"^\[BACKGROUND\]\s+Run error", re.I), "ERROR"),
    (re.compile(r"^\[BACKGROUND\]", re.I), "INFO"),
    (re.compile(r"^=== ASK", re.I), "THINK"),
    (re.compile(r"^=== RESPONSE", re.I), "CODE"),
    (re.compile(r"^=== CODE", re.I), "CODE"),
    (re.compile(r"^>>> TASK START", re.I), "PLAN"),
    (re.compile(r"^\[Reflection\]", re.I), "THINK"),
    (re.compile(r"^\[STARTUP\]", re.I), "INFO"),
    (re.compile(r"^\[ENGINE\]", re.I), "INFO"),
    (re.compile(r"^\[PARALLEL\]", re.I), "INFO"),
]


def classify_line(line: str) -> Tuple[str, str]:
    """Return (event_type, display_message). Message is shortened for tail format."""
    stripped = line.strip()
    if not stripped:
        return "INFO", ""
    for pattern, etype in PREFIX_TO_TYPE:
        if pattern.search(stripped):
            # Shorten for single-line display
            msg = stripped[:200] + ("..." if len(stripped) > 200 else "")
            return etype, msg
    # Default: INFO
    return "INFO", stripped[:200] + ("..." if len(stripped) > 200 else "")


def format_tail_line(ts: datetime, event_type: str, message: str, project: Optional[str] = None) -> str:
    """Format as HH:MM:SS  TYPE  message or [project]  HH:MM:SS  TYPE  message."""
    time_str = ts.strftime("%H:%M:%S")
    type_str = event_type.ljust(7)
    if project:
        return f"{time_str}  [{project}]  {type_str}  {message}"
    return f"{time_str}  {type_str}  {message}"
