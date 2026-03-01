"""
Metrics collector: runs on_cycle() and writes a snapshot to state/metrics.json.
Queue depth, bot count, run counts (if available). Used for observability and health.
"""

import json
import os
from datetime import datetime

def on_cycle() -> None:
    try:
        import config
        import db
    except ImportError:
        return
    state_dir = getattr(config, "STATE_DIR", "state")
    path = os.path.join(state_dir, "metrics.json")
    try:
        queued = db.count_queued_tasks()
        bots = db.list_bots()
        core_bots = [b for b in bots if (b.get("domain") or "") == "system"]
        project_bots = [b for b in bots if (b.get("domain") or "") != "system"]
        running = sum(1 for b in bots if (b.get("status") or "") == "running")
        total_runs = 0
        try:
            conn = db._get_conn()
            row = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
            conn.close()
            total_runs = row["n"] if row else 0
        except Exception:
            pass
        snapshot = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "queued_tasks": queued,
            "bots_total": len(bots),
            "bots_core": len(core_bots),
            "bots_project": len(project_bots),
            "bots_running": running,
            "total_runs": total_runs,
        }
        os.makedirs(state_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
    except Exception:
        pass
