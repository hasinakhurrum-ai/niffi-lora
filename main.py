"""
Orchestrator: DB-driven multi-bot loop.
Create schema, ensure Ollama, register default bots, seed tasks, then loop.
  python main.py         -> foreground
  python main.py --daemon -> fork to background (Unix) or spawn detached (Windows),
                            log to state/daemon.log, PID in state/daemon.pid. Returns immediately.
"""

import os
import re
import py_compile
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json

import config
import db
import engine_log
import prompts
from bot_runtime import run_task

# Phase 1: config profiles (apply before using concurrency etc.)
try:
    from profiles import apply_profile
    apply_profile()
except Exception:
    pass

# Next-gen core subsystems (best-effort; engine continues if absent)
try:
    from generated.core import tick_engine, resource_manager, self_evaluator, event_sourcing
except Exception:  # pragma: no cover
    tick_engine = None
    resource_manager = None
    self_evaluator = None
    event_sourcing = None

from scheduler import get_next_task, mark_task_failed, mark_task_running

try:
    from model_registry import ensure_model
except ImportError:
    from ollama_client import ensure_model

MODEL_IN_PROPOSAL_RE = re.compile(r"MODEL:\s*([a-zA-Z0-9_.\-:]+)", re.IGNORECASE)
CREATE_MODEL_RE = re.compile(
    r"CREATE_MODEL:\s*([a-zA-Z0-9_.\-]+)\s+base[=:]\s*([a-zA-Z0-9_.\-:]+)(?:\s+SYSTEM[=:]\s*(.+?))?(?=\n\s*(?:CREATE_MODEL|REGISTER_EXTERNAL|MODEL:|PROPOSAL:)|\Z)",
    re.IGNORECASE | re.DOTALL,
)
REGISTER_EXTERNAL_RE = re.compile(
    r"REGISTER_EXTERNAL(?:_MODEL)?:\s*([a-zA-Z0-9_.\-]+)\s+endpoint[=:]\s*(\S+)",
    re.IGNORECASE,
)


def _apply_one_proposal() -> bool:
    """Apply one unapplied proposal (CREATE_MODEL, REGISTER_EXTERNAL, MODEL:, system_prompt). Returns True if applied."""
    if not getattr(config, "APPLY_PROPOSALS", False):
        return False
    prop = db.get_one_unapplied_proposal()
    if not prop:
        return False
    bot = db.get_bot(bot_id=prop["bot_id"])
    if not bot or not prop.get("content"):
        return False
    content = prop["content"][:8000]
    for create_m in CREATE_MODEL_RE.finditer(content):
        name, base, system = create_m.group(1).strip(), create_m.group(2).strip(), (create_m.group(3) or "").strip()
        try:
            from tools import create_ollama_model
            code, out, err = create_ollama_model(name, base, system)
            if code == 0:
                from model_registry import insert_model
                insert_model(name, "ollama", None, base)
                _log(f"[BACKGROUND] Created model '{name}' from base {base}; registered.")
            else:
                _log(f"[BACKGROUND] CREATE_MODEL {name} failed: {err[:200]}")
        except Exception as e:
            _log(f"[BACKGROUND] CREATE_MODEL error: {e}")
    for reg_m in REGISTER_EXTERNAL_RE.finditer(content):
        name, endpoint = reg_m.group(1).strip(), reg_m.group(2).strip()
        try:
            from model_registry import insert_model
            insert_model(name, "external", endpoint.strip(), None)
            _log(f"[BACKGROUND] Registered external model '{name}' -> {endpoint}")
        except Exception as e:
            _log(f"[BACKGROUND] REGISTER_EXTERNAL error: {e}")
    m = MODEL_IN_PROPOSAL_RE.search(content)
    if m:
        model_name = m.group(1).strip()
        db.update_bot_model(prop["bot_id"], model_name)
        ensure_model(model_name)
        _log(f"[BACKGROUND] Applied MODEL from proposal: bot {prop['bot_id']} -> {model_name}")
        content = MODEL_IN_PROPOSAL_RE.sub("", content).strip()
    if content:
        db.update_bot_system_prompt(prop["bot_id"], content)
    db.mark_proposal_applied(prop["id"], prop.get("bot_id"))
    _log(f"[BACKGROUND] Applied proposal {prop['id']} -> bot {prop['bot_id']}")
    if (bot.get("domain") or "").strip() == "system":
        _log("[KERNEL] Applied proposal -> core_agent.")
    return True


def _enqueue_core_self_improve(reason: str) -> None:
    """
    Insert a self_improve task for the core_agent describing a runtime engine error.

    This lets the CORE_AGENT analyze failures and propose/implement fixes under generated/core|plugins|utils.
    """
    try:
        core = db.get_bot(name="core_agent")
        if not core:
            return
        core_id = core["id"]
        snippet = (reason or "").strip()[-4000:]
        prompt = prompts.get_prompt("error_self_improve", "core", snippet=snippet)
        priority = getattr(config, "CORE_TASK_PRIORITY", 3)
        db.insert_task(core_id, prompt, priority=priority, task_type="self_improve")
    except Exception:
        # Never let meta-governance enqueue failures crash the engine.
        pass


