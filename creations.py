"""
Creations store: each project's creations live under projects/<bot_name>/creations/<id>/.
Manifest remains global for listing; paths are project-scoped. Run is from creation dir (thread-safe).
"""

import json
import shutil
from pathlib import Path

import config
import db

try:
    from sandbox import get_project_root
except ImportError:
    def get_project_root(bot_name: str) -> Path:
        return Path(getattr(config, "PROJECTS_DIR", "projects")) / (bot_name or "default").replace(" ", "_")


def _ensure_creations_dir() -> Path:
    """Global creations dir (legacy manifest); new creations go under projects/<bot>/creations/."""
    d = Path(config.CREATIONS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    return _ensure_creations_dir() / "manifest.json"


def _load_manifest() -> list[dict]:
    p = _manifest_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_manifest(entries: list[dict]) -> None:
    _manifest_path().write_text(json.dumps(entries, indent=2), encoding="utf-8")


def growth_log(message: str) -> None:
    """Append to growth.log so you can see the engine growing."""
    Path(config.STATE_DIR).mkdir(parents=True, exist_ok=True)
    log_path = Path(config.GROWTH_LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(message.strip() + "\n")


def register_creation(
    run_id: int,
    bot_id: int,
    task_type: str,
    workspace_path: str,
    code_path: str,
    title: str = "",
    extra_paths: list[str] | None = None,
) -> int | None:
    """
    Copy run artifacts to projects/<bot_name>/creations/<id>/ and add to DB + manifest.
    Each project has its own directory; runs are thread-safe (own cwd).
    Returns creation id or None.
    """
    workspace = Path(workspace_path) if workspace_path else None
    if not workspace or not workspace.exists():
        return None
    bot = db.get_bot(bot_id=bot_id)
    bot_name = (bot.get("name") or f"bot_{bot_id}") if bot else f"bot_{bot_id}"
    project_root = get_project_root(bot_name)
    creations_sub = project_root / "creations"
    creations_sub.mkdir(parents=True, exist_ok=True)
    _ensure_creations_dir()
    cid = db.insert_creation(
        run_id=run_id,
        bot_id=bot_id,
        type_=task_type,
        path="",
        title=title or f"{task_type}_{run_id}",
        run_cmd=f"python {code_path}" if code_path else "",
        meta_json={"workspace": str(workspace)},
    )
    dest_dir = creations_sub / str(cid)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        if (workspace / "candidate.py").exists():
            shutil.copy2(workspace / "candidate.py", dest_dir / "main.py")
        for name in ("index.html", "logs.txt", "output.json", "input.json"):
            if (workspace / name).exists():
                shutil.copy2(workspace / name, dest_dir / name)
        for ext in ("*.html", "*.css", "*.js", "*.png", "*.jpg", "*.gif", "*.webp", "*.mp4", "*.json"):
            for p in workspace.glob(ext):
                if p.name != "input.json":
                    shutil.copy2(p, dest_dir / p.name)
        if extra_paths:
            for p in extra_paths:
                src = Path(p)
                if src.exists():
                    shutil.copy2(src, dest_dir / src.name)
    except Exception:
        pass
    run_cmd = "python main.py" if (dest_dir / "main.py").exists() else ""
    db.update_creation_path(cid, str(dest_dir), run_cmd)
    manifest = _load_manifest()
    manifest.insert(0, {
        "id": cid,
        "type": task_type,
        "title": title or f"{task_type}_{run_id}",
        "path": str(dest_dir),
        "run_cmd": run_cmd,
        "run_id": run_id,
        "bot_id": bot_id,
    })
    _save_manifest(manifest[:500])
    growth_log(f"[CREATION] id={cid} type={task_type} path={dest_dir} title={title or cid}")
    return cid


def get_manifest() -> list[dict]:
    return _load_manifest()
