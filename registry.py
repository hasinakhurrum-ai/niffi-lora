"""
Engine registry: generated modules become part of the main system by registering here.

- scheduler: callable(concurrency, tick=None) -> list of task dicts (id, bot_id, prompt, task_type, ...).
  If set and returns a non-empty list, the main loop uses it for task selection; else uses DB/ECS.
- scorer_override: callable(result, task_type) -> (score, valid) or None.
  If set and returns (score, valid), bot_runtime uses it instead of scorer.compute_score; else uses default.
- after_run: callable(bot_id, task_id, result, task_type, score, valid, **kwargs) -> None.
  If set, called after each task run (after reflection) for side effects (logging, metrics, etc.).

Generated code can assign to these in generated/plugins/registry_plugin.py at import time so the engine uses it.
"""

from __future__ import annotations

from typing import Any


class Registry:
    """Single registry instance; assign callables to integrate generated code into the main loop."""

    scheduler: Any = None  # (concurrency, tick=None) -> list[dict]
    scorer_override: Any = None  # (result, task_type) -> (score, valid) | None
    after_run: Any = None  # (bot_id, task_id, result, task_type, score, valid, ...) -> None


registry = Registry()