def _handle_fatal_exception(exc: Exception) -> None:
    """Log a fatal exception and enqueue a core self_improve task with the traceback."""
    try:
        tb_str = "".join(traceback.format_exception(exc.__class__, exc, exc.__traceback__))
    except Exception:
        tb_str = repr(exc)
    engine_log.log_error(f"[ENGINE] Fatal error in main loop: {exc}\n{tb_str}", exc_info=True)
    _enqueue_core_self_improve(tb_str)


def _log(msg: str) -> None:
    """Log to engine.log + console (stdout)."""
    engine_log.log_info(msg)


def _webhook_out(task_id: int, bot_id: int, bot_name: str, task_type: str, status: str, error: str | None) -> None:
    """Phase 6: POST to WEBHOOK_OUT_URL (async)."""
    url = getattr(config, "WEBHOOK_OUT_URL", "") or ""
    if not url or not url.strip():
        return
    payload = {"task_id": task_id, "bot_id": bot_id, "bot_name": bot_name, "task_type": task_type, "status": status}
    if error:
        payload["error"] = error
    def _post():
        try:
            import urllib.request
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
    t = __import__("threading").Thread(target=_post, daemon=True)
    t.start()

def _get_core_system_prompt() -> str:
    """Build CORE_AGENT system prompt from data-driven blocks."""
    meta = prompts.get_prompt("meta_system", "core")
    return prompts.get_prompt("core_system", "core", meta_system=meta)


def ollama_reachable() -> bool:
    try:
        import requests
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def register_default_bots() -> None:
    """Ensure there is one CORE_AGENT and a couple of example PROJECT_AGENT bots."""
    core = db.get_bot(name="core_agent")
    if not core:
        core_id = db.insert_bot("core_agent", "system", config.DEFAULT_MODEL, _get_core_system_prompt())
        db.insert_task(
            core_id,
            prompts.get_prompt("seed_self_improve", "core"),
            priority=1,
            task_type="self_improve",
        )
        db.insert_task(
            core_id,
            prompts.get_prompt("seed_upgrade_engine", "core"),
            priority=1,
            task_type="upgrade_engine",
        )

    # Project bots are created via TUI (New project) or API; no default crypto/finance bots.


def _ensure_default_model_policies() -> None:
    """Seed core/project model routing policies if none exist yet.

    Uses a CPU-friendly setup:
    - core: qwen2.5:14b for planning/governance
    - project: deepseek-coder:6.7b for code-heavy tasks, llama3:8b for some lighter tasks
    """
    try:
        core_policy = db.get_model_policy("core")
        project_policy = db.get_model_policy("project")
    except Exception:
        return

    if not core_policy:
        core_policy = {
            "default_model": "qwen2.5:14b",
            "per_task_type": {
                "self_improve": "qwen2.5:14b",
                "upgrade_engine": "qwen2.5:14b",
                "proposal_review": "qwen2.5:14b",
                "design_project": "qwen2.5:14b",
                "optimize_runtime": "qwen2.5:14b",
                "security_patch": "qwen2.5:14b",
                "performance_tune": "qwen2.5:14b",
                "simulation": "qwen2.5:14b",
                "proposal": "qwen2.5:14b",
                "reflection": "qwen2.5:14b",
            },
            "per_purpose": {
                "planning": "qwen2.5:14b",
                "code": "qwen2.5:14b",
                "repair": "qwen2.5:14b",
                "reflect": "qwen2.5:14b",
                "proposal": "qwen2.5:14b",
            },
        }
        try:
            db.upsert_model_policy("core", json.dumps(core_policy))
            _log("[BACKGROUND] Seeded default core model policy (qwen2.5:14b).")
        except Exception:
            pass

    if not project_policy:
        project_policy = {
            "default_model": config.DEFAULT_MODEL,
            "per_task_type": {
                "code": config.DEFAULT_MODEL,
                "server": config.DEFAULT_MODEL,
                "website": config.DEFAULT_MODEL,
                "tool": config.DEFAULT_MODEL,
                "simulation": config.DEFAULT_MODEL,
                "graphics": config.DEFAULT_MODEL,
                "video": config.DEFAULT_MODEL,
                "test": config.DEFAULT_MODEL,
                "deploy": config.DEFAULT_MODEL,
            },
            "per_purpose": {
                "planning": config.DEFAULT_MODEL,
                "code": config.DEFAULT_MODEL,
                "repair": config.DEFAULT_MODEL,
                "reflect": config.DEFAULT_MODEL,
                "proposal": config.DEFAULT_MODEL,
            },
        }
        try:
            db.upsert_model_policy("project", json.dumps(project_policy))
            _log("[BACKGROUND] Seeded default project model policy.")
        except Exception:
            pass


