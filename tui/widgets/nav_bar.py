"""Navigation bar: all screens and Ctrl+P in one line."""

from textual.widgets import Static


class NavBar(Static):
    """One-line nav: 1 Stream, 2 Tasks, 3 Runs, 4 Agents, 5 Creations, 6 Config, Ctrl+P Commands."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._update_text()

    def _update_text(self) -> None:
        self.update(
            "[bold]1[/] Stream   [bold]2[/] Tasks   [bold]3[/] Runs   "
            "[bold]4[/] Agents   [bold]5[/] Creations   [bold]6[/] Config   [bold]7[/] Explore   [bold]8[/] IDE   "
            "[bold]Ctrl+P[/] All commands"
        )
