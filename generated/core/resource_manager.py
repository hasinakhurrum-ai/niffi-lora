"""Resource-aware governance (best-effort, cross-platform).

Goals:
  - Observe CPU/memory pressure.
  - Expose a small API for the scheduler to cap concurrency.
  - Persist snapshots for introspection and self-evaluation.

If psutil is available, we use it; otherwise fall back to os.getloadavg (unix) and process memory estimates.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

import config


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def snapshot() -> dict[str, Any]:
    snap: dict[str, Any] = {"ts": time.time()}
    # CPU
    try:
        import psutil  # type: ignore
        snap["cpu_percent"] = float(psutil.cpu_percent(interval=0.2))
        vm = psutil.virtual_memory()
        snap["mem_total"] = int(vm.total)
        snap["mem_available"] = int(vm.available)
        snap["mem_percent"] = float(vm.percent)
    except Exception:
        try:
            la = os.getloadavg()
            snap["loadavg_1m"] = float(la[0])
        except Exception:
            snap["loadavg_1m"] = None
        snap["mem_percent"] = None
    return snap


def record_snapshot(tick: int, *, scope: str = "core") -> dict[str, Any]:
    snap = snapshot()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO resource_snapshots (tick, scope, snapshot_json, created_at) VALUES (?, ?, ?, datetime('now'))",
            (tick, scope, json.dumps(snap)),
        )
    return snap


def recommended_concurrency(default: int) -> int:
    """Return a safe concurrency cap based on current pressure."""
    snap = snapshot()
    cap = default
    # If memory pressure high, reduce.
    memp = snap.get("mem_percent")
    if isinstance(memp, (int, float)):
        if memp >= 90:
            cap = max(1, default // 4)
        elif memp >= 80:
            cap = max(1, default // 2)
    cpu = snap.get("cpu_percent")
    if isinstance(cpu, (int, float)):
        if cpu >= 95:
            cap = max(1, min(cap, default // 3))
        elif cpu >= 85:
            cap = max(1, min(cap, default // 2))
    return max(1, cap)
