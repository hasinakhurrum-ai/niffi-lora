"""Model performance awareness.

Records each LLM call (latency, status) and maintains a rolling model_health table.
Designed to work with both local Ollama and external endpoints.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

import config


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _sha(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def record_llm_call(
    *,
    model_name: str,
    latency_ms: int,
    status: str,
    bot_id: int = 0,
    task_id: int | None = None,
    purpose: str = "TASK",
    prompt: str = "",
    response: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    meta = meta or {}
    with _conn() as conn:
        conn.execute(
            "INSERT INTO llm_calls (ts, bot_id, task_id, model_name, purpose, latency_ms, status, prompt_sha, response_sha, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                bot_id,
                task_id,
                model_name,
                purpose,
                latency_ms,
                status,
                _sha(prompt),
                _sha(response or ""),
                json.dumps(meta),
            ),
        )
        # Update model_health with exponential moving averages
        row = conn.execute("SELECT * FROM model_health WHERE model_name=?", (model_name,)).fetchone()
        if row:
            prev_lat = row["avg_latency_ms"] or latency_ms
            # EMA alpha
            alpha = 0.2
            new_lat = (1 - alpha) * float(prev_lat) + alpha * float(latency_ms)
            # Failure rate approx: increment failures, track counts in metadata_json
            m = json.loads(row["metadata_json"] or "{}")
            calls = int(m.get("calls", 0)) + 1
            fails = int(m.get("fails", 0)) + (1 if status != "ok" else 0)
            m["calls"] = calls
            m["fails"] = fails
            failure_rate = fails / max(1, calls)
            conn.execute(
                "UPDATE model_health SET avg_latency_ms=?, failure_rate=?, last_failure_at=?, metadata_json=?, updated_at=datetime('now') WHERE model_name=?",
                (new_lat, failure_rate, (time.strftime("%Y-%m-%d %H:%M:%S") if status != "ok" else row["last_failure_at"]), json.dumps(m), model_name),
            )
        else:
            m = {"calls": 1, "fails": 0 if status == "ok" else 1}
            conn.execute(
                "INSERT INTO model_health (model_name, failure_rate, compile_error_rate, avg_latency_ms, last_failure_at, metadata_json, updated_at) "
                "VALUES (?, ?, 0, ?, ?, ?, datetime('now'))",
                (model_name, (m["fails"] / 1), float(latency_ms), (time.strftime("%Y-%m-%d %H:%M:%S") if status != "ok" else None), json.dumps(m)),
            )


def get_model_health(model_name: str) -> dict[str, Any] | None:
    """Return health metrics for a model (avg latency, failure rate, call counts)."""
    with _conn() as conn:
        r = conn.execute("SELECT * FROM model_health WHERE model_name=?", (model_name,)).fetchone()
        if not r:
            return None
        m = json.loads(r["metadata_json"] or "{}")
        return {
            "model_name": model_name,
            "avg_latency_ms": float(r["avg_latency_ms"] or 0),
            "failure_rate": float(r["failure_rate"] or 0),
            "calls": int(m.get("calls", 0)),
            "fails": int(m.get("fails", 0)),
            "updated_at": r["updated_at"],
        }


def score_model(model_name: str) -> float:
    """Lower is better. Unknown models get a neutral score."""
    h = get_model_health(model_name)
    if not h or h.get("calls", 0) < 3:
        return 50.0
    latency = float(h.get("avg_latency_ms", 0) or 0)
    failure = float(h.get("failure_rate", 0) or 0)
    return latency / 100.0 + (failure * 100.0)


def choose_best_model(candidates: list[str]) -> str | None:
    cands = [c for c in candidates if c]
    if not cands:
        return None
    scored = [(score_model(m), m) for m in cands]
    scored.sort(key=lambda x: x[0])
    return scored[0][1]


def choose_model_for_bot(preferred: str, fallbacks: list[str] | None = None) -> str:
    """Return preferred unless unhealthy; otherwise pick best fallback."""
    fallbacks = fallbacks or []
    preferred_health = get_model_health(preferred)
    if preferred_health and preferred_health.get("calls", 0) >= 5:
        if float(preferred_health.get("failure_rate", 0)) >= 0.35:
            best = choose_best_model([preferred, *fallbacks])
            return best or preferred
    return preferred
