"""
Project workspace (IDE-style): file tree, editor, output.
Only available when a project is selected.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    DirectoryTree,
    RichLog,
    Static,
    TextArea,
)

# Extensions we treat as editable text
TEXT_EXTENSIONS = {".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts", ".sh", ".bat", ".ps1"}


class ProjectWorkspaceScreen(Screen[None]):
    """IDE-style project workspace: file tree | editor | output."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("r", "refresh_tree", "Refresh tree"),
        Binding("o", "open_folder", "Open folder"),
        Binding("g", "run_project", "Run project"),
        Binding("s", "save_file", "Save", show=True),
    ]

    def __init__(self, project_name: str, project_root: Path) -> None:
        super().__init__()
        self._project_name = project_name
        self._project_root = project_root
        self._current_file_path: Path | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="workspace-container"):
            # Left: file tree
            with Vertical(id="workspace-sidebar", classes="workspace-panel"):
                yield Static(f"[bold]Project: {self._project_name}[/bold]", id="workspace-title")
                yield Static("↑↓ Enter: open file", id="workspace-tree-hint")
                self._tree = DirectoryTree(str(self._project_root), id="workspace-tree")
                yield self._tree

            # Right: editor + output
            with Vertical(id="workspace-main", classes="workspace-panel"):
                yield Static("Editor — select a file from the tree (S: save)", id="workspace-editor-hint")
                self._editor = TextArea(
                    id="workspace-editor",
                    language="python",
                    show_line_numbers=True,
                    read_only=False,
                )
                yield self._editor
                yield Static("Output", id="workspace-output-title")
                self._output = RichLog(id="workspace-output", highlight=True, markup=False)
                yield self._output

        with Horizontal(id="workspace-actions"):
            yield Button("Run project", id="btn-run", variant="primary")
            yield Button("Open folder", id="btn-open-folder")
            yield Button("Refresh tree", id="btn-refresh")
            yield Button("Close", id="btn-close")

    def on_mount(self) -> None:
        self._tree.focus()
        self._output.write(f"[Workspace] Project: {self._project_name}")
        self._output.write(f"[Workspace] Root: {self._project_root}")
        self._output.write("[Workspace] Select a file to edit. Press Run to compile and run.")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = event.path
        if not path or not path.is_file():
            return
        suffix = path.suffix.lower()
        if suffix not in TEXT_EXTENSIONS:
            self._output.write(f"[Workspace] Skipped (not text): {path.name}")
            return
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            self._editor.text = content
            self._current_file_path = path
            self._editor.focus()
            lang = "python" if suffix == ".py" else "json" if suffix == ".json" else "markdown" if suffix == ".md" else None
            if lang:
                self._editor.language = lang
            self._output.write(f"[Workspace] Opened: {path}")
        except Exception as e:
            self._output.write(f"[Workspace] Error opening {path}: {e}")

    def action_refresh_tree(self) -> None:
        try:
            self._tree.reload()
            self._output.write("[Workspace] Tree refreshed.")
        except Exception as e:
            self._output.write(f"[Workspace] Refresh error: {e}")

    def action_open_folder(self) -> None:
        try:
            import os
            import subprocess
            import sys
            p = str(self._project_root.resolve())
            if not os.path.isdir(p):
                self._output.write("[Workspace] Project folder does not exist.")
                return
            if os.name == "nt":
                os.startfile(os.path.normpath(p))
            elif sys.platform == "darwin":
                subprocess.run(["open", p], check=False, capture_output=True)
            else:
                subprocess.run(["xdg-open", p], check=False, capture_output=True)
            self._output.write("[Workspace] Opened folder.")
        except Exception as e:
            self._output.write(f"[Workspace] Open folder error: {e}")

    def action_run_project(self) -> None:
        app = getattr(self, "app", None)
        if not app or not hasattr(app, "run_project_for"):
            self._output.write("[Workspace] Run not available.")
            return
        self._output.write("[Workspace] Compiling and running...")
        app.run_project_for(self._project_name)
        self._output.write("[Workspace] Run started. Output in main stream (press 1).")
        self._output.write("[Workspace] You can keep editing here.")

    def action_save_file(self) -> None:
        if not self._current_file_path:
            self._output.write("[Workspace] No file open to save.")
            return
        try:
            content = self._editor.text
            self._current_file_path.write_text(content, encoding="utf-8")
            self._output.write(f"[Workspace] Saved: {self._current_file_path}")
        except Exception as e:
            self._output.write(f"[Workspace] Save error: {e}")

    def action_close(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-run":
            self.action_run_project()
        elif event.button.id == "btn-open-folder":
            self.action_open_folder()
        elif event.button.id == "btn-refresh":
            self.action_refresh_tree()
        elif event.button.id == "btn-close":
            self.action_close()