def _ensure_policy_models_pulled() -> None:
    """
    Ensure that all models referenced in current model policies are available via Ollama.

    Uses ollama_client/model_registry.ensure_model under the hood, which calls the Ollama HTTP API
    (equivalent to `ollama pull <model>`). Controlled by config.ALLOW_MODEL_PULL.
    """
    if not getattr(config, "ALLOW_MODEL_PULL", True):
        return
    try:
        core_policy = db.get_model_policy("core") or {}
        project_policy = db.get_model_policy("project") or {}
    except Exception:
        return

    names = set()
    for pol in (core_policy, project_policy):
        if not pol:
            continue
        dm = (pol.get("default_model") or "").strip()
        if dm:
            names.add(dm)
        for per in (pol.get("per_task_type") or {}, pol.get("per_purpose") or {}):
            for v in per.values():
                m = (v or "").strip()
                if m:
                    names.add(m)

    for name in sorted(names):
        try:
            ensure_model(name)
            _log(f"[BACKGROUND] ensure_model pulled/verified '{name}' via Ollama API.")
        except Exception as e:
            _log(f"[BACKGROUND] ensure_model failed for '{name}': {e}")


def _ensure_default_prompt_blocks() -> None:
    """Seed prompt blocks so CORE_AGENT and runtime prompts are data-driven."""
    def _seed(name: str, scope: str, content: str) -> None:
        try:
            blk = db.get_prompt_block(name, scope)
            if blk:
                return
            db.upsert_prompt_block(name, scope, content.strip(), version=1, enabled=True)
            _log(f"[BACKGROUND] Seeded {scope}/{name} prompt block.")
        except Exception:
            pass

    for (scope, name), content in prompts.BUILTIN_PROMPTS.items():
        if db.get_prompt_block(name, scope) is None:
            _seed(name, scope, content)


def _register_remote_model_endpoints() -> None:
    """Register remote model endpoints from config.REMOTE_MODEL_ENDPOINTS so bots can use them (MODEL: name)."""
    endpoints = getattr(config, "REMOTE_MODEL_ENDPOINTS", None) or []
    if not endpoints:
        return
    try:
        from model_registry import insert_model
        for i, item in enumerate(endpoints):
            if isinstance(item, dict):
                name = (item.get("name") or "").strip()
                url = (item.get("url") or item.get("endpoint") or "").strip().rstrip("/")
                if name and url:
                    insert_model(name, "external", url, None)
                    _log(f"[STARTUP] Registered remote model '{name}' -> {url}")
            elif isinstance(item, str):
                name = f"remote_{i}"
                url = item.strip().rstrip("/")
                if url:
                    insert_model(name, "external", url, None)
                    _log(f"[STARTUP] Registered remote model '{name}' -> {url}")
    except Exception as e:
        _log(f"[STARTUP] Remote model registration failed: {e}")


def seed_when_empty() -> None:
    """If queue is empty but bots exist, add a couple of tasks so the engine doesn't sit waiting forever. No seeding in DRAIN_MODE."""
    if getattr(config, "DRAIN_MODE", False):
        return
    if db.count_queued_tasks() > 0:
        return
    bots = db.list_bots()
    if not bots:
        return
    project_bots = [b for b in bots if (b.get("domain") or "") != "system"]
    core_priority = getattr(config, "CORE_TASK_PRIORITY", 3)
    for bot in project_bots:
        db.insert_task(bot["id"], prompts.get_prompt("project_code", "seed"), priority=0, task_type="code")
    core_bots = [b for b in bots if (b.get("domain") or "") == "system"]
    for core_bot in core_bots[:1]:
        db.insert_task(
            core_bot["id"],
            prompts.get_prompt("core_review", "seed"),
            priority=core_priority,
            task_type="self_improve",
        )
        break
    _log("[BACKGROUND] Queue was empty; seeded tasks so the engine can continue.")


def _compile_generated() -> None:
    """Compile all generated/**/*.py so the system verifies its own code each cycle; log any syntax errors."""
    import ast
    root = Path(config.GENERATED_MODULES_DIR)
    if not root.exists():
        return
    ok, fail = 0, 0
    for py in root.rglob("*.py"):
        try:
            py_compile.compile(str(py), doraise=True)
            ok += 1
        except PermissionError as e:
            # __pycache__ write can fail on Windows (e.g. [WinError 5] Access denied). Validate syntax only.
            try:
                py_str = py.read_text(encoding="utf-8", errors="replace")
                ast.parse(py_str)
                ok += 1
            except SyntaxError as se:
                fail += 1
                _log(f"[EVOLVE] Compile failed {py}: {se}")
            except Exception:
                fail += 1
                _log(f"[EVOLVE] Compile failed {py}: {e}")
        except py_compile.PyCompileError as e:
            fail += 1
            _log(f"[EVOLVE] Compile failed {py}: {e}")
    if ok or fail:
        try:
            core = len(list((root / "core").rglob("*.py"))) if (root / "core").exists() else 0
            plugins = len(list((root / "plugins").rglob("*.py"))) if (root / "plugins").exists() else 0
            utils = len(list((root / "utils").rglob("*.py"))) if (root / "utils").exists() else 0
            other = ok + fail - core - plugins - utils
            parts = []
            if core: parts.append(f"core={core}")
            if plugins: parts.append(f"plugins={plugins}")
            if utils: parts.append(f"utils={utils}")
            if other: parts.append(f"other={other}")
            if parts:
                _log(f"[BUILD] generated: {ok + fail} modules ({', '.join(parts)})")
        except Exception:
            pass


