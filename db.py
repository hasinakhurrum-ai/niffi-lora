"""SQLite schema and helpers. Single source of truth for bots, tasks, messages, runs, artifacts."""

import json
import sqlite3
from pathlib import Path

import config


def _get_conn():
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    close = conn is None
    conn = conn or _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            domain TEXT,
            model TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idle',
            system_prompt TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL REFERENCES bots(id),
            priority INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'queued',
            prompt TEXT NOT NULL,
            task_type TEXT NOT NULL DEFAULT 'code',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL REFERENCES bots(id),
            run_id INTEGER REFERENCES runs(id),
            content TEXT NOT NULL,
            applied INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL REFERENCES bots(id),
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            meta_json TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL REFERENCES bots(id),
            task_id INTEGER REFERENCES tasks(id),
            iteration INTEGER NOT NULL,
            sandbox_path TEXT,
            code_path TEXT,
            stdout TEXT,
            stderr TEXT,
            returncode INTEGER,
            duration_ms INTEGER,
            score REAL,
            status TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            meta_json TEXT
        );
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id),
            type TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_bot_state ON tasks(bot_id, state);
        CREATE INDEX IF NOT EXISTS idx_messages_bot ON messages(bot_id);
        CREATE TABLE IF NOT EXISTS creations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER REFERENCES runs(id),
            bot_id INTEGER NOT NULL REFERENCES bots(id),
            type TEXT NOT NULL,
            title TEXT,
            path TEXT NOT NULL,
            run_cmd TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            meta_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_runs_bot ON runs(bot_id);
        CREATE INDEX IF NOT EXISTS idx_creations_type ON creations(type);
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'ollama',
            endpoint_url TEXT,
            base_model TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS training_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt TEXT NOT NULL,
            response TEXT NOT NULL,
            score REAL,
            run_id INTEGER REFERENCES runs(id),
            bot_id INTEGER REFERENCES bots(id),
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_models_name ON models(name);
        CREATE INDEX IF NOT EXISTS idx_training_bot ON training_examples(bot_id);

        -- Prompt / architecture / capabilities / upgrades (DevOS core kernel)
        CREATE TABLE IF NOT EXISTS prompt_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            scope TEXT NOT NULL, -- e.g. 'core', 'project'
            content TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(name, scope)
        );
        CREATE TABLE IF NOT EXISTS prompt_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL, -- e.g. 'core'
            policy_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(scope)
        );
        CREATE TABLE IF NOT EXISTS architecture_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            component TEXT NOT NULL UNIQUE, -- scheduler, sandbox, model_router, plugin_loader, tui_eventbus, etc.
            summary TEXT,
            interfaces TEXT,
            known_issues TEXT,
            metrics_json TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS capabilities_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            capability TEXT NOT NULL UNIQUE, -- e.g. run_shell, pip_install, hot_reload_plugins
            limits_json TEXT,
            status TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS model_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL, -- e.g. 'core', 'project', 'global'
            policy_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(scope)
        );
        CREATE TABLE IF NOT EXISTS model_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT NOT NULL UNIQUE,
            failure_rate REAL,
            compile_error_rate REAL,
            avg_latency_ms REAL,
            last_failure_at TEXT,
            metadata_json TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS upgrade_backlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            problem TEXT,
            proposal_text TEXT,
            priority INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new', -- new/reviewed/planned/implementing/testing/deployed/rolled_back
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS upgrade_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upgrade_id INTEGER NOT NULL REFERENCES upgrade_backlog(id),
            plan_json TEXT,
            acceptance_criteria TEXT,
            status TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

