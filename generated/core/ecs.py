"""Minimal, modular ECS (Entity-Component-System) for Niffi.

- Entities: integer IDs, optionally named and scoped to a project.
- Components: JSON blobs keyed by (entity_id, component_name).
- Systems: callables registered by plugins; run each tick.

Storage is SQLite (eventual: derived from events). This gives you:
  * fully modular systems (plugins can register systems)
  * deterministic updates when driven by TickContext + event stream
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import config


@dataclass(frozen=True)
class Entity:
    id: int
    project: str | None
    name: str | None


SystemFn = Callable[[int, "ECSWorld"], None]


class ECSWorld:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(config.DB_PATH)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def create_entity(self, *, project: str | None = None, name: str | None = None) -> Entity:
        cur = self._conn.execute(
            "INSERT INTO ecs_entities (project, name) VALUES (?, ?)",
            (project, name),
        )
        self._conn.commit()
        return Entity(id=int(cur.lastrowid), project=project, name=name)

    def list_entities(self, *, project: str | None = None) -> list[Entity]:
        if project is None:
            rows = self._conn.execute("SELECT * FROM ecs_entities ORDER BY id ASC").fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM ecs_entities WHERE project=? ORDER BY id ASC", (project,)).fetchall()
        return [Entity(id=int(r["id"]), project=(str(r["project"]) if r["project"] else None), name=(str(r["name"]) if r["name"] else None)) for r in rows]

    def get_component(self, entity_id: int, component: str) -> dict[str, Any] | None:
        r = self._conn.execute(
            "SELECT data_json FROM ecs_components WHERE entity_id=? AND component=?",
            (entity_id, component),
        ).fetchone()
        if not r:
            return None
        return json.loads(r["data_json"] or "{}")

    def set_component(self, entity_id: int, component: str, data: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO ecs_components (entity_id, component, data_json, updated_at) VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(entity_id, component) DO UPDATE SET data_json=excluded.data_json, updated_at=datetime('now')",
            (entity_id, component, json.dumps(data)),
        )
        self._conn.commit()

    def delete_component(self, entity_id: int, component: str) -> None:
        self._conn.execute("DELETE FROM ecs_components WHERE entity_id=? AND component=?", (entity_id, component))
        self._conn.commit()

    def query_entities_with(self, component: str, *, project: str | None = None) -> Iterable[tuple[Entity, dict[str, Any]]]:
        q = (
            "SELECT e.id as id, e.project as project, e.name as name, c.data_json as data_json "
            "FROM ecs_entities e JOIN ecs_components c ON e.id=c.entity_id "
            "WHERE c.component=?"
        )
        args: list[Any] = [component]
        if project is not None:
            q += " AND e.project=?"
            args.append(project)
        q += " ORDER BY e.id ASC"
        for r in self._conn.execute(q, args):
            ent = Entity(id=int(r["id"]), project=(str(r["project"]) if r["project"] else None), name=(str(r["name"]) if r["name"] else None))
            yield ent, json.loads(r["data_json"] or "{}")


class SystemRegistry:
    def __init__(self) -> None:
        self._systems: list[tuple[str, SystemFn, int]] = []  # (name, fn, order)

    def register(self, name: str, fn: SystemFn, *, order: int = 100) -> None:
        self._systems.append((name, fn, order))
        self._systems.sort(key=lambda x: x[2])

    def run_tick(self, tick: int, world: ECSWorld) -> None:
        for name, fn, _ in list(self._systems):
            fn(tick, world)
