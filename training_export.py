"""
Training export: export training_examples to JSONL for fine-tuning or analysis.
Call export_training_data() periodically or on demand. Phase 3.
"""

import json
import os

try:
    import config
    import db
except ImportError:
    config = None
    db = None


def export_training_data(path: str | None = None, limit: int = 1000) -> str:
    """Export training examples to a JSONL file. Returns path written or error message."""
    if db is None:
        return "[training_export] db not available"
    state_dir = getattr(config, "STATE_DIR", "state")
    path = path or os.path.join(state_dir, "training_export.jsonl")
    try:
        rows = db.list_training_examples(limit=limit)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                record = {"prompt": r.get("prompt", ""), "response": r.get("response", ""), "score": r.get("score")}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
    except Exception as e:
        return f"[training_export] error: {e}"
