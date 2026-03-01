"""ECS-driven scheduling adapter.

This makes ECS the *decision engine* for what to run each tick, while keeping
the existing DB tables as the execution substrate.

Safety:
  - deterministic selection
  - no DB schema changes required
  - if ECS fails, callers can fall back to legacy scheduler
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

import config

from generated.core.ecs import ECSWorld
from generated.core import event_sourcing


BOT_COMPONENT = "Bot"
TASK_COMPONENT = "Task"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _stable_hash_int(s: str) -> int:
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:8], 16)


def sync_from_db(world: ECSWorld) -> None:
    """Sync bots + queued tasks from DB into ECS components."""
    try:
        import db
    except Exception:
        return

    bots = db.list_bots() or []

    # Ensure bot entities
    for b in bots:
        name = b.get("name") or f"bot_{b['id']}"
        domain = (b.get("domain") or "").strip() or "project"
        # Find existing bot entity
        bot_eid = None
        for ent, comp in world.query_entities_with(BOT_COMPONENT):
            if int(comp.get("bot_id", -1)) == int(b["id"]):
                bot_eid = ent.id
                break
        if bot_eid is None:
            bot_eid = world.create_entity(project=None if domain == "system" else name, name=name).id
        world.set_component(
            bot_eid,
            BOT_COMPONENT,
            {
                "bot_id": int(b["id"]),
                "name": name,
                "domain": domain,
                "status": (b.get("status") or "idle"),
                "model": (b.get("model") or ""),
            },
        )

    # Ensure task entities for queued tasks
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, bot_id, priority, task_type, created_at FROM tasks WHERE state='queued' ORDER BY priority DESC, created_at ASC"
        ).fetchall()

    for r in rows:
        tid = int(r["id"])
        # Find existing task entity
        task_eid = None
        for ent, comp in world.query_entities_with(TASK_COMPONENT):
            if int(comp.get("task_id", -1)) == tid:
                task_eid = ent.id
                break
        if task_eid is None:
            task_eid = world.create_entity(project=None, name=f"task_{tid}").id
        world.set_component(
            task_eid,
            TASK_COMPONENT,
            {
                "task_id": tid,
                "bot_id": int(r["bot_id"]),
                "priority": int(r["priority"] or 0),
                "task_type": (r["task_type"] or "code"),
                "created_at": str(r["created_at"] or ""),
            },
        )


def schedule_tasks(*, tick: int, concurrency: int) -> list[dict[str, Any]]:
    """Return a deterministic selection of tasks to run this tick."""
    concurrency = max(1, int(concurrency))
    selected_ids: list[int] = []
    world = ECSWorld()
    try:
        sync_from_db(world)

        skipped_bot_ids: set[int] = set()
        for _ent, comp in world.query_entities_with(BOT_COMPONENT):
            st = (comp.get("status") or "").strip().lower()
            if st in ("paused", "done"):
                skipped_bot_ids.add(int(comp.get("bot_id", -1)))

        # Build candidate list (exclude tasks whose bot is paused or done)
        candidates: list[tuple[int, str, int, int]] = []
        for ent, comp in world.query_entities_with(TASK_COMPONENT):
            tid = int(comp.get("task_id"))
            bot_id = int(comp.get("bot_id", -1))
            if bot_id in skipped_bot_ids:
                continue
            pr = int(comp.get("priority", 0))
            created = str(comp.get("created_at", ""))
            tie = _stable_hash_int(f"{tick}:{tid}")
            candidates.append((pr, created, tie, tid))

        candidates.sort(key=lambda x: (-x[0], x[1], x[2]))

        # One task per bot per tick.
        used_bots: set[int] = set()
        for pr, created, tie, tid in candidates:
            if len(selected_ids) >= concurrency:
                break
            bot_id = None
            for _e, c in world.query_entities_with(TASK_COMPONENT):
                if int(c.get("task_id")) == tid:
                    bot_id = int(c.get("bot_id"))
                    break
            if bot_id is None or bot_id in used_bots:
                continue
            used_bots.add(bot_id)
            selected_ids.append(tid)

        try:
            event_sourcing.append_event(
                tick=tick,
                scope="core",
                type_="SCHEDULE",
                payload={"selected_task_ids": selected_ids, "concurrency": concurrency},
            )
        except Exception:
            pass

    finally:
        world.close()

    if not selected_ids:
        return []

    # Fetch tasks from DB in order
    tasks: list[dict[str, Any]] = []
    with _conn() as conn:
        for tid in selected_ids:
            r = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if r:
                tasks.append(dict(r))
    return tasks
