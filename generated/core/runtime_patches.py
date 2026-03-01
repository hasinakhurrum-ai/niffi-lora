"""Runtime safety patches applied via on_cycle().

This module exists to keep the engine running even when core files are not
directly editable by self-improve tasks.

Rules:
- No side effects at import time (only definitions).
- on_cycle() must be idempotent.

Current patches:
1) Ensure config.STATE_DIR exists so SQLite can open config.DB_PATH.
2) Provide bot_runtime._generate(...) if missing (older bot_runtime versions).
"""

from __future__ import annotations

import os
from typing import Any


_APPLIED = False


def _ensure_state_dir() -> None:
    try:
        import config

        os.makedirs(getattr(config, "STATE_DIR", "state"), exist_ok=True)
        md = getattr(config, "MODELS_DIR", None)
        if md:
            os.makedirs(md, exist_ok=True)
    except Exception:
        pass


def _patch_bot_runtime_generate() -> None:
    """Define bot_runtime._generate if missing."""
    try:
        import bot_runtime  # type: ignore

        if hasattr(bot_runtime, "_generate") and callable(getattr(bot_runtime, "_generate")):
            return

        ollama_gen = getattr(bot_runtime, "ollama_generate", None)
        if ollama_gen is None:
            from ollama_client import generate as ollama_gen  # type: ignore

        def _generate(
            model_name: str,
            prompt: str,
            *,
            purpose: str = "TASK",
            stream: bool = False,
            temperature: float = 0.7,
            api_base: str | None = None,
            **kwargs: Any,
        ) -> str:
            # Keep signature flexible; ignore extra kwargs.
            return ollama_gen(model_name, prompt, stream=stream, temperature=temperature, api_base=api_base)

        setattr(bot_runtime, "_generate", _generate)
    except Exception:
        return


def on_cycle() -> None:
    global _APPLIED
    if _APPLIED:
        return
    _ensure_state_dir()
    _patch_bot_runtime_generate()
    _APPLIED = True