def _load_generated_plugin() -> None:
    """Load generated/plugin.py or generated/plugins/plugin.py and call on_cycle() if present (engine evolves by running its own code)."""
    import importlib.util
    for candidate in (os.path.join(config.GENERATED_MODULES_DIR, "plugin.py"), os.path.join(config.GENERATED_MODULES_DIR, "plugins", "plugin.py")):
        try:
            if not os.path.isfile(candidate):
                continue
            spec = importlib.util.spec_from_file_location("generated_plugin", candidate)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "on_cycle"):
                    mod.on_cycle()
                break
        except Exception:
            pass


def _load_all_generated_hooks() -> None:
    """
    Discover, import, and run on_cycle() for every generated module.
    So whatever the LLM generates becomes part of the running system (self-scale, self-improve while running).
    One failing module does not stop the loop; errors are logged.
    """
    import importlib.util
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
            spec.loader.exec_module(mod)
            if hasattr(mod, "on_cycle") and callable(mod.on_cycle):
                mod.on_cycle()
        except Exception as e:
            _log(f"[EVOLVE] Hook load/on_cycle failed {py}: {e}")


def _load_registry_plugins() -> None:
    """Load generated/plugins/registry_plugin so it can assign registry.scheduler, scorer_override, after_run."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("generated.plugins.registry_plugin")
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
    except Exception as e:
        _log(f"[EVOLVE] Registry plugin load failed: {e}")


def _update_architecture_and_capabilities_snapshot() -> None:
    """
    Refresh architecture_state and capabilities_registry with a lightweight snapshot.

    This runs every main loop iteration so the CORE_AGENT prompt can use up-to-date
    information without relying on hardcoded strings.
    """
    import json as _json

    try:
        # Scheduler / runtime
        queued = db.count_queued_tasks()
        bots = db.list_bots()
        core_bots = [b for b in bots if (b.get("domain") or "") == "system"]
        project_bots = [b for b in bots if (b.get("domain") or "") != "system"]
        running_projects = [b for b in project_bots if (b.get("status") or "") == "running"]
        db.upsert_architecture_state(
            "scheduler",
            summary=f"Core-first scheduler with {len(project_bots)} project bots and {len(core_bots)} core bot(s).",
            interfaces="tasks (state=queued/running/done), bots (status=idle/running/degraded); uses db.get_next_task().",
            known_issues="",
            metrics_json={
                "queued_tasks": queued,
                "project_bots": len(project_bots),
                "running_projects": len(running_projects),
                "bots_concurrency": getattr(config, 'BOTS_CONCURRENCY', 1),
            },
        )

        # Sandbox
        db.upsert_architecture_state(
            "sandbox",
            summary="Per-bot virtualenv and per-run workspace; projects under projects/<bot>/runs/, core modules under generated/.",
            interfaces="sandbox.ensure_bot_venv(bot_name), sandbox.get_run_workspace(bot_name, run_id), sandbox.run_candidate(...).",
            known_issues="",
            metrics_json={},
        )

        # Model router / registry
        models = db.list_models()
        db.upsert_architecture_state(
            "model_router",
            summary="Model registry-backed router; resolves model name to (api_base, model).",
            interfaces="model_registry.get_model_endpoint(name), model_registry.ensure_model(name).",
            known_issues="",
            metrics_json={"models_registered": len(models)},
        )

        # Capabilities
        db.upsert_capability(
            "run_shell",
            limits_json={"timeout_s": getattr(config, "SHELL_TIMEOUT_S", 120)},
            status="ok",
        )
        db.upsert_capability(
            "pip_install",
            limits_json={"timeout_s": getattr(config, "PIP_INSTALL_TIMEOUT_S", 120)},
            status="ok",
        )
        db.upsert_capability(
            "create_ollama_model",
            limits_json={"timeout_s": getattr(config, "OLLAMA_CREATE_TIMEOUT_S", 600)},
            status="ok",
        )
        db.upsert_capability(
            "spawn_project_agents",
            limits_json={"max_projects_hint": len(project_bots)},
            status="ok",
        )
        db.upsert_capability(
            "modify_engine_core",
            limits_json={"via_task_types": ["self_improve", "upgrade_engine", "optimize_runtime", "security_patch", "performance_tune", "proposal_review", "design_project"]},
            status="ok",
        )
        # Docker: local + remote, permission fallback, full resource control (cpus, memory, stats).
        try:
            from tools import docker_available
            db.upsert_capability(
                "docker",
                limits_json={
                    "install_enabled": getattr(config, "DOCKER_INSTALL_ENABLED", True),
                    "remote": "host= on all docker_* or config DOCKER_HOST (tcp:// or ssh://)",
                    "permission": "sudo -n fallback on Linux when permission denied",
                    "resource_limits": "docker_run(cpus=, memory_mb=, memory_swap_mb=, pids_limit=, shm_size_mb=); docker_stats()",
                    "timeout_s": getattr(config, "DOCKER_TIMEOUT_S", 300),
                    "tools": "docker_available(host), ensure_docker(host), docker_images/ps/inspect_*/pull/run/stop/rm/logs/stats(host=...)",
                },
                status="ok" if docker_available() else "unavailable",
            )
        except Exception:
            pass
        # Git: full repo access (branch, add, commit, merge, optional push).
        try:
            from tools import git_repo_root
            root = git_repo_root()
            db.upsert_capability(
                "git",
                limits_json={
                    "enabled": getattr(config, "GIT_ENGINE_ENABLED", False),
                    "repo_root": root or "(none)",
                    "allow_push": getattr(config, "GIT_ALLOW_PUSH", False),
                    "protected_branches": list(getattr(config, "GIT_PROTECTED_BRANCHES", ())),
                    "audit": getattr(config, "GIT_AUDIT", True),
                    "tools": "git_repo_root, git_status, git_branch_list, git_branch_create, git_checkout, git_add, git_commit, git_merge, git_push",
                },
                status="ok" if (root and getattr(config, "GIT_ENGINE_ENABLED", False)) else "unavailable",
            )
        except Exception:
            pass
    except Exception:
        # Snapshot failures should never crash the main loop.
        pass


def main() -> None:
    # Force unbuffered stdout so ASK/RESPONSE/CODE stream is visible immediately
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    if not ollama_reachable():
        print("Ollama not reachable at", config.OLLAMA_URL, flush=True)
        return
    db.init_schema()
    # Startup resilience: discover models first; if default unavailable, use any available
    try:
        from generated.core import model_discovery
        model_discovery.fetch_ollama_models()
    except Exception:
        pass
    try:
        ensure_model(config.DEFAULT_MODEL)
    except Exception as e:
        _log(f"[STARTUP] Default model {config.DEFAULT_MODEL} unavailable: {e}")
        try:
            from generated.core import model_discovery
            fallback = model_discovery.pick_first_available([config.DEFAULT_MODEL] + list(config.MODEL_FALLBACKS.get("project", [])))
            if fallback:
                _log(f"[STARTUP] Using available model: {fallback}")
            # Don't re-ensure; we'll use discovered models via model_selector
        except Exception:
            _log("[STARTUP] No fallback model found. Tasks will use model_selector with discovery.")
    register_default_bots()
    _ensure_default_model_policies()
    _ensure_policy_models_pulled()
    _ensure_default_prompt_blocks()

    # Docker: sense and optionally install (OS-agnostic); engine can then control containers for advancement.
    try:
        from tools import ensure_docker
        ok, msg = ensure_docker()
        _log(f"[ENGINE] Docker: {msg}")
    except Exception as e:
        _log(f"[ENGINE] Docker check failed: {e}")

    # Remote model endpoints: auto-register from config so bots can use remote models (MODEL: name).
    _register_remote_model_endpoints()

    # Recovery: reset bots/tasks stuck in 'running' after a crash
    bots_reset, tasks_reset = db.reset_stuck_running()
    if bots_reset or tasks_reset:
        _log(f"[STARTUP] Recovered: {bots_reset} bot(s) and {tasks_reset} task(s) from stuck 'running' -> idle/queued.")

    try:
        from system_info import get_system_info_dict
        info = get_system_info_dict()
        _log(f"[ENGINE] OS: {info.get('os', '?')} {info.get('os_release', '')} | Python: {info.get('python_version', '?')} | CPU: {info.get('cpu_count', '?')} cores | Memory: {info.get('memory_total_mb', '?')} MB")
    except Exception:
        _log(f"[ENGINE] OS: {getattr(config, 'OS_NAME', '?')} | (system_info not available)")
    _log("Engine running. Generated code uses organized structure (generated/core, generated/plugins, generated/utils); engine compiles and loads it each cycle.")
    _log("  ASK = what is sent to the LLM | RESPONSE = full output | CODE = extracted code. To list creations: python showcase.py. To run one: python showcase.py run <id>")
    _log("If you used --daemon, watch state/daemon.log. Use python -u main.py for unbuffered output.\n")

    # Populate architecture/capabilities so CORE_AGENT has context from first cycle
    _update_architecture_and_capabilities_snapshot()

    # Phase 1: health endpoint (background thread)
    try:
        from health_server import start_health_server
        start_health_server()
        _log(f"[ENGINE] Health endpoint: http://127.0.0.1:{getattr(config, 'HEALTH_PORT', 8765)}/health")
    except Exception as e:
        _log(f"[ENGINE] Health server failed: {e}")

    run_count = 0
    wait_cycles = 0
    cycle_index = 0  # for snapshot throttling
    base_concurrency = max(1, int(getattr(config, "BOTS_CONCURRENCY", 1)))
    concurrency = base_concurrency

    def _run_one_task(task: dict) -> None:
        task_id = task["id"]
        bot_id = task["bot_id"]
        prompt = task["prompt"]
        task_type = task.get("task_type") or "code"
        bot = db.get_bot(bot_id=bot_id)
        bot_name = bot.get("name", f"bot_{bot_id}") if bot else f"bot_{bot_id}"
        short = prompt[:80] + ("..." if len(prompt) > 80 else "")
        _log(f"[{bot_name}] >>> TASK START {task_id} ({task_type}) | {short}")
        try:
            import audit_log
            audit_log.emit("task_start", {"task_id": task_id, "bot_id": bot_id, "bot_name": bot_name, "task_type": task_type})
        except Exception:
            pass
        if event_sourcing and 'ctx' in locals() and ctx:
            try:
                event_sourcing.append_event(tick=ctx.tick, scope='core', type_='TASK_START', payload={'task_id': task_id, 'bot_id': bot_id, 'bot': bot_name, 'task_type': task_type})
            except Exception:
                pass
        try:
            run_task(task_id, bot_id, prompt, task_type=task_type)
            _log(f"[{bot_name}] [BACKGROUND] Task {task_id} finished.")
            try:
                import audit_log
                audit_log.emit("task_end", {"task_id": task_id, "bot_id": bot_id, "bot_name": bot_name, "status": "ok"})
            except Exception:
                pass
            _webhook_out(task_id, bot_id, bot_name, task_type, "ok", None)
            if event_sourcing and 'ctx' in locals() and ctx:
                try:
                    event_sourcing.append_event(tick=ctx.tick, scope='core', type_='TASK_END', payload={'task_id': task_id, 'bot_id': bot_id, 'bot': bot_name, 'status': 'ok'})
                except Exception:
                    pass
        except Exception as e:
            mark_task_failed(task_id)
            db.set_bot_status(bot_id, "idle")
            try:
                import audit_log
                audit_log.emit("task_end", {"task_id": task_id, "bot_id": bot_id, "bot_name": bot_name, "status": "error", "error": str(e)[:500]})
            except Exception:
                pass
            _webhook_out(task_id, bot_id, bot_name, task_type, "error", str(e)[:500])
            err_msg = f"[{bot_name}] [BACKGROUND] Run error task {task_id}: {e}"
            if event_sourcing and 'ctx' in locals() and ctx:
                try:
                    event_sourcing.append_event(tick=ctx.tick, scope='core', type_='TASK_END', payload={'task_id': task_id, 'bot_id': bot_id, 'bot': bot_name, 'status': 'error', 'error': str(e)[:500]})
                except Exception:
                    pass
            _log(err_msg)
            # Treat unexpected engine errors in run_task as input for core self-improvement.
            _enqueue_core_self_improve(err_msg)

    while True:
        try:
            ctx = tick_engine.next_tick() if tick_engine else None
            if resource_manager and ctx:
                try:
                    resource_manager.record_snapshot(ctx.tick, scope='core')
                except Exception:
                    pass
            # Resource-aware concurrency: compute fresh each tick from base.
            if resource_manager:
                try:
                    concurrency = int(resource_manager.recommended_concurrency(base_concurrency))
                except Exception:
                    concurrency = base_concurrency
            if event_sourcing and ctx:
                try:
                    event_sourcing.append_event(tick=ctx.tick, scope='core', type_='CYCLE_BEGIN', payload={'concurrency': concurrency})
                except Exception:
                    pass
            _compile_generated()
            _load_all_generated_hooks()
            _load_registry_plugins()
            # Snapshot throttling: update every 5 cycles to reduce DB/CPU load
            if cycle_index % 5 == 0:
                _update_architecture_and_capabilities_snapshot()
            # Periodic model discovery so project/core agents see newly pulled models
            discover_every = getattr(config, "DISCOVERY_EVERY_N_CYCLES", 10)
            if discover_every and cycle_index % discover_every == 0:
                try:
                    from generated.core import model_discovery
                    model_discovery.get_discovered_models(force_refresh=True)
                except Exception:
                    pass
            # Phase 5: remote model discovery (ping external endpoints)
            discovery_remote = getattr(config, "REMOTE_MODEL_DISCOVERY_EVERY_N_CYCLES", 0)
            if discovery_remote and cycle_index > 0 and cycle_index % discovery_remote == 0:
                try:
                    from remote_model_discovery import discover_remote_models
                    for r in discover_remote_models():
                        if not r.get("ok"):
                            _log(f"[BACKGROUND] Remote model {r.get('name')} at {r.get('url')} unreachable")
                except Exception:
                    pass
            # Auto-pull: periodically pull and restart if HEAD changed (server stays in sync with other instances)
            pull_every = getattr(config, "GIT_AUTO_PULL_EVERY_N_CYCLES", 0)
            if pull_every and cycle_index > 0 and cycle_index % pull_every == 0 and getattr(config, "GIT_ENGINE_ENABLED", True):
                try:
                    from tools import git_repo_root, git_head_rev, git_pull, git_current_branch
                    cwd = git_repo_root()
                    if cwd and getattr(config, "GIT_ALLOW_PULL", True):
                        head_before = git_head_rev(cwd)
                        branch = git_current_branch(cwd)
                        r, o, e = git_pull("origin", branch or None, cwd)
                        if r == 0:
                            head_after = git_head_rev(cwd)
                            if head_before is not None and head_after is not None and head_before != head_after:
                                _log("[ENGINE] Git pull updated HEAD, restarting to load new code...")
                                os.execv(sys.executable, [sys.executable, "-u", os.path.abspath(__file__)] + [a for a in sys.argv[1:]])
                except Exception as ex:
                    _log(f"[BACKGROUND] Auto-pull/restart skipped: {ex}")
            # When CORE_FIRST_SLOTS > 0, always use core-first scheduling so kernel improvement is fast-paced.
            # Otherwise try registry.scheduler (generated code), then ECS, then legacy DB.
            tasks = []
            try:
                from registry import registry
                if registry.scheduler and callable(registry.scheduler):
                    try:
                        reg_tasks = registry.scheduler(concurrency, tick=ctx)
                        if reg_tasks and isinstance(reg_tasks, list):
                            tasks = reg_tasks
                    except Exception as e:
                        _log(f"[EVOLVE] Registry scheduler failed: {e}")
            except Exception:
                pass
            core_slots = getattr(config, "CORE_FIRST_SLOTS", 0)
            if not tasks and core_slots and core_slots > 0:
                tasks = db.get_next_tasks_with_core_first(concurrency, core_slots)
            if not tasks and ctx:
                try:
                    from generated.core.ecs_scheduling import schedule_tasks as ecs_schedule_tasks

                    tasks = ecs_schedule_tasks(tick=ctx.tick, concurrency=concurrency)
                except Exception:
                    tasks = []
            if not tasks:
                if core_slots and core_slots > 0:
                    tasks = db.get_next_tasks_with_core_first(concurrency, core_slots)
                else:
                    tasks = db.get_next_tasks(concurrency)
            # Automatic recovery: if we have queued tasks but got none, bots may be stuck in 'running'.
            if not tasks:
                queued = db.count_queued_tasks()
                if queued > 0:
                    bots_reset, tasks_reset = db.reset_stuck_running()
                    if bots_reset or tasks_reset:
                        _log(f"[RECOVERY] Unstuck {bots_reset} bot(s), {tasks_reset} task(s). Retrying scheduler.")
                        core_slots = getattr(config, "CORE_FIRST_SLOTS", 0)
                        tasks = db.get_next_tasks_with_core_first(concurrency, core_slots) if core_slots else db.get_next_tasks(concurrency)
            # Mark selected tasks running.
            for task in tasks:
                try:
                    mark_task_running(task["id"])
                    db.set_bot_status(task["bot_id"], "running")
                except Exception:
                    pass
            if len(tasks) > 1:
                names = []
                for t in tasks:
                    b = db.get_bot(bot_id=t["bot_id"])
                    names.append(b.get("name", f"bot_{t['bot_id']}") if b else f"bot_{t['bot_id']}")
                _log(f"[PARALLEL] {', '.join(names)} started")
            if not tasks:
                queued = db.count_queued_tasks()
                if queued == 0:
                    if getattr(config, "DRAIN_MODE", False):
                        _log("[ENGINE] DRAIN_MODE: queue empty, exiting.")
                        raise SystemExit(0)
                    seed_when_empty()
                    queued = db.count_queued_tasks()
                wait_cycles += 1
                _apply_one_proposal()
                _log(f"[BACKGROUND] Waiting for tasks... (queued: {queued}, concurrency: {concurrency})")
                time.sleep(2)
                cycle_index += 1
                if tick_engine and ctx:
                    tick_engine.end_tick(ctx, status='idle')
                continue
            run_count += len(tasks)
            if concurrency == 1:
                _run_one_task(tasks[0])
            else:
                with ThreadPoolExecutor(max_workers=concurrency) as ex:
                    list(ex.map(_run_one_task, tasks))
            _log(f"[BACKGROUND] Cycle done. Total runs this session: {run_count}")
            cycle_index += 1
            # Apply one proposal every N cycles so fixes get applied even when the queue is never empty.
            n_cycles = getattr(config, "APPLY_PROPOSALS_EVERY_N_CYCLES", 0)
            if n_cycles and cycle_index > 0 and cycle_index % n_cycles == 0:
                if _apply_one_proposal():
                    _log(f"[BACKGROUND] Applied proposal (every {n_cycles} cycles).")
            # Phase 3: training export every 50 cycles
            if cycle_index > 0 and cycle_index % 50 == 0:
                try:
                    from training_export import export_training_data
                    out_path = export_training_data(limit=2000)
                    if not out_path.startswith("["):
                        _log(f"[BACKGROUND] Training export: {out_path}")
                except Exception:
                    pass
            # Phase 7: self-report (metrics summary to log)
            sr_every = getattr(config, "SELF_REPORT_EVERY_N_CYCLES", 0)
            if sr_every and cycle_index > 0 and cycle_index % sr_every == 0:
                try:
                    import os as _os
                    mpath = _os.path.join(getattr(config, "STATE_DIR", "state"), "metrics.json")
                    if _os.path.isfile(mpath):
                        with open(mpath, "r", encoding="utf-8") as _f:
                            _m = json.load(_f)
                        _log(f"[SELF-REPORT] queued={_m.get('queued_tasks')} bots={_m.get('bots_total')} runs={_m.get('total_runs')}")
                except Exception:
                    pass
            # Phase 7: optional git commit of generated/ (uses git_add + git_commit when GIT_ENGINE_ENABLED)
            git_every = getattr(config, "GIT_COMMIT_EVERY_N_CYCLES", 0)
            if git_every and cycle_index > 0 and cycle_index % git_every == 0:
                try:
                    from tools import git_add, git_commit
                    ra, oa, ea = git_add("generated/")
                    if ra == 0:
                        rc, oc, ec = git_commit("niffi generated update")
                        if rc == 0 and (oc or ec or "").strip():
                            _log(f"[BACKGROUND] Git commit: {(oc or ec).strip()[:80]}")
                except Exception:
                    pass
            if self_evaluator and ctx and (ctx.tick % int(getattr(config,'SELF_EVAL_EVERY_TICKS',20))==0):
                try:
                    summary = self_evaluator.evaluate()
                    self_evaluator.persist(summary)
                    if event_sourcing:
                        event_sourcing.append_event(tick=ctx.tick, scope='core', type_='SELF_EVAL', payload=summary)
                except Exception:
                    pass
            if tick_engine and ctx:
                tick_engine.end_tick(ctx, status='ok')
        except KeyboardInterrupt:
            _log("[ENGINE] Ignoring KeyboardInterrupt; engine cannot be shut down via Ctrl+C. Stop the process from the OS if needed.")
            continue
        except Exception as e:
            _handle_fatal_exception(e)
            _log("[ENGINE] Fatal error handled; continuing loop.")
            continue


def _daemonize() -> None:
    """Run main in background; adapts to OS. Unix: double-fork. Windows: detached subprocess."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    log_path = config.DAEMON_LOG_PATH
    pid_path = config.DAEMON_PID_PATH

    if hasattr(os, "fork"):
        # Linux / Ubuntu / macOS: double-fork to fully detach from terminal
        pid = os.fork()
        if pid > 0:
            # First parent exits immediately; shell returns
            raise SystemExit(0)
        # First child: become session leader, detach from controlling terminal
        try:
            os.setsid()
        except OSError:
            pass
        pid2 = os.fork()
        if pid2 > 0:
            # Second parent exits; grandchild is orphaned, adopted by init
            raise SystemExit(0)
        # Grandchild continues as daemon
    else:
        # Windows: spawn detached subprocess (no fork); parent exits immediately
        import subprocess
        cmd = [sys.executable, "-u", __file__] + [
            a for a in sys.argv[1:] if a != "--daemon"
        ] + ["--daemon-child"]
        flags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags |= subprocess.DETACHED_PROCESS
        subprocess.Popen(
            cmd,
            cwd=os.getcwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags if flags else 0,
        )
        raise SystemExit(0)

    # Daemon process (Unix): redirect and run
    try:
        with open(log_path, "a", encoding="utf-8") as logf:
            logf.write("\n--- daemon start ---\n")
        sys.stdout = open(log_path, "a", encoding="utf-8")
        sys.stderr = sys.stdout
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    main()