-- Deterministic simulation + event sourcing
CREATE TABLE IF NOT EXISTS ticks (
    tick INTEGER PRIMARY KEY,
    seed INTEGER NOT NULL,
    started_at REAL,
    ended_at REAL,
    status TEXT NOT NULL DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tick INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    scope TEXT NOT NULL, -- core|project
    project TEXT,
    type TEXT NOT NULL,
    payload_json TEXT,
    hash TEXT NOT NULL,
    UNIQUE(tick, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_tick ON events(tick, seq);
CREATE INDEX IF NOT EXISTS idx_events_scope ON events(scope, project);

-- ECS storage (fully modular systems live in code; state lives here)
CREATE TABLE IF NOT EXISTS ecs_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ecs_components (
    entity_id INTEGER NOT NULL REFERENCES ecs_entities(id) ON DELETE CASCADE,
    component TEXT NOT NULL,
    data_json TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY(entity_id, component)
);

-- Resource snapshots for resource-aware scheduling
CREATE TABLE IF NOT EXISTS resource_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER NOT NULL,
    scope TEXT NOT NULL DEFAULT 'core',
    snapshot_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_resource_tick ON resource_snapshots(tick);

-- LLM call log (model performance awareness)
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    bot_id INTEGER NOT NULL REFERENCES bots(id),
    task_id INTEGER REFERENCES tasks(id),
    model_name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    prompt_sha TEXT,
    response_sha TEXT,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_model ON llm_calls(model_name, ts);
CREATE INDEX IF NOT EXISTS idx_llm_calls_bot ON llm_calls(bot_id, ts);

    """)
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'code'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.commit()
    if close:
        conn.close()


# --- Bots ---
def insert_bot(name: str, domain: str, model: str, system_prompt: str) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO bots (name, domain, model, status, system_prompt) VALUES (?, ?, ?, 'idle', ?)",
        (name, domain, model, system_prompt),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def get_bot(bot_id: int | None = None, name: str | None = None) -> dict | None:
    conn = _get_conn()
    if bot_id:
        row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    elif name:
        row = conn.execute("SELECT * FROM bots WHERE name = ?", (name,)).fetchone()
    else:
        conn.close()
        return None
    conn.close()
    return dict(row) if row else None


def list_bots() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM bots ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_bot_status(bot_id: int, status: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE bots SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, bot_id),
    )
    conn.commit()
    conn.close()


# --- Tasks ---
def insert_task(bot_id: int, prompt: str, priority: int = 0, task_type: str = "code") -> int:
    """
    Insert a new task for a bot.

    DevOS enforcement:
    - CORE agents (bots.domain = 'system') may only receive CORE_TASK_TYPES.
    - PROJECT / other agents may only receive PROJECT_TASK_TYPES.
    """
    from config import CORE_TASK_TYPES, PROJECT_TASK_TYPES  # local import to avoid cycles at module import time

    conn = _get_conn()
    # Look up bot domain to enforce role-based task types.
    row = conn.execute("SELECT domain FROM bots WHERE id = ?", (bot_id,)).fetchone()
    domain = (row["domain"] or "").strip() if row else ""
    ttype = (task_type or "code").strip()

    if domain == "system":
        if ttype not in CORE_TASK_TYPES:
            raise ValueError(f"Core bot may not receive task_type='{ttype}'. Allowed: {CORE_TASK_TYPES}")
    else:
        if ttype not in PROJECT_TASK_TYPES:
            raise ValueError(f"Project bot may not receive task_type='{ttype}'. Allowed: {PROJECT_TASK_TYPES}")

    cur = conn.execute(
        "INSERT INTO tasks (bot_id, priority, state, prompt, task_type) VALUES (?, ?, 'queued', ?, ?)",
        (bot_id, priority, prompt, ttype),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def list_core_bots() -> list[dict]:
    """All bots with domain='system' (core/kernel agents)."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM bots WHERE domain = 'system' ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_project_bots() -> list[dict]:
    """All bots that are not core/kernel (projects, tools, etc.)."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM bots WHERE domain != 'system' OR domain IS NULL ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_next_task() -> dict | None:
    """
    Next runnable task: priority DESC, created_at ASC. Must be queued.

    Strict-core behavior:
    - Prefer tasks for CORE_AGENT-style bots (bots.domain = 'system') so the engine
      can always continue improving itself, even when projects are busy.
    - If no core tasks are queued, fall back to any eligible project task.
    """
    conn = _get_conn()
    # First, try to get a core (system) task.
    row = conn.execute(
        """
        SELECT t.* FROM tasks t
        JOIN bots b ON b.id = t.bot_id
        WHERE t.state = 'queued'
          AND b.status IN ('idle', 'degraded')
          AND b.domain = 'system'
        ORDER BY t.priority DESC, t.created_at ASC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        # Fallback: any project task.
        row = conn.execute(
            """
            SELECT t.* FROM tasks t
            JOIN bots b ON b.id = t.bot_id
            WHERE t.state = 'queued'
              AND b.status IN ('idle', 'degraded')
            ORDER BY t.priority DESC, t.created_at ASC
            LIMIT 1
            """
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_next_core_task() -> dict | None:
    """One runnable task for a core (system) bot, or None. Priority DESC, created_at ASC."""
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT t.* FROM tasks t
        JOIN bots b ON b.id = t.bot_id
        WHERE t.state = 'queued'
          AND b.status IN ('idle', 'degraded')
          AND b.domain = 'system'
        ORDER BY t.priority DESC, t.created_at ASC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_next_tasks_with_core_first(concurrency: int, core_slots: int = 1) -> list[dict]:
    """
    Up to `concurrency` runnable tasks, at most one per bot.
    Reserves up to `core_slots` for core (system) tasks when available so kernel improvement is fast-paced.
    Then fills remaining slots by priority DESC, created_at ASC.
    """
    if concurrency < 1:
        return []
    out: list[dict] = []
    core_bots_used: set[int] = set()
    for _ in range(min(core_slots, concurrency)):
        core_task = get_next_core_task()
        if not core_task or core_task["bot_id"] in core_bots_used:
            break
        core_bots_used.add(int(core_task["bot_id"]))
        out.append(core_task)
    if len(out) >= concurrency:
        return out
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT t.* FROM tasks t
        JOIN bots b ON b.id = t.bot_id
        WHERE t.state = 'queued'
          AND b.status IN ('idle', 'degraded')
        ORDER BY t.priority DESC, t.created_at ASC
        """
    ).fetchall()
    conn.close()
    seen_bots: set[int] = {int(t["bot_id"]) for t in out}
    for r in rows:
        if len(out) >= concurrency:
            break
        bid = int(r["bot_id"])
        if bid in seen_bots:
            continue
        seen_bots.add(bid)
        out.append(dict(r))
    return out


def get_next_tasks(concurrency: int) -> list[dict]:
    """
    Up to `concurrency` runnable tasks, at most one per bot.
    Core and projects have equal priority; all run in parallel.
    Order: priority DESC, created_at ASC.
    """
    if concurrency < 1:
        return []
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT t.* FROM tasks t
        JOIN bots b ON b.id = t.bot_id
        WHERE t.state = 'queued'
          AND b.status IN ('idle', 'degraded')
        ORDER BY t.priority DESC, t.created_at ASC
        """
    ).fetchall()
    conn.close()
    out: list[dict] = []
    seen_bots: set[int] = set()
    for r in rows:
        if len(out) >= concurrency:
            break
        bid = int(r["bot_id"])
        if bid in seen_bots:
            continue
        seen_bots.add(bid)
        out.append(dict(r))
    return out


def count_queued_tasks() -> int:
    """Number of tasks in state 'queued'."""
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE state = 'queued'").fetchone()
    conn.close()
    return row["n"] if row else 0


def list_tasks(
    bot_id: int | None = None,
    state: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """List tasks with optional filter by bot_id and/or state. Includes bot_name. Ordered by priority DESC, created_at ASC."""
    conn = _get_conn()
    q = "SELECT t.*, b.name AS bot_name FROM tasks t JOIN bots b ON b.id = t.bot_id ORDER BY t.priority DESC, t.created_at ASC LIMIT ?"
    params: list = [limit]
    if bot_id is not None and state is not None:
        q = "SELECT t.*, b.name AS bot_name FROM tasks t JOIN bots b ON b.id = t.bot_id WHERE t.bot_id = ? AND t.state = ? ORDER BY t.priority DESC, t.created_at ASC LIMIT ?"
        params = [bot_id, state, limit]
    elif bot_id is not None:
        q = "SELECT t.*, b.name AS bot_name FROM tasks t JOIN bots b ON b.id = t.bot_id WHERE t.bot_id = ? ORDER BY t.priority DESC, t.created_at ASC LIMIT ?"
        params = [bot_id, limit]
    elif state is not None:
        q = "SELECT t.*, b.name AS bot_name FROM tasks t JOIN bots b ON b.id = t.bot_id WHERE t.state = ? ORDER BY t.priority DESC, t.created_at ASC LIMIT ?"
        params = [state, limit]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_task_state(task_id: int, state: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET state = ?, updated_at = datetime('now') WHERE id = ?",
        (state, task_id),
    )
    conn.commit()
    conn.close()


def mark_task_done(task_id: int) -> None:
    """Convenience: set task state to done."""
    set_task_state(task_id, "done")


def mark_task_running(task_id: int) -> None:
    """Convenience: set task state to running (e.g. when distributed master claims a task)."""
    set_task_state(task_id, "running")


def mark_task_failed(task_id: int) -> None:
    """Convenience: set task state to failed."""
    set_task_state(task_id, "failed")


def mark_task_error(task_id: int, _error: str = "") -> None:
    """Convenience: set task state to failed (for distributed worker reporting)."""
    set_task_state(task_id, "failed")


def get_running_task_id_for_bot(bot_id: int) -> int | None:
    """Return the task id of the bot's currently running task, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM tasks WHERE bot_id = ? AND state = 'running' LIMIT 1",
        (bot_id,),
    ).fetchone()
    conn.close()
    return int(row["id"]) if row else None


# --- Prompt blocks / policies ---
def get_prompt_block(name: str, scope: str) -> dict | None:
    """Return the prompt block for name+scope, or None if not found."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM prompt_blocks WHERE name = ? AND scope = ? AND enabled = 1 ORDER BY version DESC LIMIT 1",
        (name, scope),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_prompt_blocks(scope: str) -> list[dict]:
    """Return all enabled prompt blocks for a given scope (e.g. 'core')."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM prompt_blocks WHERE scope = ? AND enabled = 1 ORDER BY name, version DESC",
        (scope,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_prompt_block(name: str, scope: str, content: str, version: int = 1, enabled: bool = True) -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM prompt_blocks WHERE name = ? AND scope = ?",
        (name, scope),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE prompt_blocks SET content = ?, version = ?, enabled = ?, updated_at = datetime('now') WHERE id = ?",
            (content, version, 1 if enabled else 0, row["id"]),
        )
        conn.commit()
        rowid = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO prompt_blocks (name, scope, content, version, enabled) VALUES (?, ?, ?, ?, ?)",
            (name, scope, content, version, 1 if enabled else 0),
        )
        conn.commit()
        rowid = cur.lastrowid
    conn.close()
    return rowid


def get_prompt_policy(scope: str) -> dict | None:
    """Return latest enabled prompt policy for scope, parsed from JSON; or None."""
    import json

    conn = _get_conn()
    row = conn.execute(
        "SELECT policy_json FROM prompt_policies WHERE scope = ? AND enabled = 1 ORDER BY id DESC LIMIT 1",
        (scope,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row["policy_json"])
    except Exception:
        return None


def upsert_prompt_policy(scope: str, policy_json: str, enabled: bool = True) -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM prompt_policies WHERE scope = ?",
        (scope,),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE prompt_policies SET policy_json = ?, enabled = ?, updated_at = datetime('now') WHERE id = ?",
            (policy_json, 1 if enabled else 0, row["id"]),
        )
        conn.commit()
        rowid = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO prompt_policies (scope, policy_json, enabled) VALUES (?, ?, ?)",
            (scope, policy_json, 1 if enabled else 0),
        )
        conn.commit()
        rowid = cur.lastrowid
    conn.close()
    return rowid


def reset_stuck_running() -> tuple[int, int]:
    """
    Recovery: set any bot stuck in 'running' to 'idle', and any task stuck in 'running' to 'queued'.
    Returns (bots_reset, tasks_reset).
    """
    conn = _get_conn()
    cur_b = conn.execute("UPDATE bots SET status = 'idle', updated_at = datetime('now') WHERE status = 'running'")
    bots_reset = cur_b.rowcount
    cur_t = conn.execute("UPDATE tasks SET state = 'queued', updated_at = datetime('now') WHERE state = 'running'")
    tasks_reset = cur_t.rowcount
    conn.commit()
    conn.close()
    return (bots_reset, tasks_reset)


# --- Architecture state / capabilities ---
def upsert_architecture_state(component: str, summary: str = "", interfaces: str = "", known_issues: str = "", metrics_json: dict | None = None) -> int:
    import json

    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM architecture_state WHERE component = ?",
        (component,),
    ).fetchone()
    metrics = json.dumps(metrics_json or {}) if not isinstance(metrics_json, str) else metrics_json
    if row:
        conn.execute(
            """
            UPDATE architecture_state
            SET summary = ?, interfaces = ?, known_issues = ?, metrics_json = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (summary, interfaces, known_issues, metrics, row["id"]),
        )
        conn.commit()
        rowid = row["id"]
    else:
        cur = conn.execute(
            """
            INSERT INTO architecture_state (component, summary, interfaces, known_issues, metrics_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (component, summary, interfaces, known_issues, metrics),
        )
        conn.commit()
        rowid = cur.lastrowid
    conn.close()
    return rowid


def list_architecture_state() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM architecture_state ORDER BY component").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_capability(capability: str, limits_json: dict | None = None, status: str = "ok") -> int:
    import json

    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM capabilities_registry WHERE capability = ?",
        (capability,),
    ).fetchone()
    limits = json.dumps(limits_json or {}) if not isinstance(limits_json, str) else limits_json
    if row:
        conn.execute(
            """
            UPDATE capabilities_registry
            SET limits_json = ?, status = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (limits, status, row["id"]),
        )
        conn.commit()
        rowid = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO capabilities_registry (capability, limits_json, status) VALUES (?, ?, ?)",
            (capability, limits, status),
        )
        conn.commit()
        rowid = cur.lastrowid
    conn.close()
    return rowid


def list_capabilities() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM capabilities_registry ORDER BY capability").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Model policies / health ---
def get_model_policy(scope: str) -> dict | None:
    """Return latest enabled model policy for a scope, parsed from JSON; or None."""
    import json

    conn = _get_conn()
    row = conn.execute(
        "SELECT policy_json FROM model_policies WHERE scope = ? AND enabled = 1 ORDER BY id DESC LIMIT 1",
        (scope,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row["policy_json"])
    except Exception:
        return None


def upsert_model_policy(scope: str, policy_json: str, enabled: bool = True) -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM model_policies WHERE scope = ?",
        (scope,),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE model_policies SET policy_json = ?, enabled = ?, updated_at = datetime('now') WHERE id = ?",
            (policy_json, 1 if enabled else 0, row["id"]),
        )
        conn.commit()
        rowid = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO model_policies (scope, policy_json, enabled) VALUES (?, ?, ?)",
            (scope, policy_json, 1 if enabled else 0),
        )
        conn.commit()
        rowid = cur.lastrowid
    conn.close()
    return rowid


