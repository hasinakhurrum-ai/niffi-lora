"""
Niffi TUI entrypoint.
  python -m tui.run         -> start TUI and run engine in subprocess (default)
  python -m tui.run --no-engine  -> start TUI only (no main.py subprocess)
"""
import sys
from pathlib import Path

# Ensure niffi root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tui.app import NiffiTUI


def main() -> None:
    run_engine = "--no-engine" not in sys.argv
    app = NiffiTUI(run_engine=run_engine)
    app.run()


if __name__ == "__main__":
    main()
