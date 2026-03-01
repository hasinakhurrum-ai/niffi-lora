"""
Showcase: list and run what the engine has created.
  python -m niffi.showcase           -> list all creations
  python -m niffi.showcase run 3     -> run creation id 3
  python -m niffi.showcase run 3     -> run creation by id (or by path)
"""

import sys
import subprocess
from pathlib import Path

# Run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db
from creations import get_manifest


def list_creations() -> None:
    rows = db.list_creations()
    if not rows:
        print("No creations yet. Run the engine to generate code, servers, websites, etc.")
        return
    print(f"\n{'ID':<6} {'Type':<14} {'Title':<30} {'Path'}")
    print("-" * 80)
    for r in rows:
        cid = r.get("id")
        typ = (r.get("type") or "")[:12]
        title = (r.get("title") or "")[:28]
        path = (r.get("path") or "")[:50]
        print(f"{cid:<6} {typ:<14} {title:<30} {path}")


def run_creation(creation_id: str) -> None:
    try:
        cid = int(creation_id)
    except ValueError:
        print("Usage: python -m niffi.showcase run <id>")
        return
    c = db.get_creation(creation_id=cid)
    if not c:
        print(f"Creation {cid} not found.")
        return
    path = c.get("path")
    run_cmd = c.get("run_cmd") or "python main.py"
    if not path or not Path(path).exists():
        print(f"Creation {cid} path missing or not found: {path}")
        return
    work_dir = Path(path)
    cmd = run_cmd.strip().split()
    if cmd[0] == "python" and len(cmd) >= 2:
        script = work_dir / cmd[1]
        if not script.exists():
            script = work_dir / "main.py"
        if script.exists():
            print(f"Running: python {script} in {work_dir}")
            subprocess.run(["python", str(script)], cwd=work_dir)
            return
    print(f"Run command: {run_cmd} in {path}")
    subprocess.run(run_cmd, shell=True, cwd=path)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "run":
        if len(sys.argv) < 3:
            print("Usage: python -m niffi.showcase run <id>")
            return
        run_creation(sys.argv[2])
    else:
        list_creations()


if __name__ == "__main__":
    main()
