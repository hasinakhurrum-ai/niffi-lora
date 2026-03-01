"""
Niffi TUI: strict 3-layer model (MAIN_STREAM / PROJECT_STREAM / PROMPT_MODE).
Stream-first, no command bar in stream mode; prompt only when [i] pressed.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.binding import Binding
from textual.widgets import Static, Input, ListItem, ListView, Label, Button, TextArea, RichLog
from textual.screen import ModalScreen
from textual.message import Message

from .state import StreamState, Verbosity, next_verbosity
from . import events as tui_events
from .widgets import HeaderBar, NavBar, StreamView, FooterBar
from .screens.command_palette import CommandPalette
from .screens.data_screens import (
    TasksScreen,
    RunsScreen,
    AgentsScreen,
    CreationsScreen,
    ConfigScreen,
    ExploreProjectScreen,
)
from .screens.workspace_screen import ProjectWorkspaceScreen


# Project root (niffi directory)
NIFFI_ROOT = Path(__file__).resolve().parent.parent


def _get_clipboard_text() -> str | None:
    """Best-effort clipboard access for paste action. Returns text or None."""
    # Try optional pyperclip first if user has it installed.
    try:
        import pyperclip  # type: ignore[import-not-found]

        try:
            text = pyperclip.paste()
            if isinstance(text, str) and text:
                return text
        except Exception:
            pass
    except Exception:
        pass
    # Fallback: tkinter clipboard (standard library, may not be available in all environments).
    try:
        import tkinter as _tk  # type: ignore[import-not-found]

        root = _tk.Tk()
        root.withdraw()
        try:
            text = root.clipboard_get()
        finally:
            root.destroy()
        if isinstance(text, str) and text:
            return text
    except Exception:
        pass
    return None


def _set_clipboard_text(text: str) -> bool:
    """Best-effort clipboard writer. Returns True on apparent success."""
    if not text:
        return False
    # Prefer pyperclip if available.
    try:
        import pyperclip  # type: ignore[import-not-found]

        try:
            pyperclip.copy(text)
            return True
        except Exception:
            pass
    except Exception:
        pass
    # Fallback: tkinter clipboard.
    try:
        import tkinter as _tk  # type: ignore[import-not-found]

        root = _tk.Tk()
        root.withdraw()
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()  # keep clipboard after window closes
        finally:
            root.destroy()
        return True
    except Exception:
        pass
    return False


class StreamLine(Message):
    """Emitted when engine stdout has a new line (from subprocess reader thread)."""
    def __init__(self, line: str, project: str | None = None) -> None:
        self.line = line
        self.project = project
        super().__init__()


class HelpScreen(ModalScreen):
    """Help overlay: keyboard shortcuts and mouse usage. Click or press any key to close."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        help_text = """[bold]Niffi — Quick reference[/bold]

[bright_blue]Screens (all in one place):[/bright_blue]
  [1] Stream   [2] Tasks   [3] Runs   [4] Agents   [5] Creations   [6] Config   [7] Explore   [8] IDE
  [Ctrl+P]     All commands (switch project, add prompt, run creation, help, etc.)

[bright_blue]Keyboard shortcuts:[/bright_blue]
  [i]          Add instruction (task for current agent)
  [Tab]        Switch project
  [m] or [q]   Main view (all projects)
  [v]          Cycle verbosity
  [j] / [k]    Scroll down / up
  [g] / [G]    Jump to top / bottom
  [h] or [?]   This help   [t]   Toggle theme (dark/light)
  [Esc]        Close dialog or cancel
  [R]          Refresh (on data screens)   [G] in Explore: Run project

