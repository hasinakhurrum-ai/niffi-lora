"""Stream panel: per-project buffers; main buffer = all; project view = that project only. Clears on switch."""

from collections import deque
from datetime import datetime
from typing import Optional

from textual.app import ComposeResult
from textual.widgets import RichLog
from textual.reactive import reactive

from ..state import StreamState
from .. import events as ev


STREAM_MAX_LINES = 5000


def _make_deque() -> deque:
    return deque(maxlen=STREAM_MAX_LINES)


class StreamView(RichLog):
    """Main buffer = all lines. Project buffers = lines for that bot only. View switches clear and show the right buffer."""

    current_project = reactive("")
    stream_state = reactive(StreamState.MAIN_STREAM)
    _main_buffer: deque = _make_deque()
    _project_buffers: dict = {}  # project_name -> deque of formatted lines
    _trimmed = reactive(False)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._main_buffer = _make_deque()
        self._project_buffers = {}

    def _project_buffer(self, project: str) -> deque:
        if project not in self._project_buffers:
            self._project_buffers[project] = _make_deque()
        return self._project_buffers[project]

    def append_event(self, raw_line: str, project: Optional[str] = None) -> None:
        """Parse tag from line if needed; add to main buffer and to project buffer when project set; show if current view matches."""
        # Use message for classification (strip tag for type detection)
        _, rest = ev.parse_project_tag(raw_line)
        if project is None and rest != raw_line:
            project, _ = ev.parse_project_tag(raw_line)
        event_type, msg = ev.classify_line(rest)
        ts = datetime.now()
        line_main = ev.format_tail_line(ts, event_type, msg[:200] + ("..." if len(msg) > 200 else msg), project)
        line_project = ev.format_tail_line(ts, event_type, msg[:200] + ("..." if len(msg) > 200 else msg), None)

        self._main_buffer.append(line_main)
        if len(self._main_buffer) >= STREAM_MAX_LINES:
            self._trimmed = True
        if project:
            pb = self._project_buffer(project)
            pb.append(line_project)
            if len(pb) >= STREAM_MAX_LINES:
                self._trimmed = True

        # Only write to widget if this line belongs to the current view
        if self.stream_state == StreamState.MAIN_STREAM:
            self.write(line_main)
        elif self.stream_state == StreamState.PROJECT_STREAM and self.current_project == project:
            self.write(line_project)

    def _refresh_from_current_view(self) -> None:
        """Clear and repopulate from main buffer or current project buffer."""
        self.clear()
        if self.stream_state == StreamState.MAIN_STREAM:
            for line in self._main_buffer:
                self.write(line)
        else:
            buf = self._project_buffers.get(self.current_project, _make_deque())
            for line in buf:
                self.write(line)

    def watch_stream_state(self, old: StreamState, value: StreamState) -> None:
        self._refresh_from_current_view()

    def watch_current_project(self, old: str, value: str) -> None:
        self._refresh_from_current_view()

    def clear_current_stream(self) -> None:
        """Clear the visible stream: empty the buffer for the current view and refresh the display."""
        if self.stream_state == StreamState.MAIN_STREAM:
            self._main_buffer.clear()
        elif self.current_project:
            self._project_buffers[self.current_project] = _make_deque()
        self._refresh_from_current_view()

    def set_buffer_from_deque(self, buffer: deque) -> None:
        """Legacy: replace main buffer and refresh (used for trim). Prefer _refresh_from_current_view."""
        self._main_buffer = buffer
        self._refresh_from_current_view()

    def get_all_text(self) -> str:
        """Return all text from the current view buffer as a single string."""
        if self.stream_state == StreamState.MAIN_STREAM:
            buf = self._main_buffer
        else:
            buf = self._project_buffers.get(self.current_project, _make_deque())
        return "\n".join(buf)
