"""
Data screens: Tasks, Runs, Running agents, Creations, Config.
Consistent styling, empty states, refresh, Esc to close.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Label,
    RichLog,
    Static,
)
from textual.reactive import reactive

# Lazy db import per screen to avoid circular import
def _db():
    import db
    return db


# --- Shared styles (applied via classes) ---
SECTION_TITLE = "bold"
EMPTY_COLOR = "#64748b"
STATUS_QUEUED = "#3b82f6"
STATUS_RUNNING = "#eab308"
STATUS_DONE = "#22c55e"
STATUS_FAILED = "#ef4444"
STATUS_IDLE = "#94a3b8"
STATUS_DEGRADED = "#f97316"


class BaseDataScreen(ModalScreen[None]):
    """Base for data screens: title, hint, Esc to close."""
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("r", "refresh", "Refresh"),
    ]

    title_text = reactive("")
    hint_text = reactive("")

    def compose(self) -> ComposeResult:
        with Vertical(classes="data-screen"):
            yield Static(self.title_text, id="data-screen-title", classes="section-title")
            yield Static(self.hint_text, id="data-screen-hint", classes="section-hint")
            yield from self._compose_body()

    def _compose_body(self) -> ComposeResult:
        """Override in subclasses to add table/content and buttons."""
        return
        yield

    def action_close(self) -> None:
        self.dismiss(None)


class TasksScreen(BaseDataScreen):
    """Tasks list: id, bot, state, priority, type, prompt snippet. Refresh with R."""
    title_text = "Tasks"
    hint_text = "Enter: view details  R: refresh  Esc: close"

    def __init__(self, bot_id: int | None = None) -> None:
        super().__init__()
        self._bot_id = bot_id

    def _compose_body(self) -> ComposeResult:
        self.table = DataTable(id="tasks-table", cursor_type="row", zebra_stripes=True)
        self.table.add_columns("ID", "Bot", "State", "Pri", "Type", "Prompt")
        yield self.table
        with Horizontal(classes="data-screen-actions"):
            yield Button("Refresh", id="btn-refresh", variant="primary")
            yield Button("Close", id="btn-close")

    def on_mount(self) -> None:
        self._load()

    def _load(self) -> None:
        self.table.clear()
        try:
            db = _db()
            tasks = db.list_tasks(bot_id=self._bot_id, limit=200)
            if not tasks:
                self.table.add_row("—", "—", "—", "—", "—", "No tasks. Add one with [i] or from Stream.")
            else:
                for t in tasks:
                    tid = str(t.get("id", ""))
                    bot = (t.get("bot_name") or t.get("bot_id") or "")
                    state = (t.get("state") or "queued")
                    pri = str(t.get("priority", 0))
                    ttype = (t.get("task_type") or "code")[:12]
                    prompt = (t.get("prompt") or "")[:50] + ("..." if len(t.get("prompt") or "") > 50 else "")
                    self.table.add_row(tid, bot, state, pri, ttype, prompt)
        except Exception as e:
            self.table.add_row("—", "—", "—", "—", "—", f"Error: {e}")

    def action_refresh(self) -> None:
        self._load()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh":
            self.action_refresh()
        elif event.button.id == "btn-close":
            self.dismiss(None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        try:
            row = getattr(event, "row", None)
            if row is None:
                row = self.table.cursor_row
            db = _db()
            tasks = db.list_tasks(bot_id=self._bot_id, limit=200)
            if 0 <= row < len(tasks):
                t = tasks[row]
                self.app.push_screen(TaskDetailScreen(t), lambda _: None)
        except Exception:
            pass


class TaskDetailScreen(ModalScreen[None]):
    """Show full task: id, bot, state, type, full prompt."""
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, task: dict) -> None:
        super().__init__()
        self._task = task

    def compose(self) -> ComposeResult:
        t = self._task
        with Vertical(classes="detail-screen"):
            yield Static("Task details", id="detail-title", classes="section-title")
            yield Static(f"ID: {t.get('id')}  Bot: {t.get('bot_name', t.get('bot_id'))}  State: {t.get('state')}  Type: {t.get('task_type')}", id="detail-meta")
            log = RichLog(id="detail-content", highlight=True, markup=True)
            log.write((t.get("prompt") or "(no prompt)"))
            yield log
            yield Button("Close", id="btn-close")

    def action_dismiss(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close":
            self.dismiss(None)


class RunsScreen(BaseDataScreen):
    """Runs list: id, bot_id, task_id, score, status, duration. Refresh with R."""
    title_text = "Runs"
    hint_text = "Enter: view stdout/stderr  R: refresh  Esc: close"

    def __init__(self, bot_id: int | None = None) -> None:
        super().__init__()
        self._bot_id = bot_id

    def _compose_body(self) -> ComposeResult:
        self.table = DataTable(id="runs-table", cursor_type="row", zebra_stripes=True)
        self.table.add_columns("ID", "Bot", "Task", "Score", "Status", "Duration")
        yield self.table
        with Horizontal(classes="data-screen-actions"):
            yield Button("Refresh", id="btn-refresh", variant="primary")
            yield Button("Close", id="btn-close")

    def on_mount(self) -> None:
        self._load()

    def _load(self) -> None:
        self.table.clear()
        try:
            db = _db()
            runs = db.list_runs(bot_id=self._bot_id, limit=100)
            bots = {b["id"]: b.get("name", "") for b in db.list_bots()}
            if not runs:
                self.table.add_row("—", "—", "—", "—", "—", "No runs yet. Execute tasks to see runs here.")
            else:
                for r in runs:
                    rid = str(r.get("id", ""))
                    bot = bots.get(r.get("bot_id") or 0, str(r.get("bot_id", "")))
                    task_id = str(r.get("task_id") or "—")
                    score = str(r.get("score") if r.get("score") is not None else "—")
                    status = (r.get("status") or "—")[:12]
                    dur = r.get("duration_ms")
                    dur_str = f"{dur} ms" if dur is not None else "—"
                    self.table.add_row(rid, bot, task_id, score, status, dur_str)
        except Exception as e:
            self.table.add_row("—", "—", "—", "—", "—", str(e))

    def action_refresh(self) -> None:
        self._load()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh":
            self.action_refresh()
        elif event.button.id == "btn-close":
            self.dismiss(None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        try:
            row = getattr(event, "row", None)
            if row is None:
                row = self.table.cursor_row
            db = _db()
            runs = db.list_runs(bot_id=self._bot_id, limit=100)
            if 0 <= row < len(runs):
                r = runs[row]
                self.app.push_screen(RunDetailScreen(r), lambda _: None)
        except Exception:
            pass


class RunDetailScreen(ModalScreen[None]):
    """Show run stdout/stderr."""
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, run: dict) -> None:
        super().__init__()
        self._run = run

    def compose(self) -> ComposeResult:
        r = self._run
        with Vertical(classes="detail-screen"):
            yield Static(f"Run #{r.get('id')}  Score: {r.get('score')}  Status: {r.get('status')}", id="detail-title", classes="section-title")
            yield Static("stdout:", id="detail-stdout-label")
            log_out = RichLog(id="detail-stdout", highlight=True)
            log_out.write((r.get("stdout") or "(empty)")[:8000])
            yield log_out
            yield Static("stderr:", id="detail-stderr-label")
            log_err = RichLog(id="detail-stderr", highlight=True)
            log_err.write((r.get("stderr") or "(empty)")[:4000])
            yield log_err
            yield Button("Close", id="btn-close")

    def action_dismiss(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close":
            self.dismiss(None)


STATE_LABELS = {
    "idle": "Idle",
    "running": "Running",
    "paused": "Paused",
    "done": "Done",
    "degraded": "Degraded",
}


def _agent_state_label(status: str) -> str:
    """Return a clear state label for display."""
    s = (status or "idle").strip().lower()
    return STATE_LABELS.get(s, s[:12] if s else "Idle")


class AgentsScreen(BaseDataScreen):
    """Agents: name, state, actions. Shows what state each agent is in."""
    title_text = "Agents"
    hint_text = "State: Idle=ready, Running=executing, Paused=skipped, Done=finished. R: refresh  Esc: close"

    def __init__(self) -> None:
        super().__init__()
        self._bots: list[dict] = []

    def _compose_body(self) -> ComposeResult:
        yield ScrollableContainer(
            Vertical(id="agents-rows"),
            id="agents-scroll",
        )
        with Horizontal(classes="data-screen-actions"):
            yield Button("Refresh", id="btn-refresh", variant="primary")
            yield Button("Close", id="btn-close")

    def on_mount(self) -> None:
        self._load()
        self.set_interval(2.0, self._load)

    def on_unmount(self) -> None:
        # Interval is cleared automatically when screen is dismissed
        pass

    def _load(self) -> None:
        self._bots = []
        try:
            container = self.query_one("#agents-rows", Vertical)
            container.remove_children()
            db = _db()
            db.init_schema()
            self._bots = db.list_bots()
            if not self._bots:
                try:
                    from main import register_default_bots
                    register_default_bots()
                    self._bots = db.list_bots()
                except Exception:
                    pass
            if not self._bots:
                container.mount(Static("No agents. Start the engine (python main.py) or ensure the DB is initialized.", id="agents-empty"))
                return
            header = Horizontal(classes="agent-row agent-header")
            container.mount(header)
            header.mount(Static(" Agent ", classes="agent-name"))
            header.mount(Static(" State ", classes="agent-status"))
            header.mount(Static(" Actions ", classes="agent-actions-label"))
            for i, b in enumerate(self._bots):
                row = Horizontal(classes="agent-row")
                container.mount(row)
                self._mount_agent_row_content(row, i, b)
        except Exception as e:
            try:
                self.query_one("#agents-rows", Vertical).mount(Static(str(e), id="agents-error"))
            except Exception:
                pass

    def _mount_agent_row_content(self, row: Horizontal, index: int, bot: dict) -> None:
        """Row: agent name | state (clear label, colored) | Start/Stop | Live."""
        name = (bot.get("name") or "")[:24]
        status_raw = (bot.get("status") or "idle").strip().lower()
        state_label = _agent_state_label(status_raw)
        is_running = status_raw == "running"
        status_class = status_raw if status_raw in ("idle", "running", "paused", "done", "degraded") else "idle"
        row.mount(Static(f" {name} ", id=f"agent-{index}-name", classes="agent-name"))
        row.mount(
            Static(f" {state_label} ", id=f"agent-{index}-status", classes=f"agent-status status-{status_class}"),
        )
        if is_running:
            row.mount(Button("Stop", id=f"agent-{index}-stop", variant="error"))
        else:
            row.mount(Button("Start", id=f"agent-{index}-start"))
        row.mount(Button("Live", id=f"agent-{index}-live", variant="primary"))

    def _bot_for_button_id(self, button_id: str) -> tuple[dict | None, int | None]:
        if not button_id.startswith("agent-") or "-" not in button_id:
            return None, None
        parts = button_id.split("-")
        if len(parts) >= 2 and parts[1].isdigit():
            idx = int(parts[1])
            if 0 <= idx < len(self._bots):
                return self._bots[idx], idx
        return None, None

    def _do_start(self, bot: dict) -> None:
        try:
            import config
            db = _db()
            db.set_bot_status(bot["id"], "idle")
            import prompts
            domain = (bot.get("domain") or "").strip()
            if domain == "system":
                prompt = prompts.get_prompt("start_core", "tui")
                task_type = "self_improve"
                priority = getattr(config, "CORE_TASK_PRIORITY", 3)
            else:
                prompt = prompts.get_prompt("start_project", "tui")
                task_type = "code"
                priority = getattr(config, "USER_TASK_PRIORITY", 5)
            db.insert_task(bot["id"], prompt, priority=priority, task_type=task_type)
            name = (bot.get("name") or "agent")[:20]
            engine_running = False
            try:
                app = self.app
                if app and getattr(app, "_run_engine", False) and getattr(app, "_process", None):
                    p = app._process
                    engine_running = p is not None and p.poll() is None
            except Exception:
                pass
            if engine_running:
                self.notify(f"Task queued for {name}. Engine will run it shortly.", severity="information")
            else:
                self.notify(f"Task queued for {name}. Start the engine (python main.py) to run it.", severity="warning")
            self._load()
        except Exception as e:
            self.notify(f"Failed to start: {e}", severity="error")

    def _do_stop(self, bot: dict) -> None:
        try:
            db = _db()
            tid = db.get_running_task_id_for_bot(bot["id"])
            if tid is not None:
                db.set_task_state(tid, "failed")
            db.set_bot_status(bot["id"], "idle")
            self._load()
        except Exception:
            pass

    def _do_live(self, bot: dict) -> None:
        name = (bot.get("name") or "").strip()
        if name:
            self.dismiss(("view_stream", name))

    def action_refresh(self) -> None:
        self._load()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-refresh":
            self.action_refresh()
            return
        if bid == "btn-close":
            self.dismiss(None)
            return
        bot, _ = self._bot_for_button_id(bid)
        if not bot:
            return
        if bid.endswith("-start"):
            self._do_start(bot)
        elif bid.endswith("-stop"):
            self._do_stop(bot)
        elif bid.endswith("-live"):
            self._do_live(bot)


class CreationsScreen(BaseDataScreen):
    """Creations list: id, type, title, path. Enter to run. Refresh with R."""
    title_text = "Creations"
    hint_text = "Enter: run creation  R: refresh  Esc: close"

    def _compose_body(self) -> ComposeResult:
        self.table = DataTable(id="creations-table", cursor_type="row", zebra_stripes=True)
        self.table.add_columns("ID", "Type", "Title", "Path")
        yield self.table
        with Horizontal(classes="data-screen-actions"):
            yield Button("Refresh", id="btn-refresh", variant="primary")
            yield Button("Close", id="btn-close")

    def on_mount(self) -> None:
        self._load()

    def _load(self) -> None:
        self.table.clear()
        try:
            db = _db()
            creations = db.list_creations(limit=100)
            if not creations:
                self.table.add_row("—", "—", "—", "No creations yet. Run tasks to generate code.")
            else:
                for c in creations:
                    cid = str(c.get("id", ""))
                    typ = (c.get("type") or "")[:14]
                    title = (c.get("title") or "")[:28]
                    path = (c.get("path") or "")[:44]
                    self.table.add_row(cid, typ, title, path)
        except Exception as e:
            self.table.add_row("—", "—", "—", str(e))

    def action_refresh(self) -> None:
        self._load()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh":
            self.action_refresh()
        elif event.button.id == "btn-close":
            self.dismiss(None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        try:
            row = getattr(event, "row", None)
            if row is None:
                row = self.table.cursor_row
            db = _db()
            creations = db.list_creations(limit=100)
            if 0 <= row < len(creations):
                cid = creations[row].get("id")
                if cid is not None:
                    self.dismiss(cid)  # App can run this creation
        except Exception:
            pass


class ConfigScreen(BaseDataScreen):
    """Read-only config snapshot. R: refresh."""
    title_text = "Config"
    hint_text = "R: refresh  Esc: close"

    def _compose_body(self) -> ComposeResult:
        self._config_log = RichLog(id="config-content", highlight=True, markup=False)
        yield self._config_log
        with Horizontal(classes="data-screen-actions"):
            yield Button("Refresh", id="btn-refresh", variant="primary")
            yield Button("Close", id="btn-close")

    def on_mount(self) -> None:
        self._load()

    def _load(self) -> None:
        self._config_log.clear()
        try:
            import config as cfg
            safe = (
                "OLLAMA_URL", "DEFAULT_MODEL", "BOTS_CONCURRENCY", "TIMEOUT_S",
                "SERVER_TIMEOUT_S", "MAX_CONTEXT_MESSAGES", "STATE_DIR", "DB_PATH",
                "PROJECTS_DIR", "GENERATED_MODULES_DIR", "PRINT_ASK_RESPONSE_CODE",
                "STREAM_LLM", "OS_NAME", "IS_WINDOWS",
            )
            for key in safe:
                if hasattr(cfg, key):
                    val = getattr(cfg, key)
                    self._config_log.write(f"{key} = {val!r}")
        except Exception as e:
            self._config_log.write(f"Error: {e}")

    def action_refresh(self) -> None:
        self._load()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh":
            self.action_refresh()
        elif event.button.id == "btn-close":
            self.dismiss(None)


def _open_folder(path: str) -> bool:
    """Open path in OS file manager. Returns True on success."""
    import os
    import subprocess
    import sys
    p = path.strip()
    if not p or not os.path.isdir(p):
        return False
    try:
        if os.name == "nt":
            os.startfile(os.path.normpath(p))
            return True
        if sys.platform == "darwin":
            subprocess.run(["open", p], check=False, timeout=5, capture_output=True)
            return True
        subprocess.run(["xdg-open", p], check=False, timeout=5, capture_output=True)
        return True
    except Exception:
        return False


class ExploreProjectScreen(ModalScreen[None]):
    """Explore current project: root path, run/creation paths, file list, Open folder."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open_folder", "Open folder"),
        Binding("g", "run_project", "Run project"),
    ]

    def __init__(self, project_name: str) -> None:
        super().__init__()
        self._project_name = project_name or ""

    def compose(self) -> ComposeResult:
        with Vertical(classes="data-screen"):
            yield Static("Explore project", id="data-screen-title", classes="section-title")
            yield Static("O: open folder  R: refresh  G: run project  Esc: close", id="data-screen-hint", classes="section-hint")
            self._content = RichLog(id="explore-content", highlight=True, markup=False)
            yield self._content
            with Horizontal(classes="data-screen-actions"):
                yield Button("Run project", id="btn-run-project", variant="primary")
                yield Button("Open folder", id="btn-open-root")
                yield Button("Refresh", id="btn-refresh")
                yield Button("Close", id="btn-close")

    def on_mount(self) -> None:
        self._load()

    def _load(self) -> None:
        self._content.clear()
        if not self._project_name:
            self._content.write("No project selected. Switch to a project (Tab) first.")
            return
        try:
            from pathlib import Path
            from sandbox import get_project_root
            db = _db()
            bot = db.get_bot(name=self._project_name)
            if not bot:
                self._content.write(f"Project '{self._project_name}' not found.")
                return
            root = get_project_root(self._project_name)
            root_str = str(root.resolve())
            self._content.write(f"[Project root]\n{root_str}\n")
            if not root.exists():
                self._content.write("(directory does not exist yet)\n")
            else:
                from sandbox import get_project_workspace
                workspace = get_project_workspace(self._project_name)
                self._content.write(f"\n[Sandbox workspace]\n{workspace.resolve()}\n")
                if workspace.exists():
                    try:
                        for e in sorted(workspace.iterdir())[:30]:
                            self._content.write(f"  {'[dir]' if e.is_dir() else ''}  {e.name}")
                    except Exception:
                        pass
                creations = db.list_creations(limit=20, bot_id=bot["id"])
                if creations:
                    self._content.write("[Creations]")
                    for c in creations:
                        path = (c.get("path") or "").strip()
                        cid = c.get("id", "")
                        title = (c.get("title") or c.get("type") or "")[:40]
                        if path:
                            self._content.write(f"  id={cid}  {title}\n    {path}")
                    self._content.write("")
                try:
                    entries = sorted(root.iterdir())[:50]
                    self._content.write("[Project root files]")
                    for e in entries:
                        name = e.name
                        if e.is_dir():
                            self._content.write(f"  [dir]  {name}")
                        else:
                            self._content.write(f"  {name}")
                except Exception:
                    pass
        except Exception as e:
            self._content.write(f"Error: {e}")

    def action_refresh(self) -> None:
        self._load()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_open_folder(self) -> None:
        """Open project root in OS file manager (same as button)."""
        try:
            from sandbox import get_project_root
            root = get_project_root(self._project_name)
            if root.exists():
                _open_folder(str(root.resolve()))
            else:
                self._content.write("\n[Project folder does not exist yet]")
        except Exception as e:
            self._content.write(f"\n[Error: {e}]")

    def action_run_project(self) -> None:
        """Compile and run this project (run latest creation). Call into app."""
        if not self._project_name:
            self._content.write("\n[Select a project first (Tab).]")
            return
        app = getattr(self, "app", None)
        if app and hasattr(app, "run_project_for"):
            self._content.write("\n[Compiling and running project...]")
            app.run_project_for(self._project_name)
            self.dismiss(None)
        else:
            self._content.write("\n[Run not available.]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-run-project":
            self.action_run_project()
        elif event.button.id == "btn-open-root":
            try:
                from sandbox import get_project_root
                root = get_project_root(self._project_name)
                if root.exists():
                    ok = _open_folder(str(root.resolve()))
                    if not ok:
                        self._content.write("\n[Could not open folder]")
                else:
                    self._content.write("\n[Opened folder]")
            except Exception as e:
                self._content.write(f"\n[Error: {e}]")
        elif event.button.id == "btn-refresh":
            self.action_refresh()
        elif event.button.id == "btn-close":
            self.dismiss(None)