[bright_blue]Tips:[/bright_blue]
  • Nav bar shows 1–6 and Ctrl+P. Use them to open any screen.
  • In Tasks/Runs: Enter = view details. In Creations: Enter = run that creation.
  • In Explore (7): Run project (G) = compile and run latest creation; O = open folder.
  • [8] IDE = project workspace: file tree, editor, run. S = save file.
  • Timeline shows the full event stream."""
        yield Vertical(
            Static(help_text, id="help-content", markup=True),
            Button("Close", variant="primary", id="help-close"),
            id="help-container",
        )

    def on_mount(self) -> None:
        self.query_one("#help-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "help-close":
            self.dismiss(None)


class ProjectSelectorScreen(ModalScreen):
    """Tab: select project (bot) or Create New. Arrow + Enter; Esc to close without change."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def __init__(self, current: str, bot_names: list[str]) -> None:
        self.current = current
        self.bot_names = bot_names
        self._ids = list(bot_names) + ["__new__"]
        super().__init__()

    def compose(self) -> ComposeResult:
        items = [ListItem(Label(name)) for name in self.bot_names]
        items.append(ListItem(Label("+ Create New Project")))
        yield Vertical(
            Label("Select project — you'll work in this project environment", id="selector-title"),
            Static("↑↓ move  Enter select  Esc cancel. Then use Explore (7) to see files and Run project (G).", id="selector-hint"),
            ListView(*items, id="project-list"),
            id="selector-container",
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        lv = self.query_one("#project-list", ListView)
        idx = lv.index
        if idx is None or idx < 0 or idx >= len(self._ids):
            self.dismiss(None)
            return
        sel = self._ids[idx]
        if sel == "__new__":
            self.dismiss("__create_new__")
            return
        self.dismiss(sel)

    def on_mount(self) -> None:
        self.query_one("#project-list", ListView).focus()


class MenuOverlayScreen(ModalScreen):
    """Deprecated; kept for backward compatibility only (no longer used)."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def compose(self) -> ComposeResult:
        yield Vertical(Label("Menu overlay is no longer used. Press Esc.", id="overlay-menu-title"))

    def action_close(self) -> None:
        self.dismiss(None)


class ErrorLogScreen(ModalScreen[None]):
    """Logs viewer: full engine log or errors-only. R: Refresh, Esc: Close."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._current_mode: str = "all"

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Logs", id="error-log-title"),
            RichLog(id="error-log", highlight=True, markup=False),
            Horizontal(
                Button("All", id="log-btn-all", variant="primary"),
                Button("Errors only", id="log-btn-errors"),
                Button("Refresh", id="log-btn-refresh"),
                Button("Close", id="error-log-close"),
                id="log-buttons",
            ),
            id="error-log-container",
        )

    def on_mount(self) -> None:
        self._load_log(self._current_mode)

    def _log_paths(self) -> tuple[Path, Path, Path | None]:
        state = NIFFI_ROOT / "state"
        engine = state / "engine.log"
        daemon = state / "daemon.log"
        try:
            import config
            err_path = getattr(config, "ENGINE_ERROR_LOG_PATH", None)
            error_log = (state / "error.log") if err_path is None else Path(err_path)
        except Exception:
            error_log = state / "error.log"
        return engine, daemon, error_log if error_log else None

    def _load_log(self, mode: str) -> None:
        self._current_mode = mode
        engine_path, daemon_path, error_path = self._log_paths()
        log_widget = self.query_one("#error-log", RichLog)
        title_widget = self.query_one("#error-log-title", Label)
        log_widget.clear()
        if mode == "errors":
            if error_path and error_path.exists():
                title_widget.update("Errors only (state/error.log)")
                try:
                    with error_path.open("r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()[-300:]
                    for line in lines:
                        log_widget.write(line.rstrip("\n"))
                except Exception as e:
                    log_widget.write(f"Error reading log: {e}")
            else:
                title_widget.update("Errors only")
                log_widget.write(
                    "Errors-only log not available. Run the engine to generate state/error.log."
                )
            return
        path = engine_path if engine_path.exists() else daemon_path
        title_widget.update(
            "Full log (state/engine.log)" if path == engine_path else "Full log (state/daemon.log)"
        )
        try:
            if path.exists():
                with path.open("r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-500:]
                for line in lines:
                    log_widget.write(line.rstrip("\n"))
            else:
                log_widget.write(
                    "No log file found. Run the engine first."
                )
        except Exception as e:
            log_widget.write(f"Error reading log: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "error-log-close":
            self.dismiss(None)
        elif bid == "log-btn-all":
            self._load_log("all")
        elif bid == "log-btn-errors":
            self._load_log("errors")
        elif bid == "log-btn-refresh":
            self._load_log(self._current_mode)

    def action_refresh(self) -> None:
        self._load_log(self._current_mode)

    def action_close(self) -> None:
        self.dismiss(None)


def _build_project_system_prompt(instructions: str) -> str:
    """Include project goal in system prompt so the bot follows it for all tasks."""
    import prompts
    goal = (instructions or "Build a small Python project.").strip()
    return f"Project goal (follow for every task): {goal}\n\n{prompts.get_prompt('new_project_default', 'tui')}"


class NewProjectFormScreen(ModalScreen):
    """Create New Project: project name + instructions. Save creates bot, adds task, returns project name."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Create new project", id="new-project-title"),
            Static("Give your project a name and optional first task. After Save you'll see its live stream.", id="new-project-hint"),
            Label("Project name:", id="lbl-name"),
            Input(placeholder="e.g. my_app (required)", id="new-project-name"),
            Label("Instructions (first task):", id="lbl-instr"),
            TextArea(placeholder="What should the bot do first? Leave blank for a default task.", id="new-project-instructions", language=None),
            Horizontal(
                Button("Cancel", id="new-project-cancel"),
                Button("Save", variant="primary", id="new-project-save"),
                id="new-project-buttons",
            ),
            id="new-project-form",
        )

    def on_mount(self) -> None:
        self.query_one("#new-project-name", Input).focus()

    def _save(self) -> None:
        name_input = self.query_one("#new-project-name", Input)
        name = (name_input.value or "").strip().replace(" ", "_")
        if not name:
            name_input.placeholder = "Enter a project name (required)"
            return
        try:
            import db
            import config
            if db.get_bot(name=name):
                name_input.placeholder = "Name already exists, choose another"
                return
        except Exception:
            pass
        instructions = ""
        try:
            ta = self.query_one("#new-project-instructions", TextArea)
            instructions = (ta.text or "").strip()
        except Exception:
            pass
        try:
            import db
            import config
            import prompts
            system_prompt = _build_project_system_prompt(instructions)
            bot_id = db.insert_bot(name, "project", config.DEFAULT_MODEL, system_prompt)
            first_task = instructions or prompts.get_prompt("new_project_default", "tui")
            db.insert_task(bot_id, first_task, priority=getattr(config, "USER_TASK_PRIORITY", 5), task_type="code")
            self.dismiss(name)
        except Exception as e:
            name_input.placeholder = f"Error: {str(e)[:40]}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-project-save":
            self._save()
        elif event.button.id == "new-project-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RunCreationScreen(ModalScreen):
    """List creations for current project; choose one to run. Runs in background."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, project_name: str) -> None:
        self._project_name = project_name
        super().__init__()

    def compose(self) -> ComposeResult:
        try:
            import db
            bot = db.get_bot(name=self._project_name)
            bot_id = bot["id"] if bot else None
            creations = db.list_creations(limit=50, bot_id=bot_id) if bot_id else []
        except Exception:
            creations = []
        self._creations = creations
        self._ids = []
        if not creations:
            yield Vertical(
                Label(f"No creations yet for [bold]{self._project_name}[/bold]", id="run-creation-title", markup=True),
                Static("Run the engine to generate code, then use Run latest creation.", id="run-creation-hint"),
                Button("Close", id="run-creation-close"),
                id="run-creation-container",
            )
            return
        list_items = [
            ListItem(Label(f"#{c['id']}  {c.get('type', '')}  {(c.get('title') or '')[:40]}"))
            for c in creations
        ]
        self._ids = [c["id"] for c in creations]
        yield Vertical(
            Label(f"Run a creation — {self._project_name}", id="run-creation-title"),
            ListView(*list_items, id="run-creation-list"),
            Button("Close", id="run-creation-close"),
            id="run-creation-container",
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not getattr(self, "_ids", None):
            return
        lv = self.query_one("#run-creation-list", ListView)
        idx = lv.index
        if idx is None or idx < 0 or idx >= len(self._ids):
            self.dismiss(None)
            return
        self.dismiss(self._ids[idx])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-creation-close":
            self.dismiss(None)

    def on_mount(self) -> None:
        if getattr(self, "_ids", None):
            self.query_one("#run-creation-list", ListView).focus()
        else:
            self.query_one("#run-creation-close", Button).focus()


class AddPromptFormScreen(ModalScreen):
    """Add a new prompt/task to the current project. Save adds task and closes."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, project_name: str) -> None:
        self._project_name = project_name
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"Add prompt to [bold]{self._project_name}[/bold]", id="add-prompt-title", markup=True),
            Static("Add a new task. It will be queued and appear in the live stream.", id="add-prompt-hint"),
            Label("New instruction / task:", id="lbl-prompt"),
            TextArea(placeholder="What should the bot do next?", id="add-prompt-text", language=None),
            Horizontal(
                Button("Cancel", id="add-prompt-cancel"),
                Button("Save", variant="primary", id="add-prompt-save"),
                id="add-prompt-buttons",
            ),
            id="add-prompt-form",
        )

    def on_mount(self) -> None:
        self.query_one("#add-prompt-text", TextArea).focus()

    def _save(self) -> None:
        try:
            ta = self.query_one("#add-prompt-text", TextArea)
            text = (ta.text or "").strip()
        except Exception:
            text = ""
        if not text:
            return
        try:
            import db
            bot = db.get_bot(name=self._project_name)
            if bot:
                db.insert_task(bot["id"], text, priority=0, task_type="code")
                if (bot.get("status") or "").strip().lower() == "done":
                    db.set_bot_status(bot["id"], "idle")
                self.dismiss(text)
            else:
                self.dismiss(None)
        except Exception:
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-prompt-save":
            self._save()
        elif event.button.id == "add-prompt-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NiffiTUI(App[None]):
    """Main TUI app. Three states; prompt overlay on [i]; Tab = project selector."""

    TITLE = "Niffi"
    BINDINGS = [
        Binding("1", "show_stream", "Stream"),
        Binding("2", "show_tasks", "Tasks"),
        Binding("3", "show_runs", "Runs"),
        Binding("4", "show_agents", "Agents"),
        Binding("5", "show_creations", "Creations"),
        Binding("6", "show_config", "Config"),
        Binding("7", "show_explore_project", "Explore"),
        Binding("8", "show_workspace", "Workspace"),
        Binding("i", "instruct", "Instruct"),
        Binding("m", "main", "Main"),
        Binding("q", "main", "Main"),
        Binding("tab", "switch_project", "Switch project"),
        Binding("escape", "cancel_prompt", "Cancel"),
        Binding("v", "verbosity", "Verbosity"),
        Binding("j", "scroll_down", "Down"),
        Binding("k", "scroll_up", "Up"),
        Binding("g", "scroll_top", "Top"),
        Binding("G", "scroll_bottom", "Bottom"),
        Binding("h", "help", "Help"),
        Binding("?", "help", "Help"),
        Binding("ctrl+p", "command_palette", "Commands"),
        Binding("ctrl+v", "paste_clipboard", "Paste"),
        Binding("ctrl+shift+v", "paste_clipboard", "Paste"),
        Binding("ctrl+c", "copy_stream", "Copy"),
        Binding("t", "toggle_theme", "Theme"),
    ]

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

/* Global button styling: high-contrast on dark backgrounds */
Button {
    background: #111827;
    color: #e5e7eb;
    border: solid #4b5563;
    text-style: bold;
    padding: 0 2;
    min-width: 10;
}
Button:hover {
    background: #1f2937;
    color: #f9fafb;
}
Button.-primary {
    background: #2563eb;
    color: #f9fafb;
    border: solid #3b82f6;
}
Button.-primary:hover {
    background: #1d4ed8;
}

    /* Header: dark, professional, compact */
    HeaderBar#header {
        dock: top;
        height: 2;
        min-height: 2;
        padding: 0 2;
        background: #0b1220;
        color: #e2e8f0;
        text-style: bold;
        border-bottom: heavy #1f2937;
    }
    HeaderBar.mode-main {
        background: #020617;
    }
    HeaderBar.mode-project {
        background: #022c22;
    }

    #nav-bar {
        dock: top;
        height: 1;
        padding: 0 2;
        background: #0f172a;
        color: #94a3b8;
        border-bottom: solid #1e293b;
    }

    /* Stream area: primary activity + timeline */
    #stream-area {
        height: 1fr;
        border: solid $border;
        background: $background;
        padding: 0 1;
    }
    #primary-stream {
        height: 3fr;
        min-height: 1;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
    #timeline-divider {
        height: 1;
        color: #64748b;
        padding: 0 2;
    }
    #timeline-stream {
        height: 1fr;
        min-height: 1;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
    #stream-toolbar {
        height: 1;
        min-height: 1;
        padding: 0 1;
        background: #020617;
        color: #e5e7eb;
        border-top: solid #1f2937;
    }
    #stream-toolbar Button {
        margin-right: 1;
    }

    #footer {
        height: auto;
        min-height: 1;
        dock: bottom;
        padding: 0 1;
        background: #0b1220;
        color: #94a3b8;
        border-top: solid #1f2937;
    }
    #footer-hints {
        width: 100%;
    }
    #footer-prompt {
        width: 100%;
    }
    .hidden {
        display: none;
    }
    ProjectSelectorScreen {
        align: center middle;
    }
    #selector-container {
        width: 50;
        height: auto;
        border: solid #666;
        padding: 1 2;
        background: $surface;
    }
    #selector-title {
        text-style: bold;
        padding-bottom: 0;
    }
    #selector-hint {
        padding-bottom: 1;
        color: $text-muted;
    }
    /* Legacy menu screens kept for compatibility but not used in modern UX */
    NewProjectFormScreen, AddPromptFormScreen, RunCreationScreen {
        align: center middle;
    }
    #run-creation-container {
        width: 56;
        border: solid $primary;
        padding: 2;
        background: $surface;
    }
    #run-creation-title {
        padding-bottom: 1;
    }
    #run-creation-hint {
        color: $text-muted;
        padding-bottom: 1;
    }
    #run-creation-close {
        margin-top: 1;
    }
    #new-project-form, #add-prompt-form {
        width: 60;
        height: auto;
        max-height: 85%;
        border: solid $primary;
        padding: 2;
        background: $surface;
    }
    #new-project-title, #add-prompt-title {
        text-style: bold;
        padding-bottom: 0;
    }
    #new-project-hint, #add-prompt-hint {
        color: $text-muted;
        padding-bottom: 1;
    }
    #lbl-name, #lbl-instr, #lbl-prompt {
        padding-top: 1;
        color: $text;
    }
    #new-project-name {
        width: 1fr;
    }
    #new-project-instructions, #add-prompt-text {
        width: 1fr;
        height: 8;
        min-height: 4;
    }
    #new-project-buttons, #add-prompt-buttons {
        padding-top: 2;
        height: auto;
    }
    #new-project-buttons Button, #add-prompt-buttons Button {
        margin-right: 2;
    }
    HelpScreen {
        align: center middle;
    }
    #help-container {
        width: 60;
        height: auto;
        max-height: 80%;
        border: solid $primary;
        padding: 2;
        background: $surface;
    }
    #help-content {
        padding: 1 0;
        width: 1fr;
    }
    #help-close {
        margin-top: 1;
    }

    /* Project workspace (IDE-style) */
    ProjectWorkspaceScreen {
        layout: vertical;
    }
    #workspace-container {
        height: 1fr;
        min-height: 1;
        layout: horizontal;
    }
    #workspace-sidebar {
        width: 28;
        min-width: 20;
        height: 1fr;
        border-right: solid #334155;
        padding: 0 1;
        background: #0f172a;
    }
    #workspace-tree-hint {
        color: $text-muted;
        padding: 0 0 1 0;
    }
    #workspace-tree {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    #workspace-main {
        width: 1fr;
        height: 1fr;
        layout: vertical;
        padding: 0 1;
    }
    #workspace-editor-hint {
        color: $text-muted;
        padding: 0 0 1 0;
    }
    #workspace-editor {
        height: 1fr;
        min-height: 4;
        scrollbar-gutter: stable;
    }
    #workspace-output-title {
        padding: 1 0 0 0;
        color: $text-muted;
    }
    #workspace-output {
        height: 8;
        min-height: 4;
        max-height: 12;
        border: solid #334155;
        padding: 0 1;
    }
    #workspace-actions {
        height: auto;
        padding: 1 0;
        border-top: solid #334155;
    }
    #workspace-actions Button {
        margin-right: 2;
    }

    /* Command palette styling */
    CommandPalette {
        align: center middle;
    }
    CommandPalette #cmd-input {
        width: 60;
        border: solid #4b5563;
        background: #020617;
        color: #e5e7eb;
    }
    CommandPalette #cmd-list {
        width: 60;
        height: 8;
        border: solid #4b5563;
        background: #020617;
    }
    CommandPalette ListItem {
        padding: 0 2;
        color: #e5e7eb;
    }
    CommandPalette ListItem.--highlight {
        background: #1f2937;
        color: #f9fafb;
    }

    /* Data screens (Tasks, Runs, Bots, Creations, Config) */
    BaseDataScreen, TaskDetailScreen, RunDetailScreen {
        align: center middle;
    }
    .data-screen {
        width: 90;
        height: 85;
        border: solid #334155;
        padding: 1 2;
        background: #0f172a;
        border-title-align: left;
    }
    .data-screen .section-title {
        text-style: bold;
        color: #e2e8f0;
        padding-bottom: 0;
        margin-bottom: 0;
    }
    .data-screen .section-hint {
        color: #64748b;
        padding-bottom: 1;
        margin-bottom: 0;
    }
    .data-screen DataTable {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    .data-screen-actions {
        height: auto;
        padding-top: 1;
    }
    .data-screen-actions Button {
        margin-right: 2;
    }
    .agent-row {
        height: auto;
        min-height: 2;
        padding: 0 1;
        border-bottom: solid #334155;
        width: 100%;
    }
    .agent-name {
        min-width: 24;
        width: 24;
        color: #e2e8f0;
    }
    .agent-status {
        min-width: 10;
        width: 10;
    }
    .agent-status.status-idle { color: #94a3b8; }
    .agent-status.status-running { color: #22c55e; }
    .agent-status.status-paused { color: #eab308; }
    .agent-status.status-done { color: #3b82f6; }
    .agent-status.status-degraded { color: #f59e0b; }
    .agent-header { color: #64748b; border-bottom: heavy #475569; }
    .agent-actions-label { min-width: 14; color: #64748b; }
    .agent-row Button {
        margin-right: 2;
        min-width: 6;
    }
    #agents-scroll {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    AgentsScreen .data-screen {
        width: 100;
    }
    .detail-screen {
        width: 80;
        height: 80;
        border: solid #334155;
        padding: 1 2;
        background: #0f172a;
    }
    .detail-screen .section-title {
        text-style: bold;
        color: #e2e8f0;
        padding-bottom: 1;
    }
    .detail-screen RichLog {
        height: 1fr;
        scrollbar-size: 1 1;
        padding: 0 1;
    }

    /* Light theme overrides when app has -light-mode */
    .-light-mode Screen { background: #f1f5f9; }
    .-light-mode Button { background: #e2e8f0; color: #0f172a; border: solid #94a3b8; }
    .-light-mode Button:hover { background: #cbd5e1; color: #020617; }
    .-light-mode Button.-primary { background: #2563eb; color: #f8fafc; border: solid #3b82f6; }
    .-light-mode HeaderBar#header { background: #0f172a; color: #e2e8f0; border-bottom: heavy #334155; }
    .-light-mode #nav-bar { background: #1e293b; color: #64748b; border-bottom: solid #334155; }
    .-light-mode #stream-toolbar { background: #0f172a; color: #e5e7eb; border-top: solid #334155; }
    .-light-mode #footer { background: #0f172a; color: #64748b; border-top: solid #334155; }
    .-light-mode .data-screen { background: #f8fafc; border: solid #64748b; }
    .-light-mode .data-screen .section-title { color: #0f172a; }
    .-light-mode .data-screen .section-hint { color: #475569; }
    .-light-mode .agent-row { background: #f1f5f9; border-bottom: solid #e2e8f0; padding: 0 1; }
    .-light-mode .agent-name { color: #0f172a; }
    .-light-mode .agent-status.status-idle { color: #64748b; }
    .-light-mode .agent-status.status-running { color: #16a34a; }
    .-light-mode .agent-status.status-paused { color: #ca8a04; }
    .-light-mode .agent-status.status-done { color: #2563eb; }
    .-light-mode .agent-status.status-degraded { color: #d97706; }
    """


    def __init__(self, run_engine: bool = True) -> None:
        super().__init__()
        self._state = StreamState.MAIN_STREAM
        self._current_project = ""
        self._previous_state: StreamState | None = None
        self._verbosity = Verbosity.SUMMARY
        self._run_engine = run_engine
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._source_watch_stop = False
        self._source_watcher_thread: threading.Thread | None = None
        self._instruction_history: list[str] = []
        self._project_run_locks: dict[str, threading.Lock] = {}
        self._live_unseen = 0
        self._timeline_at_bottom = True
        self._light_theme = False

    def action_toggle_theme(self) -> None:
        """Switch between dark and light theme."""
        self._light_theme = not self._light_theme
        if self._light_theme:
            self.add_class("-light-mode")
        else:
            self.remove_class("-light-mode")

    @property
    def state(self) -> StreamState:
        return self._state

    @state.setter
    def state(self, value: StreamState) -> None:
        self._state = value
        self._sync_widgets_state()

    @property
    def current_project(self) -> str:
        return self._current_project

    @current_project.setter
    def current_project(self, value: str) -> None:
        self._current_project = value or ""
        self._sync_widgets_state()

    def _sync_widgets_state(self) -> None:
        try:
            primary = self.query_one("#primary-stream", StreamView)
            timeline = self.query_one("#timeline-stream", StreamView)
            footer = self.query_one(FooterBar)
            header = self.query_one(HeaderBar)
        except Exception:
            return
        for stream in (primary, timeline):
            stream.stream_state = self._state
            stream.current_project = self._current_project
        footer.state = self._state
        footer.current_project = self._current_project

        # Header reflects MAIN vs PROJECT and project name; counts derived from DB.
        header.state = self._state
        header.current_project = self._current_project
        try:
            import db as _db

            bots = _db.list_bots()
            projects = [b for b in bots if (b.get("domain") or "") != "system"]
            header.projects_count = len(projects)
            header.running_count = sum(1 for b in bots if (b.get("status") or "") == "running")
            header.tasks_count = _db.count_queued_tasks()
        except Exception:
            header.projects_count = 0
            header.running_count = 0
            header.tasks_count = 0

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        yield NavBar(id="nav-bar")

        # Primary activity panel + timeline with subtle divider and toolbar
        with Vertical(id="stream-area"):
            yield StreamView(id="primary-stream")
            yield Static("── Timeline ──", id="timeline-divider")
            yield StreamView(id="timeline-stream")
            with Horizontal(id="stream-toolbar"):
                yield Button("Copy", id="stream-copy")
                yield Button("Paste", id="stream-paste")

        yield FooterBar(id="footer")

    def _ensure_db_and_bots(self) -> None:
        """Ensure DB is initialized and default bots exist so Agents screen and project selector work."""
        try:
            import db
            db.init_schema()
            if not db.list_bots():
                from main import register_default_bots
                register_default_bots()
        except Exception:
            pass

    def on_mount(self) -> None:
        os.chdir(NIFFI_ROOT)
        self._ensure_db_and_bots()
        self._sync_widgets_state()
        self.set_interval(1.5, self._sync_widgets_state)
        # Welcome message so the stream is never empty and users know what to do
        welcome = "[TUI] Welcome to Niffi. Press [h] for help or Ctrl+P for commands. Activity from the engine will appear here."
        self.query_one("#timeline-stream", StreamView).append_event(welcome)
        # Cursor-style: if no project selected, show project selector after a tick so user picks a project first
        if not self._current_project:
            def _maybe_show_project_selector() -> None:
                try:
                    import db
                    bots = db.list_bots()
                    project_bots = [b for b in bots if (b.get("domain") or "") != "system"]
                    names = [b.get("name") or f"bot_{b['id']}" for b in project_bots]
                    if names:
                        self.push_screen(
                            ProjectSelectorScreen(self._current_project, names),
                            self._on_project_selected,
                        )
                except Exception:
                    pass
            self.set_timer(0.2, _maybe_show_project_selector)
        if self._run_engine:
            self._start_engine_subprocess()
            self._start_source_watcher()

    def _start_engine_subprocess(self) -> None:
        """Start main.py in subprocess and stream stdout to TUI."""
        cwd = str(NIFFI_ROOT)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-u", "main.py"],
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            self.call_from_thread(
                lambda: self.query_one(StreamView).append_event(f"[TUI] Failed to start engine: {e}")
            )
            return
        self._reader_thread = threading.Thread(
            target=self._read_engine_stdout,
            daemon=True,
        )
        self._reader_thread.start()

    def _restart_engine_subprocess(self) -> None:
        """Terminate current engine subprocess and start a new one (call from main thread)."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        try:
            sv = self.query_one("#timeline-stream", StreamView)
            sv.append_event("[TUI] Source changed, restarting engine...")
        except Exception:
            pass
        self._start_engine_subprocess()

    def _source_watcher_loop(self) -> None:
        """Background loop: poll source mtimes; on change, restart engine via call_from_thread."""
        import time
        try:
            import config
            interval = getattr(config, "TUI_RELOAD_POLL_INTERVAL_S", 2)
        except Exception:
            interval = 2
        root = Path(NIFFI_ROOT)
        paths = list(root.glob("*.py")) + (list((root / "tui").glob("*.py")) if (root / "tui").exists() else [])
        paths = [p for p in paths if p.is_file()]
        last_mtime = None
        while not self._source_watch_stop:
            try:
                mtimes = []
                for p in paths:
                    try:
                        mtimes.append(p.stat().st_mtime)
                    except OSError:
                        pass
                current = max(mtimes) if mtimes else None
                if last_mtime is not None and current is not None and current != last_mtime:
                    self.call_from_thread(self._restart_engine_subprocess)
                if current is not None:
                    last_mtime = current
            except Exception:
                pass
            for _ in range(int(interval * 10)):
                if self._source_watch_stop:
                    break
                time.sleep(0.1)

    def _start_source_watcher(self) -> None:
        """Start background thread that watches source files and restarts engine on change."""
        try:
            import config
            if not getattr(config, "TUI_RELOAD_ENGINE_ON_SOURCE_CHANGE", True):
                return
        except Exception:
            pass
        self._source_watch_stop = False
        self._source_watcher_thread = threading.Thread(target=self._source_watcher_loop, daemon=True)
        self._source_watcher_thread.start()

    def _read_engine_stdout(self) -> None:
        if not self._process or not self._process.stdout:
            return
        last_project = [None]
        for line in iter(self._process.stdout.readline, ""):
            line = line.rstrip("\n\r")
            if not line:
                continue
            proj, _ = tui_events.parse_project_tag(line)
            if proj:
                last_project[0] = proj
            p = last_project[0]
            self.call_from_thread((lambda l, pr: lambda: self.post_message(StreamLine(l, pr)))(line, p))

    def on_stream_line(self, event: StreamLine) -> None:
        line = event.line
        project = getattr(event, "project", None)

        # Update header activity based on lifecycle keywords
        try:
            header = self.query_one(HeaderBar)
            txt = line.upper()
            if "THINK" in txt:
                header_text = "THINKING"
            elif "PLAN" in txt:
                header_text = "PLANNING"
            elif "CODE" in txt:
                header_text = "CODING"
            elif "EXEC" in txt:
                header_text = "EXECUTING"
            elif "TEST" in txt:
                header_text = "TESTING"
            elif "BUILD" in txt:
                header_text = "BUILDING"
            elif "ERROR" in txt:
                header_text = "ERROR"
            else:
                header_text = header.autonomy  # fall back to autonomy label
            # Reuse autonomy field as a simple activity indicator in the header.
            header.autonomy = header_text
        except Exception:
            pass

        primary = self.query_one("#primary-stream", StreamView)
        timeline = self.query_one("#timeline-stream", StreamView)

        # Primary: focus on most recent key lifecycle lines
        if any(kw in line for kw in ["THINK", "PLAN", "CODE", "EXEC", "TEST", "BUILD", "ERROR"]):
            primary.clear_current_stream()
            primary.append_event(line, project)
        else:
            primary.append_event(line, project)

        # Timeline: always append
        timeline.append_event(line, project)

        # Run-when-done hint: when a creation is registered, show how to run it
        if "[CREATION]" in line and "id=" in line:
            try:
                hint = "[TUI] Creation ready. Press 5 for Creations then Enter to run, or use Run latest."
                timeline.append_event(hint, project)
            except Exception:
                pass

        # Live indicator in footer when user has scrolled away from bottom.
        try:
            footer = self.query_one(FooterBar)
            if self._timeline_at_bottom:
                self._live_unseen = 0
                footer.show_live_indicator(0)
            else:
                self._live_unseen += 1
                footer.show_live_indicator(self._live_unseen)
        except Exception:
            pass

    def action_instruct(self) -> None:
        if self._state == StreamState.PROMPT_MODE:
            return
        self._previous_state = self._state
        self._state = StreamState.PROMPT_MODE
        self._sync_widgets_state()
        self.query_one("#footer-prompt", Input).value = ""
        self.query_one("#footer-prompt", Input).focus()

    def action_main(self) -> None:
        self._state = StreamState.MAIN_STREAM
        self._current_project = ""
        self._sync_widgets_state()
        self.query_one("#timeline-stream", StreamView).append_event("[TUI] Viewing all projects. Main stream continues in background.")

    def action_switch_project(self) -> None:
        try:
            import db
            bots = db.list_bots()
            # Only show non-core bots as selectable "projects".
            project_bots = [b for b in bots if (b.get("domain") or "") != "system"]
            names = [b.get("name") or f"bot_{b['id']}" for b in project_bots]
        except Exception:
            names = []
        if not names:
            names = ["(no projects)"]
        self.push_screen(ProjectSelectorScreen(self._current_project, names), self._on_project_selected)

    def _on_project_selected(self, result: str | None) -> None:
        if result is None or result == "(no projects)":
            return
        if result == "__create_new__":
            self.push_screen(NewProjectFormScreen(), self._on_new_project_created)
            return
        self._current_project = result
        self._state = StreamState.PROJECT_STREAM
        self._sync_widgets_state()
        self.query_one("#timeline-stream", StreamView).append_event(f"[{result}] [TUI] Switched to this project. Stream shows only this project.")

    def _on_new_project_created(self, project_name: str | None) -> None:
        if not project_name:
            return
        self._current_project = project_name
        self._state = StreamState.PROJECT_STREAM
        self._sync_widgets_state()
        self.query_one("#timeline-stream", StreamView).append_event(f"[{project_name}] [TUI] Created project. Switched to its live stream.")

    def action_verbosity(self) -> None:
        self._verbosity = next_verbosity(self._verbosity)
        self._sync_widgets_state()

    def action_scroll_down(self) -> None:
        try:
            container = self.query_one("#timeline-stream", StreamView)
            container.scroll_relative(y=1)
            self._timeline_at_bottom = False
        except Exception:
            pass

    def action_scroll_up(self) -> None:
        try:
            container = self.query_one("#timeline-stream", StreamView)
            container.scroll_relative(y=-1)
            self._timeline_at_bottom = False
        except Exception:
            pass

    def action_scroll_top(self) -> None:
        try:
            container = self.query_one("#timeline-stream", StreamView)
            container.scroll_home()
            self._timeline_at_bottom = False
        except Exception:
            pass

    def action_scroll_bottom(self) -> None:
        try:
            container = self.query_one("#timeline-stream", StreamView)
            container.scroll_end()
            self._timeline_at_bottom = True
            self._live_unseen = 0
            footer = self.query_one(FooterBar)
            footer.show_live_indicator(0)
        except Exception:
            pass

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_copy_stream(self) -> None:
        """Copy the current timeline view buffer to the OS clipboard."""
        try:
            timeline = self.query_one("#timeline-stream", StreamView)
            text = timeline.get_all_text()
        except Exception:
            text = ""
        if not text:
            try:
                self.query_one(StreamView).append_event("[TUI] Nothing to copy from timeline.")
            except Exception:
                pass
            return
        ok = _set_clipboard_text(text)
        try:
            msg = (
                "[TUI] Copied timeline to clipboard."
                if ok
                else "[TUI] Failed to access clipboard to copy timeline."
            )
            self.query_one(StreamView).append_event(msg)
        except Exception:
            pass

    def action_paste_clipboard(self) -> None:
        """Paste OS clipboard into the active instruction input when in PROMPT_MODE."""
        # Only paste into the footer prompt when we're in instruction mode.
        if self._state != StreamState.PROMPT_MODE:
            return
        text = _get_clipboard_text()
        if not text:
            try:
                self.query_one(StreamView).append_event("[TUI] Clipboard is empty or unavailable for paste.")
            except Exception:
                pass
            return
        try:
            inp = self.query_one("#footer-prompt", Input)
            current = inp.value or ""
            inp.value = current + text
            # Ensure the input still has focus so user can press Enter.
            inp.focus()
        except Exception:
            try:
                self.query_one(StreamView).append_event("[TUI] Failed to paste clipboard into instruction input.")
            except Exception:
                pass

    def action_command_palette(self) -> None:
        """Open the command palette (Ctrl+P)."""
        self.push_screen(CommandPalette(), self._on_command_selected)

    def action_show_stream(self) -> None:
        """Focus stream (already on main view)."""
        if self._state == StreamState.PROMPT_MODE:
            self._cancel_prompt()
        try:
            self.query_one("#timeline-stream", StreamView).focus()
        except Exception:
            pass

    def action_show_tasks(self) -> None:
        """Open Tasks screen."""
        bot_id = None
        if self._current_project:
            try:
                import db
                bot = db.get_bot(name=self._current_project)
                if bot:
                    bot_id = bot["id"]
            except Exception:
                pass
        self.push_screen(TasksScreen(bot_id=bot_id))

    def action_show_runs(self) -> None:
        """Open Runs screen."""
        bot_id = None
        if self._current_project:
            try:
                import db
                bot = db.get_bot(name=self._current_project)
                if bot:
                    bot_id = bot["id"]
            except Exception:
                pass
        self.push_screen(RunsScreen(bot_id=bot_id))

    def action_show_agents(self) -> None:
        """Open Running agents screen (pause/resume/start/stop, view stream)."""
        self.push_screen(AgentsScreen(), self._on_agents_screen_done)

    def action_show_creations(self) -> None:
        """Open Creations screen. Selecting a row runs that creation."""
        self.push_screen(CreationsScreen(), self._on_creations_screen_done)

    def _on_creations_screen_done(self, result: int | None) -> None:
        if result is not None:
            self._run_creation_in_background(result)

    def _on_agents_screen_done(self, result: None | tuple[str, str]) -> None:
        if result is not None and len(result) == 2 and result[0] == "view_stream":
            bot_name = result[1]
            self._current_project = bot_name
            self._state = StreamState.PROJECT_STREAM
            self._sync_widgets_state()
            try:
                for stream in (self.query_one("#primary-stream", StreamView), self.query_one("#timeline-stream", StreamView)):
                    stream.current_project = bot_name
                    stream.stream_state = StreamState.PROJECT_STREAM
                    stream._refresh_from_current_view()
            except Exception:
                pass

    def action_show_config(self) -> None:
        """Open Config screen."""
        self.push_screen(ConfigScreen())

    def action_show_explore_project(self) -> None:
        """Open Explore project screen for current project."""
        self.push_screen(ExploreProjectScreen(self._current_project or ""))

    def action_show_workspace(self) -> None:
        """Open IDE-style project workspace (file tree, editor, output)."""
        if not self._current_project:
            try:
                self.query_one(StreamView).append_event(
                    "[TUI] Select a project first (Tab), then press 8 for workspace."
                )
            except Exception:
                pass
            return
        try:
            from sandbox import get_project_root
            root = get_project_root(self._current_project)
            if not root.exists():
                try:
                    root.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
            self.push_screen(ProjectWorkspaceScreen(self._current_project, root))
        except Exception as e:
            try:
                self.query_one(StreamView).append_event(f"[TUI] Workspace error: {e}")
            except Exception:
                pass

    def _on_command_selected(self, result: str | None) -> None:
        """Dispatch a selected command palette entry to an action."""
        if not result:
            return
        if result == "Stream (home)":
            self.action_show_stream()
        elif result == "Tasks":
            self.action_show_tasks()
        elif result == "Runs":
            self.action_show_runs()
        elif result in ("Bots", "Running agents"):
            self.action_show_agents()
        elif result == "Creations":
            self.action_show_creations()
        elif result == "Config":
            self.action_show_config()
        elif result == "Explore project (paths, files, Run project)":
            self.action_show_explore_project()
        elif result == "Project workspace (IDE: tree, editor, run)":
            self.action_show_workspace()
        elif result == "Switch project":
            self.action_switch_project()
        elif result == "Create project":
            self.push_screen(NewProjectFormScreen(), self._on_new_project_created)
        elif result == "Add prompt":
            self.action_add_prompt()
        elif result == "Run latest creation":
            self.action_run_latest()
        elif result == "Run creation (choose)":
            self.action_run_choose()
        elif result == "Main view":
            self.action_main()
        elif result == "Help":
            self.action_help()
        elif result in ("Show logs", "Show error log"):
            self.action_show_error_log()
        elif result == "Toggle theme":
            self.action_toggle_theme()

    def action_open_menu(self) -> None:
        # Legacy binding; prefer Ctrl+P (command_palette) instead.
        self.action_command_palette()

    def action_cancel_prompt(self) -> None:
        if self._state == StreamState.PROMPT_MODE:
            self._cancel_prompt()

    def _run_menu_action(self, action_key: str) -> None:
        if action_key == "instruct":
            self.action_instruct()
        elif action_key == "switch_project":
            self.action_switch_project()
        elif action_key == "main":
            self.action_main()
        elif action_key == "verbosity":
            self.action_verbosity()
        elif action_key == "help":
            self.action_help()
        elif action_key == "add_prompt":
            self.action_add_prompt()
        elif action_key == "run_latest":
            self.action_run_latest()
        elif action_key == "run_choose":
            self.action_run_choose()

    def action_show_error_log(self) -> None:
        """Open a modal window showing the tail of the engine's error log."""
        self.push_screen(ErrorLogScreen())

    def action_run_latest(self) -> None:
        if not self._current_project:
            self.query_one(StreamView).append_event("[TUI] Switch to a project first, then use Run latest creation.")
            return
        try:
            import db
            bot = db.get_bot(name=self._current_project)
            if not bot:
                self.query_one(StreamView).append_event(f"[{self._current_project}] [TUI] Project not found.")
                return
            creations = db.list_creations(limit=1, bot_id=bot["id"])
            if not creations:
                self.query_one(StreamView).append_event(f"[{self._current_project}] [TUI] No creations yet. Run tasks to generate code.")
                return
            cid = creations[0]["id"]
            self._run_creation_in_background(cid)
        except Exception as e:
            self.query_one(StreamView).append_event(f"[TUI] Error: {e}")

    def action_run_choose(self) -> None:
        if not self._current_project:
            self.query_one(StreamView).append_event("[TUI] Switch to a project first.")
            return
        self.push_screen(RunCreationScreen(self._current_project), self._on_run_creation_selected)

    def _on_run_creation_selected(self, creation_id: int | None) -> None:
        if creation_id is not None:
            self._run_creation_in_background(creation_id)

    def run_project_for(self, project_name: str, compile_first: bool = True) -> None:
        """Compile (optional) then run project from its sandbox (single workspace). Used from Explore/Workspace."""
        def do_run() -> None:
            def stream(msg: str) -> None:
                self.call_from_thread(lambda m=msg: self._append_stream_event(m))

            try:
                if compile_first:
                    stream(f"[TUI] Compiling generated modules for {project_name}...")
                    r = subprocess.run(
                        [sys.executable, "main.py", "--compile-only"],
                        cwd=NIFFI_ROOT,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if r.returncode != 0:
                        stream(f"[TUI] Compile warning: {r.stderr or r.stdout or 'non-zero exit'}")
                    else:
                        stream("[TUI] Compile done.")
                from sandbox import get_project_workspace, ensure_bot_venv
                workspace = get_project_workspace(project_name)
                python_exe = ensure_bot_venv(project_name)
                entry = workspace / "candidate.py" if (workspace / "candidate.py").exists() else workspace / "main.py"
                if not entry.exists():
                    stream(f"[TUI] No candidate.py or main.py in project workspace. Run tasks to generate code.")
                    return
                stream(f"[TUI] Running in sandbox: {entry.name}")
                env = os.environ.copy()
                project_root = NIFFI_ROOT
                env["PYTHONPATH"] = str(project_root)
                proc = subprocess.Popen(
                    [str(python_exe), str(entry)],
                    cwd=str(workspace),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                for line in proc.stdout or []:
                    self.call_from_thread(lambda m=line.rstrip(): self._append_stream_event(m))
                proc.wait()
                stream(f"[TUI] Sandbox run finished (exit {proc.returncode}).")
            except Exception as e:
                self.call_from_thread(
                    lambda: self.query_one(StreamView).append_event(f"[TUI] Run project failed: {e}")
                )
        t = threading.Thread(target=do_run, daemon=True)
        t.start()

    def _append_stream_event(self, line: str) -> None:
        """Append a line to the primary stream (safe from any thread)."""
        try:
            self.query_one(StreamView).append_event(line)
        except Exception:
            pass

    def _run_creation_in_background_sync(self, creation_id: int, project: str) -> None:
        """Run creation in current thread (used from run_project_for)."""
        if project not in self._project_run_locks:
            self._project_run_locks[project] = threading.Lock()
        with self._project_run_locks[project]:
            subprocess.run(
                [sys.executable, "showcase.py", "run", str(creation_id)],
                cwd=NIFFI_ROOT,
                capture_output=False,
            )

    def _run_creation_in_background(self, creation_id: int, project: str | None = None) -> None:
        project = project or self._current_project or f"creation_{creation_id}"
        if project not in self._project_run_locks:
            self._project_run_locks[project] = threading.Lock()

        def run() -> None:
            lock = self._project_run_locks[project]
            lock.acquire()
            try:
                subprocess.run(
                    [sys.executable, "showcase.py", "run", str(creation_id)],
                    cwd=NIFFI_ROOT,
                    capture_output=False,
                )
                self.call_from_thread(
                    lambda: self.query_one(StreamView).append_event(
                        f"[TUI] Finished running creation {creation_id}."
                    )
                )
            except Exception as e:
                self.call_from_thread(
                    lambda: self.query_one(StreamView).append_event(f"[TUI] Run failed: {e}")
                )
            finally:
                lock.release()

        self.query_one(StreamView).append_event(f"[TUI] Running creation {creation_id}...")
        t = threading.Thread(target=run, daemon=True)
        t.start()

    def action_add_prompt(self) -> None:
        if not self._current_project:
            self.query_one(StreamView).append_event("[TUI] Switch to a project first, then use Actions → Add prompt.")
            return
        self.push_screen(AddPromptFormScreen(self._current_project), self._on_add_prompt_done)

    def _on_add_prompt_done(self, prompt_text: str | None) -> None:
        if prompt_text:
            self.query_one(StreamView).append_event(f"[TUI] Prompt added to {self._current_project}. It will appear in the live stream.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = getattr(event.button, "id", None)
        if bid == "stream-copy":
            self.action_copy_stream()
        elif bid == "stream-paste":
            self.action_paste_clipboard()
        elif bid == "menu-actions":
            actions_list = [("Instruct", "instruct")]
            if self._current_project:
                actions_list.append(("Add prompt", "add_prompt"))
                actions_list.extend([("Run latest creation", "run_latest"), ("Choose creation to run...", "run_choose")])
            actions_list.append(("Switch project", "switch_project"))
            self.push_screen(
                SubmenuScreen("Actions", actions_list),
                self._on_submenu_selected,
            )
        elif bid == "menu-view":
            self.push_screen(
                SubmenuScreen("View", [("Main", "main"), ("Verbosity", "verbosity")]),
                self._on_submenu_selected,
            )
        elif bid == "stream-clear":
            self.query_one(StreamView).clear_current_stream()
        elif bid == "menu-help":
            self.action_help()
        elif bid == "menu-overlay-trigger":
            self.push_screen(MenuOverlayScreen(bool(self._current_project)), self._on_submenu_selected)

    def _on_submenu_selected(self, action_key: str | None) -> None:
        if action_key:
            self._run_menu_action(action_key)

    def _submit_instruction(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self.state = self._previous_state or StreamState.MAIN_STREAM
            self._sync_widgets_state()
            return
        self._instruction_history.append(text)
        try:
            import db
            bots = db.list_bots()
            if self._current_project:
                bot = db.get_bot(name=self._current_project)
            else:
                # In MAIN view (no current project), default to the core/system bot if present.
                bot = None
                for b in bots:
                    if (b.get("domain") or "") == "system":
                        bot = b
                        break
                if bot is None and bots:
                    bot = bots[0]
            if bot:
                import config
                from instruction_utils import infer_task_type_from_instruction
                domain = (bot.get("domain") or "").strip()
                if domain == "system":
                    task_type = "self_improve"
                    priority = getattr(config, "CORE_TASK_PRIORITY", 3)
                else:
                    task_type = infer_task_type_from_instruction(text)
                    if task_type not in getattr(config, "PROJECT_TASK_TYPES", ()):
                        task_type = "code"
                    priority = getattr(config, "USER_TASK_PRIORITY", 5)
                db.insert_task(bot["id"], text, priority=priority, task_type=task_type)
                if (bot.get("status") or "").strip().lower() == "done":
                    db.set_bot_status(bot["id"], "idle")
                    self.query_one(StreamView).append_event(f"[TUI] Project reactivated; instruction queued for {bot.get('name', 'bot')} ({task_type}): {text[:80]}...")
                else:
                    self.query_one(StreamView).append_event(f"[TUI] Instruction added for {bot.get('name', 'bot')} ({task_type}): {text[:80]}...")
            else:
                self.query_one(StreamView).append_event("[TUI] No bot available to add task.")
        except Exception as e:
            self.query_one(StreamView).append_event(f"[TUI] Failed to add task: {e}")
        self._state = self._previous_state or StreamState.MAIN_STREAM
        self._sync_widgets_state()

    def _cancel_prompt(self) -> None:
        self._state = self._previous_state or StreamState.MAIN_STREAM
        self._sync_widgets_state()
        self.query_one("#footer-prompt", Input).value = ""

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "footer-prompt":
            return
        self._submit_instruction(event.input.value)
        event.input.value = ""

    def on_key(self, event) -> None:
        if self._state == StreamState.PROMPT_MODE:
            if event.key == "escape":
                self._cancel_prompt()
                event.prevent_default()
                event.stop()
            elif event.key == "ctrl+u":
                inp = self.query_one("#footer-prompt", Input)
                if inp.has_focus:
                    inp.value = ""
                    event.prevent_default()
