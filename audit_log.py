"""
Audit log: append-only log of key engine events (task start/end, proposal applied, etc.).
Written to state/audit.log as JSON lines. No PII; use for debugging and compliance.
"""

import json
import os
from datetime import datetime

try:
    import config
except ImportError:
    config = None

AUDIT_PATH = getattr(config, "STATE_DIR", "state") + "/audit.log"


def emit(event_type: str, payload: dict | None = None) -> None:
    """Append one audit record. event_type e.g. task_start, task_end, proposal_applied, cycle_begin."""
    try:
        state_dir = os.path.dirname(AUDIT_PATH)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        record = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "event": event_type,
            **(payload or {}),
        }
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass
