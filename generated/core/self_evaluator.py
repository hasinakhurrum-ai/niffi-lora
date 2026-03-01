"""Formal self-evaluation for Niffi core architecture.

This does NOT use LLM calls. It's a deterministic rule-based evaluator that:
  - checks presence of required subsystems (event store, tick engine, ecs, resource manager, model metrics)
  - checks DB schema readiness (tables exist)
  - checks that main loop is emitting tick events (heuristic: recent ticks)

It writes a summary to architecture_state and may create upgrade_backlog items for missing pieces.
"""

from __future__ import annotations

import json
import sqlite3
import time

import config


REQUIRED_COMPONENTS = [
    "event_sourcing",
    "tick_engine",
    "ecs",
    "resource_manager",
    "model_metrics",
    "distributed",
]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    r = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(r)


def evaluate() -> dict:
    with _conn() as conn:
        tables_ok = {
            "events": _table_exists(conn, "events"),
            "ticks": _table_exists(conn, "ticks"),
            "ecs_entities": _table_exists(conn, "ecs_entities"),
            "ecs_components": _table_exists(conn, "ecs_components"),
            "resource_snapshots": _table_exists(conn, "resource_snapshots"),
            "llm_calls": _table_exists(conn, "llm_calls"),
        }
        tick_row = conn.execute("SELECT MAX(tick) AS t, COUNT(*) AS c FROM ticks").fetchone()
        max_tick = int(tick_row["t"]) if tick_row and tick_row["t"] is not None else -1
        tick_count = int(tick_row["c"]) if tick_row else 0

    score = 0
    score += 20 if tables_ok["events"] else 0
    score += 15 if tables_ok["ticks"] else 0
    score += 15 if tables_ok["ecs_entities"] and tables_ok["ecs_components"] else 0
    score += 10 if tables_ok["resource_snapshots"] else 0
    score += 10 if tables_ok["llm_calls"] else 0
    score += 10 if tick_count > 0 else 0
    # Remaining points reserved for distributed + replay + governance (future)
    summary = {
        "ts": time.time(),
        "score_100": score,
        "tables": tables_ok,
        "ticks": {"count": tick_count, "max": max_tick},
        "notes": [],
    }
    if score < 70:
        summary["notes"].append("Core is missing one or more required subsystems or schema.")
    return summary


def persist(summary: dict) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO architecture_state (component, summary, interfaces, known_issues, metrics_json, updated_at) "
            "VALUES ('core_self_eval', ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(component) DO UPDATE SET summary=excluded.summary, interfaces=excluded.interfaces, known_issues=excluded.known_issues, metrics_json=excluded.metrics_json, updated_at=datetime('now')",
            (
                "Deterministic self-evaluation of core architecture.",
                "evaluate(), persist()",
                "\n".join(summary.get("notes", [])),
                json.dumps(summary),
            ),
        )
