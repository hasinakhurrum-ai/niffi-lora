from textual.screen import ModalScreen
from textual.widgets import Input, ListView, ListItem, Label
from textual.containers import Vertical
from textual.binding import Binding


class CommandPalette(ModalScreen[str | None]):
    """Unified command palette: all screens and actions in one list. Ctrl+P."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    COMMANDS = [
        "Stream (home)",
        "Tasks",
        "Runs",
        "Running agents",
        "Creations",
        "Config",
        "Explore project (paths, files, Run project)",
        "Project workspace (IDE: tree, editor, run)",
        "Switch project",
        "Create project",
        "Add prompt",
        "Run latest creation",
        "Run creation (choose)",
        "Help",
        "Show logs",
        "Toggle theme",
    ]

    def compose(self):
        items = [ListItem(Label(c)) for c in self.COMMANDS]
        self._labels = list(self.COMMANDS)
        yield Vertical(
            Input(
                placeholder="All commands — ↑↓ select  Enter: run  Esc: close",
                id="cmd-input",
            ),
            ListView(*items, id="cmd-list"),
        )

    def on_mount(self) -> None:
        self.query_one("#cmd-list", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        lv = self.query_one("#cmd-list", ListView)
        idx = lv.index
        if idx is None or idx < 0 or idx >= len(self._labels):
            self.dismiss(None)
            return
        self.dismiss(self._labels[idx])

