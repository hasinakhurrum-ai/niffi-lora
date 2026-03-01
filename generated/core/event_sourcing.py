"""Event-sourced foundation for Niffi DevOS.

- Deterministic: events are ordered by (tick, seq) and hashed.
- Append-only: events are never updated (only compensated with new events).
- Replay: reconstructs derived state by re-applying events up to a tick.

This module is intentionally small and dependency-free.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Iterable

import config


@dataclass(frozen=True)
class Event:
    id: int
    ts: float
    tick: int
    seq: int
    scope: str  # 'core' or 'project'
    project: str | None
    type: str
    payload: dict[str, Any]
    hash: str


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def compute_hash(tick: int, seq: int, scope: str, project: str | None, type_: str, payload: dict[str, Any], prev_hash: str) -> str:
    m = hashlib.sha256()
    m.update(str(tick).encode())
    m.update(b"|")
    m.update(str(seq).encode())
    m.update(b"|")
    m.update(scope.encode())
    m.update(b"|")
    m.update((project or "").encode())
    m.update(b"|")
    m.update(type_.encode())
    m.update(b"|")
    m.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    m.update(b"|")
    m.update((prev_hash or "").encode())
    return m.hexdigest()


def append_event(*, tick: int, scope: str, type_: str, payload: dict[str, Any] | None = None, project: str | None = None) -> Event:
    payload = payload or {}
    ts = time.time()
    with _conn() as conn:
        row = conn.execute(
            "SELECT seq, hash FROM events WHERE tick=? ORDER BY seq DESC LIMIT 1",
            (tick,),
        ).fetchone()
        prev_seq = int(row["seq"]) if row else -1
        prev_hash = str(row["hash"]) if row else ""
        seq = prev_seq + 1
        h = compute_hash(tick, seq, scope, project, type_, payload, prev_hash)
        cur = conn.execute(
            "INSERT INTO events (ts, tick, seq, scope, project, type, payload_json, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, tick, seq, scope, project, type_, json.dumps(payload), h),
        )
        eid = int(cur.lastrowid)
        return Event(id=eid, ts=ts, tick=tick, seq=seq, scope=scope, project=project, type=type_, payload=payload, hash=h)


def iter_events(*, tick_from: int = 0, tick_to: int | None = None, scope: str | None = None, project: str | None = None) -> Iterable[Event]:
    q = "SELECT * FROM events WHERE tick>=?"
    args: list[Any] = [tick_from]
    if tick_to is not None:
        q += " AND tick<=?"
        args.append(tick_to)
    if scope is not None:
        q += " AND scope=?"
        args.append(scope)
    if project is not None:
        q += " AND project=?"
        args.append(project)
    q += " ORDER BY tick ASC, seq ASC"
    with _conn() as conn:
        for r in conn.execute(q, args):
            yield Event(
                id=int(r["id"]),
                ts=float(r["ts"]),
                tick=int(r["tick"]),
                seq=int(r["seq"]),
                scope=str(r["scope"]),
                project=(str(r["project"]) if r["project"] is not None else None),
                type=str(r["type"]),
                payload=json.loads(r["payload_json"] or "{}"),
                hash=str(r["hash"]),
            )


def verify_chain(*, tick_from: int = 0, tick_to: int | None = None) -> tuple[bool, str]:
    """Verify per-tick hash chain integrity.

    Returns (ok, message). For each tick, events chain via prev hash.
    """
    prev_hash = ""
    last_tick = None
    last_seq = -1
    for ev in iter_events(tick_from=tick_from, tick_to=tick_to):
        if last_tick is None or ev.tick != last_tick:
            prev_hash = ""
            last_seq = -1
            last_tick = ev.tick
        expected = compute_hash(ev.tick, ev.seq, ev.scope, ev.project, ev.type, ev.payload, prev_hash)
        if expected != ev.hash:
            return False, f"Hash mismatch at tick={ev.tick} seq={ev.seq} id={ev.id}"
        if ev.seq != last_seq + 1:
            return False, f"Seq gap at tick={ev.tick}: got {ev.seq} expected {last_seq+1}"
        prev_hash = ev.hash
        last_seq = ev.seq
    return True, "OK"
