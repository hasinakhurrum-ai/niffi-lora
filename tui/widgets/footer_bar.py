"""Footer: one line. Key hints in stream mode; prompt input in PROMPT_MODE; optional 'Editing: file' during CODE."""

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Static, Input
from textual.containers import Container

from ..state import StreamState


class FooterBar(Container):
    """Footer area: either hints Static or Input for prompt. Single line."""

    state = reactive(StreamState.MAIN_STREAM)
    current_project = reactive("")
    editing_file = reactive("")  # When CODE active
    prompt_placeholder = reactive("Instruction:  > ")

    def compose(self) -> ComposeResult:
        self.static_hints = Static(
            "1-8: Screens   Ctrl+P: All commands   I: Instruct   Tab: Switch   M: Main   H: Help",
            id="footer-hints",
        )
        self.prompt_input = Input(
            placeholder="Type your instruction and press Enter. Press Esc to cancel.",
            id="footer-prompt",
            classes="hidden",
        )
        yield self.static_hints
        yield self.prompt_input

    def watch_state(self, value: StreamState) -> None:
        if value == StreamState.PROMPT_MODE:
            ctx = self.current_project or "MAIN"
            self.query_one("#footer-prompt", Input).placeholder = f"Instruction for {ctx}:  > "
            self.query_one("#footer-hints", Static).add_class("hidden")
            self.query_one("#footer-prompt", Input).remove_class("hidden")
            self.query_one("#footer-prompt", Input).focus()
        else:
            self.query_one("#footer-prompt", Input).add_class("hidden")
            self.query_one("#footer-hints", Static).remove_class("hidden")
            self._update_hints()

    def watch_editing_file(self, value: str) -> None:
        self._update_hints()

    def _update_hints(self) -> None:
        """Reset footer text to the default hint line, optionally including an editing file."""
        h = self.query_one("#footer-hints", Static)
        if self.editing_file and self.state != StreamState.PROMPT_MODE:
            h.update(
                f"Editing: {self.editing_file}  |  1-6: Screens   Ctrl+P: Commands   I: Instruct   H: Help"
            )
        else:
            h.update("1-8: Screens   Ctrl+P: All commands   I: Instruct   Tab: Switch   M: Main   H: Help")

    def show_live_indicator(self, count: int) -> None:
        """Show or clear a 'LIVE' indicator when the timeline has unseen events."""
        h = self.query_one("#footer-hints", Static)
        if count <= 0:
            # No unseen events: revert to normal hints.
            self._update_hints()
        else:
            h.update(
                f"LIVE • {count} new events • Press G to jump   I: Instruct   Tab: Switch   M: Main"
            )