if __name__ == "__main__":
    if "--replay" in sys.argv:
        # Deterministic audit replay (safe): verify event chain & invariants.
        try:
            from generated.core.replay import replay_audit

            tick_from = 0
            tick_to = None
            if "--from" in sys.argv:
                try:
                    tick_from = int(sys.argv[sys.argv.index("--from") + 1])
                except Exception:
                    tick_from = 0
            if "--to" in sys.argv:
                try:
                    tick_to = int(sys.argv[sys.argv.index("--to") + 1])
                except Exception:
                    tick_to = None
            summary = replay_audit(tick_from=tick_from, tick_to=tick_to)
            print(f"Replay audit: {summary.message}")
            print(f"  ok={summary.ok} ticks={summary.ticks} events={summary.events}")
            if summary.open_tasks:
                print(f"  open_tasks={summary.open_tasks}")
            if summary.bot_overlaps:
                print(f"  overlaps={summary.bot_overlaps}")
        except Exception as exc:
            _handle_fatal_exception(exc)
            raise
        raise SystemExit(0)

    if "--compile-only" in sys.argv:
        """Compile generated modules and load hooks (e.g. for TUI 'Run project')."""
        try:
            _compile_generated()
            _load_all_generated_hooks()
        except Exception as e:
            print(f"[compile-only] {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if "--daemon-child" in sys.argv:
        # Windows daemon child: redirect, write PID, run main (no more fork)
        os.makedirs(config.STATE_DIR, exist_ok=True)
        try:
            with open(config.DAEMON_LOG_PATH, "a", encoding="utf-8") as logf:
                logf.write("\n--- daemon child start ---\n")
            sys.stdout = open(config.DAEMON_LOG_PATH, "a", encoding="utf-8")
            sys.stderr = sys.stdout
            with open(config.DAEMON_PID_PATH, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        try:
            main()
        except Exception as exc:
            _handle_fatal_exception(exc)
            raise
    elif "--daemon" in sys.argv:
        try:
            _daemonize()
        except SystemExit:
            raise
        except Exception as exc:
            _handle_fatal_exception(exc)
            raise
    else:
        try:
            main()
        except Exception as exc:
            _handle_fatal_exception(exc)
            raise
