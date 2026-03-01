"""Core engine self-tests used to gate application of engine MODULE changes.

These tests are intentionally lightweight and fast; they should never hang.
They focus on structural and invariants checks (DB schema, scheduler, generated/ dirs).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import config
import db


def _test_db_schema() -> None:
    """Ensure core tables exist and are queryable."""
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    for table in ("bots", "tasks", "runs", "artifacts", "creations", "models", "training_examples"):
        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
        cur.fetchone()
    conn.close()


def _test_scheduler_domain_rules() -> None:
    """Smoke test: scheduler can be called with no tasks without crashing."""
    _ = db.get_next_task()


def _test_generated_dirs() -> None:
    """Ensure generated/ core dirs exist or can be created."""
    root = Path(getattr(config, "GENERATED_MODULES_DIR", "generated"))
    subs = getattr(config, "GENERATED_SUBDIRS", ("core", "plugins", "utils"))
    for sub in subs:
        p = root / sub
        p.mkdir(parents=True, exist_ok=True)


def run_core_tests() -> tuple[bool, str]:
    """
    Run core engine self-tests.

    Returns:
        (ok, message)
    """
    try:
        _test_db_schema()
        _test_scheduler_domain_rules()
        _test_generated_dirs()
        return True, "ok"
    except Exception as e:
        return False, str(e)

