"""DB-driven scheduler: pick next runnable task(s), lock, return for execution."""

import db


def get_next_task() -> dict | None:
    """Next queued task: priority DESC, created_at ASC. Only idle/degraded bots."""
    return db.get_next_task()


def get_next_tasks(concurrency: int) -> list[dict]:
    """Up to concurrency tasks, one per bot, so core and project agents run in parallel."""
    return db.get_next_tasks(concurrency)


def mark_task_running(task_id: int) -> None:
    db.set_task_state(task_id, "running")


def mark_task_done(task_id: int) -> None:
    db.set_task_state(task_id, "done")


def mark_task_failed(task_id: int) -> None:
    db.set_task_state(task_id, "failed")
