"""Deterministic replay/audit.

This is a *safe* replay: it does not re-run LLM calls or sandbox execution.
It verifies the event chain and checks invariants over the recorded timeline.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

import config
from generated.core import event_sourcing


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass
class ReplaySummary:
    ok: bool
    ticks: int
    events: int
    open_tasks: list[dict[str, Any]]
    bot_overlaps: list[dict[str, Any]]
    message: str


def replay_audit(*, tick_from: int = 0, tick_to: int | None = None, compare_db: bool = True) -> ReplaySummary:
    ok_chain, msg = event_sourcing.verify_chain(tick_from=tick_from, tick_to=tick_to)
    if not ok_chain:
        return ReplaySummary(False, 0, 0, [], [], f"event_chain_invalid: {msg}")

    in_flight: dict[int, dict[str, Any]] = {}
    bot_running: dict[int, int] = {}
    overlaps: list[dict[str, Any]] = []
    event_count = 0
    last_tick: int | None = None

    for ev in event_sourcing.iter_events(tick_from=tick_from, tick_to=tick_to):
        event_count += 1
        last_tick = ev.tick
        if ev.type == "TASK_START":
            tid = int(ev.payload.get("task_id"))
            bot_id = int(ev.payload.get("bot_id"))
            if bot_id in bot_running and bot_running[bot_id] != tid:
                overlaps.append({"bot_id": bot_id, "existing_task": bot_running[bot_id], "new_task": tid, "tick": ev.tick})
            bot_running[bot_id] = tid
            in_flight[tid] = {"task_id": tid, "bot_id": bot_id, "start_tick": ev.tick}
        elif ev.type == "TASK_END":
            tid = int(ev.payload.get("task_id"))
            bot_id = int(ev.payload.get("bot_id"))
            in_flight.pop(tid, None)
            if bot_running.get(bot_id) == tid:
                bot_running.pop(bot_id, None)

    open_tasks = list(in_flight.values())
    ok = (len(open_tasks) == 0 and len(overlaps) == 0)

    if compare_db and open_tasks:
        try:
            with _conn() as conn:
                for ot in open_tasks:
                    r = conn.execute("SELECT state FROM tasks WHERE id=?", (ot["task_id"],)).fetchone()
                    if r:
                        ot["db_state"] = r["state"]
        except Exception:
            pass

    ticks = 0
    if last_tick is not None:
        if tick_to is None:
            ticks = last_tick - tick_from + 1
        else:
            ticks = tick_to - tick_from + 1

    return ReplaySummary(ok, ticks, event_count, open_tasks, overlaps, "OK" if ok else "invariants_failed")
