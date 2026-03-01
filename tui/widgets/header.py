"""Header bar: exactly one line. Reactive to state and project."""

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Static

from ..state import StreamState, Verbosity


class HeaderBar(Static):
    """Two-line-style kernel bar rendered as one line.

    MAIN:    [ Niffi DevOS ]   MAIN   |   CORE: ACTIVITY
    PROJECT: [ Niffi DevOS ]   PROJECT: name   |   MODE: ACTIVITY
    """

    state = reactive(StreamState.MAIN_STREAM)
    current_project = reactive("")
    projects_count = reactive(0)
    running_count = reactive(0)
    alerts_count = reactive(0)
    verbosity = reactive(Verbosity.SUMMARY)
    build_ok = reactive(True)
    tasks_count = reactive(0)
    autonomy = reactive("HIGH")  # Reused as activity / mode label

    def watch_state(self, value: StreamState) -> None:
        # Toggle CSS class for subtle MAIN vs PROJECT tint and refresh header text.
        self.remove_class("mode-main")
        self.remove_class("mode-project")
        if value == StreamState.MAIN_STREAM:
            self.add_class("mode-main")
        else:
            self.add_class("mode-project")
        self.update_header()

    def watch_current_project(self, value: str) -> None:
        self.update_header()

    def watch_projects_count(self, value: int) -> None:
        self.update_header()

    def watch_running_count(self, value: int) -> None:
        self.update_header()

    def watch_alerts_count(self, value: int) -> None:
        self.update_header()

    def watch_verbosity(self, value: Verbosity) -> None:
        self.update_header()

    def watch_build_ok(self, value: bool) -> None:
        self.update_header()

    def watch_tasks_count(self, value: int) -> None:
        self.update_header()

    def watch_autonomy(self, value: str) -> None:
        self.update_header()

    def update_header(self) -> None:
        """Render a structured kernel-style header line."""
        parts: list[str] = []
        if self.running_count is not None and self.running_count > 0:
            parts.append(f"Running: {self.running_count}")
        if self.tasks_count is not None:
            parts.append(f"Queued: {self.tasks_count}")
        extra = f"   |   {'   |   '.join(parts)}" if parts else ""
        if self.state == StreamState.MAIN_STREAM:
            text = f"[ Niffi DevOS ]   MAIN   |   CORE: {self.autonomy or 'IDLE'}{extra}"
        else:
            project = self.current_project or "—"
            mode = self.autonomy or "IDLE"
            text = f"[ Niffi DevOS ]   PROJECT: {project}   |   MODE: {mode}{extra}"
        self.update(text)