def upsert_model_health(
    model_name: str,
    failure_rate: float | None = None,
    compile_error_rate: float | None = None,
    avg_latency_ms: float | None = None,
    last_failure_at: str | None = None,
    metadata_json: dict | None = None,
) -> int:
    """Upsert basic health metrics for a model. Values may be partially provided."""
    import json

    conn = _get_conn()
    row = conn.execute(
        "SELECT id, failure_rate, compile_error_rate, avg_latency_ms, last_failure_at, metadata_json FROM model_health WHERE model_name = ?",
        (model_name,),
    ).fetchone()
    if row:
        # Merge with existing values where new values are None.
        fr = failure_rate if failure_rate is not None else row["failure_rate"]
        cer = compile_error_rate if compile_error_rate is not None else row["compile_error_rate"]
        lat = avg_latency_ms if avg_latency_ms is not None else row["avg_latency_ms"]
        lfail = last_failure_at if last_failure_at is not None else row["last_failure_at"]
        if metadata_json is None:
            meta = row["metadata_json"]
        else:
            meta = json.dumps(metadata_json)
        conn.execute(
            """
            UPDATE model_health
            SET failure_rate = ?, compile_error_rate = ?, avg_latency_ms = ?, last_failure_at = ?, metadata_json = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (fr, cer, lat, lfail, meta, row["id"]),
        )
        conn.commit()
        rowid = row["id"]
    else:
        meta = json.dumps(metadata_json or {})
        cur = conn.execute(
            """
            INSERT INTO model_health (model_name, failure_rate, compile_error_rate, avg_latency_ms, last_failure_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (model_name, failure_rate, compile_error_rate, avg_latency_ms, last_failure_at, meta),
        )
        conn.commit()
        rowid = cur.lastrowid
    conn.close()
    return rowid


def get_model_health(model_name: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM model_health WHERE model_name = ?",
        (model_name,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Upgrades (backlog / plans) ---
def insert_upgrade_backlog(title: str, problem: str = "", proposal_text: str = "", priority: int = 0, status: str = "new") -> int:
    conn = _get_conn()
    cur = conn.execute(
        """
        INSERT INTO upgrade_backlog (title, problem, proposal_text, priority, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, problem, proposal_text, priority, status),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def update_upgrade_backlog(upgrade_id: int, status: str | None = None, priority: int | None = None) -> None:
    conn = _get_conn()
    if status is not None and priority is not None:
        conn.execute(
            "UPDATE upgrade_backlog SET status = ?, priority = ?, updated_at = datetime('now') WHERE id = ?",
            (status, priority, upgrade_id),
        )
    elif status is not None:
        conn.execute(
            "UPDATE upgrade_backlog SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, upgrade_id),
        )
    elif priority is not None:
        conn.execute(
            "UPDATE upgrade_backlog SET priority = ?, updated_at = datetime('now') WHERE id = ?",
            (priority, upgrade_id),
        )
    conn.commit()
    conn.close()


def list_upgrades(limit: int = 50, status: str | None = None) -> list[dict]:
    conn = _get_conn()
    if status is not None:
        rows = conn.execute(
            """
            SELECT * FROM upgrade_backlog
            WHERE status = ?
            ORDER BY priority DESC, created_at DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM upgrade_backlog
            ORDER BY priority DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def insert_upgrade_plan(upgrade_id: int, plan_json: str, acceptance_criteria: str = "", status: str = "planned") -> int:
    conn = _get_conn()
    cur = conn.execute(
        """
        INSERT INTO upgrade_plans (upgrade_id, plan_json, acceptance_criteria, status)
        VALUES (?, ?, ?, ?)
        """,
        (upgrade_id, plan_json, acceptance_criteria, status),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def list_upgrade_plans(upgrade_id: int | None = None, limit: int = 50) -> list[dict]:
    conn = _get_conn()
    if upgrade_id is not None:
        rows = conn.execute(
            "SELECT * FROM upgrade_plans WHERE upgrade_id = ? ORDER BY created_at DESC LIMIT ?",
            (upgrade_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM upgrade_plans ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Messages ---
def insert_message(bot_id: int, role: str, content: str, meta_json: dict | None = None) -> int:
    conn = _get_conn()
    meta = json.dumps(meta_json) if meta_json else None
    cur = conn.execute(
        "INSERT INTO messages (bot_id, role, content, meta_json) VALUES (?, ?, ?, ?)",
        (bot_id, role, content, meta),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def get_messages_for_bot(bot_id: int, limit: int | None = None) -> list[dict]:
    limit = limit or config.MAX_CONTEXT_MESSAGES
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE bot_id = ? ORDER BY id DESC LIMIT ?",
        (bot_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


# --- Runs ---
def get_last_iteration(bot_id: int) -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COALESCE(MAX(iteration), 0) AS it FROM runs WHERE bot_id = ?",
        (bot_id,),
    ).fetchone()
    conn.close()
    return row["it"] if row else 0


def get_best_run(bot_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM runs WHERE bot_id = ? AND status IN ('ok', 'repaired') AND score IS NOT NULL ORDER BY score DESC LIMIT 1",
        (bot_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def insert_run(
    bot_id: int,
    task_id: int | None,
    iteration: int,
    sandbox_path: str,
    code_path: str,
    stdout: str,
    stderr: str,
    returncode: int,
    duration_ms: int,
    score: float | None,
    status: str,
    meta_json: dict | None = None,
) -> int:
    conn = _get_conn()
    meta = json.dumps(meta_json) if meta_json else None
    cur = conn.execute("""
        INSERT INTO runs (bot_id, task_id, iteration, sandbox_path, code_path, stdout, stderr, returncode, duration_ms, score, status, meta_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (bot_id, task_id, iteration, sandbox_path, code_path, stdout, stderr, returncode, duration_ms, score, status, meta))
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def list_runs(
    bot_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """List runs, most recent first. Optional filter by bot_id."""
    conn = _get_conn()
    if bot_id is not None:
        rows = conn.execute(
            "SELECT * FROM runs WHERE bot_id = ? ORDER BY id DESC LIMIT ?",
            (bot_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Artifacts ---
# --- Proposals (self-improve) ---
def insert_proposal(bot_id: int, content: str, run_id: int | None = None) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO proposals (bot_id, run_id, content, applied) VALUES (?, ?, ?, 0)",
        (bot_id, run_id, content),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def get_proposals(bot_id: int | None = None, applied: bool | None = None) -> list[dict]:
    conn = _get_conn()
    if bot_id is not None and applied is not None:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE bot_id = ? AND applied = ? ORDER BY id DESC",
            (bot_id, 1 if applied else 0),
        ).fetchall()
    elif bot_id is not None:
        rows = conn.execute("SELECT * FROM proposals WHERE bot_id = ? ORDER BY id DESC", (bot_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM proposals ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_proposal_applied(proposal_id: int, bot_id: int | None = None) -> None:
    conn = _get_conn()
    conn.execute("UPDATE proposals SET applied = 1 WHERE id = ?", (proposal_id,))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS proposal_applied_log (id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER NOT NULL, bot_id INTEGER, applied_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute("INSERT INTO proposal_applied_log (proposal_id, bot_id) VALUES (?, ?)", (proposal_id, bot_id or 0))
    except Exception:
        pass
    conn.commit()
    conn.close()


def get_one_unapplied_proposal() -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM proposals WHERE applied = 0 ORDER BY id ASC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_recent_run_outcomes(limit: int = 5) -> list[dict]:
    """Last N runs with task_type, status, score for outcome feedback in prompts."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT r.id, r.bot_id, r.status, r.score, r.created_at, t.task_type FROM runs r LEFT JOIN tasks t ON r.task_id = t.id ORDER BY r.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        rows = conn.execute("SELECT id, bot_id, status, score, created_at FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) | {"task_type": "code"} for r in rows]
    conn.close()
    return [dict(r) for r in rows]


def get_proposal_score(proposal_id: int, window_hours: float = 24.0) -> float | None:
    """Success rate of runs for the bot that had this proposal applied, after application. Returns 0..1 or None if no data."""
    conn = _get_conn()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS proposal_applied_log (id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER NOT NULL, bot_id INTEGER, applied_at TEXT DEFAULT (datetime('now')))"
        )
        row = conn.execute(
            "SELECT bot_id, applied_at FROM proposal_applied_log WHERE proposal_id = ? ORDER BY id DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
    except Exception:
        conn.close()
        return None
    if not row:
        conn.close()
        return None
    bot_id = row["bot_id"]
    applied_at = row["applied_at"] or ""
    # Count runs for this bot after applied_at with status ok vs total
    if applied_at:
        rows = conn.execute(
            "SELECT status FROM runs WHERE bot_id = ? AND datetime(created_at) >= datetime(?) LIMIT 500",
            (bot_id, applied_at),
        ).fetchall()
    else:
        rows = conn.execute("SELECT status FROM runs WHERE bot_id = ? ORDER BY id DESC LIMIT 500", (bot_id,)).fetchall()
    conn.close()
    if not rows:
        return None
    ok = sum(1 for r in rows if (r["status"] or "").lower() in ("ok", "repaired"))
    return ok / len(rows)


def update_bot_system_prompt(bot_id: int, system_prompt: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE bots SET system_prompt = ?, updated_at = datetime('now') WHERE id = ?",
        (system_prompt, bot_id),
    )
    conn.commit()
    conn.close()


def update_bot_model(bot_id: int, model: str) -> None:
    """Set the Ollama model this bot uses. Engine will ensure_model() on next run."""
    conn = _get_conn()
    conn.execute(
        "UPDATE bots SET model = ?, updated_at = datetime('now') WHERE id = ?",
        (model.strip(), bot_id),
    )
    conn.commit()
    conn.close()


# --- Creations ---
def insert_creation(run_id: int, bot_id: int, type_: str, path: str, title: str = "", run_cmd: str = "", meta_json: dict | None = None) -> int:
    conn = _get_conn()
    meta = json.dumps(meta_json) if meta_json else None
    cur = conn.execute(
        "INSERT INTO creations (run_id, bot_id, type, title, path, run_cmd, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, bot_id, type_, title or type_, path, run_cmd or "", meta),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def list_creations(limit: int = 200, bot_id: int | None = None) -> list[dict]:
    conn = _get_conn()
    if bot_id is not None:
        rows = conn.execute(
            "SELECT * FROM creations WHERE bot_id = ? ORDER BY id DESC LIMIT ?", (bot_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM creations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_creation(creation_id: int | None = None, path: str | None = None) -> dict | None:
    conn = _get_conn()
    if creation_id:
        row = conn.execute("SELECT * FROM creations WHERE id = ?", (creation_id,)).fetchone()
    elif path:
        row = conn.execute("SELECT * FROM creations WHERE path = ?", (path,)).fetchone()
    else:
        conn.close()
        return None
    conn.close()
    return dict(row) if row else None


def update_creation_path(creation_id: int, path: str, run_cmd: str = "") -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE creations SET path = ?, run_cmd = ? WHERE id = ?",
        (path, run_cmd, creation_id),
    )
    conn.commit()
    conn.close()


# --- Artifacts ---
# --- Models (self-created / external; used in parallel by bots) ---
def insert_model(name: str, source_type: str = "ollama", endpoint_url: str | None = None, base_model: str | None = None) -> int:
    """Register a model. If name exists, updates it. Returns row id."""
    conn = _get_conn()
    name = name.strip()
    row = conn.execute("SELECT id FROM models WHERE name = ?", (name,)).fetchone()
    if row:
        conn.execute(
            "UPDATE models SET source_type = ?, endpoint_url = ?, base_model = ?, created_at = datetime('now') WHERE name = ?",
            (source_type, endpoint_url, base_model, name),
        )
        conn.commit()
        rowid = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO models (name, source_type, endpoint_url, base_model) VALUES (?, ?, ?, ?)",
            (name, source_type, endpoint_url, base_model),
        )
        conn.commit()
        rowid = cur.lastrowid
    conn.close()
    return rowid


def get_model(name: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM models WHERE name = ?", (name.strip(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_models() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM models ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Training examples (for training new models from runs) ---
def insert_training_example(
    prompt: str,
    response: str,
    score: float | None = None,
    run_id: int | None = None,
    bot_id: int | None = None,
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO training_examples (prompt, response, score, run_id, bot_id) VALUES (?, ?, ?, ?, ?)",
        (prompt, response, score, run_id, bot_id),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def list_training_examples(limit: int = 1000, bot_id: int | None = None) -> list[dict]:
    conn = _get_conn()
    if bot_id is not None:
        rows = conn.execute(
            "SELECT * FROM training_examples WHERE bot_id = ? ORDER BY id DESC LIMIT ?",
            (bot_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM training_examples ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
