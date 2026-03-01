"""Goals and level: so the engine can grow toward an explicit target."""

import json
from pathlib import Path

import config


def _level_path() -> Path:
    return Path(config.LEVEL_PATH)


def get_level() -> dict:
    p = _level_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        data = {"level": 1, "goal": "Create and run code, servers, websites.", "creations_count": 0, "next_goal": "Add simulations and graphics."}
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"level": 1, "goal": "", "creations_count": 0, "next_goal": ""}


def set_level(level: int = None, goal: str = None, creations_count: int = None, next_goal: str = None) -> None:
    data = get_level()
    if level is not None:
        data["level"] = level
    if goal is not None:
        data["goal"] = goal
    if creations_count is not None:
        data["creations_count"] = creations_count
    if next_goal is not None:
        data["next_goal"] = next_goal
    _level_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def increment_creations() -> int:
    data = get_level()
    data["creations_count"] = data.get("creations_count", 0) + 1
    set_level(creations_count=data["creations_count"])
    return data["creations_count"]
