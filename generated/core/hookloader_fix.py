"""Patch the engine hook loader to be compatible with dataclasses/typing.

Problem:
The engine loads generated modules via importlib.util.module_from_spec(spec)
and spec.loader.exec_module(mod) but does NOT register the module in sys.modules.

On Python 3.13+ (and especially 3.14 dev builds), dataclasses/typing frequently
assume the module exists in sys.modules when resolving annotations.
If it isn't, you can get errors like:
  'NoneType' object has no attribute '__dict__'

Fix:
Monkeypatch main._load_all_generated_hooks so it inserts the module into
sys.modules[mod_name] before exec_module.

This patch is applied once via on_cycle().
"""

from __future__ import annotations

import sys
from pathlib import Path


_APPLIED = False


def on_cycle() -> None:
    global _APPLIED
    if _APPLIED:
        return

    try:
        import main  # running engine module
        import config
        import importlib.util

        def _patched_load_all_generated_hooks() -> None:
            root = Path(config.GENERATED_MODULES_DIR)
            if not root.exists():
                return
            root = root.resolve()
            project_root = root.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            for py in sorted(root.rglob("*.py")):
                if py.name == "__init__.py":
                    continue
                try:
                    rel = py.relative_to(root)
                    mod_name = "gen_" + str(rel).replace("\\", "/").replace("/", "_").replace(".py", "")
                    spec = importlib.util.spec_from_file_location(mod_name, str(py))
                    if not spec or not spec.loader:
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    # CRITICAL: register before exec so dataclasses/typing can resolve module globals.
                    sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "on_cycle") and callable(mod.on_cycle):
                        mod.on_cycle()
                except Exception as e:
                    try:
                        main._log(f"[EVOLVE] Hook load/on_cycle failed {py}: {e}")
                    except Exception:
                        pass

        # Patch in place
        setattr(main, "_load_all_generated_hooks", _patched_load_all_generated_hooks)

        try:
            main._log("[EVOLVE] Applied hook loader patch (sys.modules registration)")
        except Exception:
            pass

        _APPLIED = True
    except Exception:
        return
