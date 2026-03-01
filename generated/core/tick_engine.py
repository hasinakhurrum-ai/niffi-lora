"""Deterministic tick engine wrapper for the existing orchestrator loop.

The current orchestrator does: compile -> load hooks -> schedule -> run tasks.
We keep that, but we:
  - assign a monotonically increasing tick id
  - derive a deterministic seed per tick
  - persist tick metadata
  - emit event-sourced tick start/end and key lifecycle events

This enables deterministic replay of *engine decisions* (not external IO).
"""

from __future__ import annotations

import os
import random
import sqlite3
import time
from dataclasses import dataclass

import config
from generated.core import event_sourcing


@dataclass
class TickContext:
    tick: int
    seed: int
    started_at: float


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def next_tick() -> TickContext:
    with _conn() as conn:
        row = conn.execute("SELECT MAX(tick) AS t FROM ticks").fetchone()
        t = int(row["t"]) + 1 if row and row["t"] is not None else 0
        # Deterministic seed derived from tick and a stable engine salt.
        salt = getattr(config, "ENGINE_SEED_SALT", "niffi")
        seed = int.from_bytes(f"{salt}:{t}".encode("utf-8"), "little", signed=False) % (2**31 - 1)
        started = time.time()
        conn.execute(
            "INSERT INTO ticks (tick, seed, started_at, status) VALUES (?, ?, ?, 'running')",
            (t, seed, started),
        )
    # Apply deterministic knobs for this process (best effort)
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    event_sourcing.append_event(tick=t, scope="core", type_="TICK_START", payload={"seed": seed})
    return TickContext(tick=t, seed=seed, started_at=started)


def end_tick(ctx: TickContext, *, status: str = "ok") -> None:
    ended = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE ticks SET ended_at=?, status=? WHERE tick=?",
            (ended, status, ctx.tick),
        )
    event_sourcing.append_event(tick=ctx.tick, scope="core", type_="TICK_END", payload={"status": status, "duration_s": ended - ctx.started_at})
