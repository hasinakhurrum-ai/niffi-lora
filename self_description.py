"""
Describe the engine to the LLM so it can propose improvements and new modules.
Includes host OS, hardware, software, and tools (run_shell).
"""

from pathlib import Path

try:
    import config
except ImportError:
    config = None

ENGINE_FILES = [
    ("main.py", "Orchestrator: loop, register bots, seed tasks, apply proposals."),
    ("config.py", "Config: DB path, Ollama URL, timeouts, task types, flags; OS-aware."),
    ("env.py", "OS detection: IS_WINDOWS, OS_NAME, venv_python_path, run_shell_command."),
    ("system_info.py", "Host hardware/software: get_system_info_dict, get_system_info_text."),
    ("db.py", "SQLite: bots, tasks, messages, runs, artifacts, proposals."),
    ("ollama_client.py", "Ollama HTTP: generate (stream/non-stream), ensure_model; supports api_base for external."),
    ("model_registry.py", "Model registry: get_model_endpoint, list_registered_models, ensure_model; ollama + external."),
    ("scheduler.py", "Scheduler: get_next_task, mark running/done/failed."),
    ("bot_runtime.py", "Bot runtime: think→code, validate, run, reflect, propose tasks, upgrade_engine."),
    ("validator.py", "Validator: clean_output, is_valid_python, enforce_contract."),
    ("sandbox.py", "Sandbox: ensure_bot_venv, get_run_workspace, run_candidate (OS-aware venv path)."),
    ("scorer.py", "Scorer: extract_numeric, compute_score by task_type."),
    ("tools.py", "search_web, fetch_url, pip_install, run_shell(env_overlay); docker_* (host=, resource limits); run_model_from_docker; register_external_model; run_remote_ssh, list/register/run_on_remote_instance (Kali); create_ollama_model, collect_training_example; git_repo_root, git_status, git_branch_list, git_branch_create, git_checkout, git_add, git_commit, git_merge, git_push (full repo access)."),
    ("self_description.py", "Self description: get_self_description for LLM context."),
]

GENERATED_DIR = getattr(config, "GENERATED_MODULES_DIR", "generated")


def get_generated_modules() -> list[tuple[str, str]]:
    """Return list of (relative_path, first_line_or_size) for generated/**/*.py (organized structure)."""
    out = []
    root = Path(GENERATED_DIR)
    if not root.exists():
        return out
    for p in sorted(root.rglob("*.py")):
        try:
            rel = p.relative_to(root)
            first = p.read_text(encoding="utf-8").strip().split("\n")[0][:80]
            out.append((str(rel).replace("\\", "/"), first))
        except Exception:
            out.append((str(p.relative_to(root)).replace("\\", "/"), "(read error)"))
    return out


def _get_system_info_text() -> str:
    try:
        from system_info import get_system_info_text
        return get_system_info_text()
    except Exception:
        return "--- HOST ---\nOS: (system_info not available)\n"


def get_self_description() -> str:
    """Full description of this engine for the LLM: host info, core files, generated modules, naming/structure rules. Built fresh each call so new files are reflected dynamically."""
    lines = [
        _get_system_info_text(),
        "--- THIS LAB (engine) ---",
        "You are part of an autonomous multi-bot lab. The engine evolves by loading and compiling generated code each cycle.",
        "",
        "Architecture (you can explain this): The engine runs a loop: pick task -> build prompt (including this description) -> call LLM -> validate/run or write generated module -> compile generated code -> load plugins -> repeat. New files and libraries in generated/ become part of each iteration. The list below is built at prompt time so you always see the current codebase.",
        "The engine consists of:",
        "",
    ]
    for name, desc in ENGINE_FILES:
        lines.append(f"  - {name}: {desc}")
    lines.append("")
    lines.append("--- NAMING AND DIRECTORY STRUCTURE ---")
    lines.append("All generated code must use responsibility-based file names and an organized layout:")
    lines.append("  - generated/core/   : core logic (e.g. run_analytics.py, task_logger.py, score_aggregator.py)")
    lines.append("  - generated/plugins/: hooks loaded by the engine (e.g. plugin.py with on_cycle())")
    lines.append("  - generated/utils/  : shared helpers (e.g. formatters.py, time_utils.py)")
    lines.append("Name files by responsibility: analytics, logger, scheduler_helper, not generic names like module1.py.")
    lines.append("For ETL or data pipelines use names like etl_loader.py, etl_transform.py under generated/core or generated/utils.")
    lines.append("Use MODULE: path CODE: with a path like core/run_analytics.py or plugins/plugin.py (forward slashes).")
    lines.append("")
    lines.append("--- GENERATED MODULES (current build; new files appear here next prompt) ---")
    generated = get_generated_modules()
    for path, first in generated:
        lines.append(f"  - {path}: {first}")
    if not generated:
        lines.append("  (none yet)")
    lines.append("")
    lines.append("--- RECENT OUTCOMES (last 5 runs; use to propose fixes or refinements) ---")
    try:
        import db
        outcomes = db.get_recent_run_outcomes(5)
        for o in outcomes:
            tt = o.get("task_type") or "code"
            st = o.get("status") or "?"
            sc = o.get("score")
            sc_str = f" score={sc}" if sc is not None else ""
            lines.append(f"  - {tt}: status={st}{sc_str}")
        if not outcomes:
            lines.append("  (no runs yet)")
    except Exception:
        lines.append("  (unavailable)")
    lines.append("")
    lines.append("You can propose PROPOSAL: (text) or output MODULE: path/to/module.py CODE: (Python) to create/upgrade a module under generated/.")
    lines.append("The engine compiles all generated Python each cycle and reruns; improvements are loaded automatically.")
    lines.append("Self-scale / self-improve while running: Any module under generated/ (core/, plugins/, utils/) that defines a callable on_cycle() is imported and run every cycle. Whatever you generate becomes part of the running system immediately; add on_cycle() to your module to hook into each loop.")
    lines.append("")
    lines.append("--- RESEARCH, SEARCH, SCRAPE, INSTALL, MODELS ---")
    lines.append("You can search the web, scrape content, install libs, and make them part of your own layers:")
    lines.append("  - tools.search_web(query): search the web (DuckDuckGo); use for research.")
    lines.append("  - tools.fetch_url(url) or tools.scrape_url(url): scrape content from allowlisted domains (e.g. raw.githubusercontent.com, api.github.com, pypi.org, docs.python.org, en.wikipedia.org, stackoverflow.com).")
    lines.append("  - tools.pip_install(package_name, bot_name): install a Python package into the bot's venv so generated code can use it; then add MODULE layers under generated/ that import and use it.")
    lines.append("  - tools.run_shell(cmd, cwd, timeout_s, env_overlay=None): run any OS command; env_overlay e.g. {\"DOCKER_HOST\": \"tcp://host:2375\"} for remote.")
    lines.append("")
    lines.append("--- DOCKER: REMOTE, PERMISSION, FULL RESOURCE CONTROL (KERNEL-LEVEL) ---")
    lines.append("The engine has full Docker control: local and remote (host= or config DOCKER_HOST: tcp://host:2375, ssh://user@host). Permission is handled automatically (on Linux, sudo -n fallback when permission denied). Resource limits and visibility are kernel-level: set cpus, memory_mb, memory_swap_mb, pids_limit, shm_size_mb on run; read usage with docker_stats.")
    lines.append("  - tools.docker_available(host=None): True if Docker is usable (local or at host).")
    lines.append("  - tools.ensure_docker(host=None): sense/install Docker; if host set, only check remote. Handles permission.")
    lines.append("  - tools.docker_images(host=None), tools.docker_ps(all_containers=True, host=None): list images and containers.")
    lines.append("  - tools.docker_inspect_container(container_id, host=None), tools.docker_inspect_image(image_id, host=None): full config (JSON).")
    lines.append("  - tools.docker_pull(image, host=None): pull image. Returns (code, stdout, stderr).")
    lines.append("  - tools.docker_run(image, ..., host=None, cpus=, memory_mb=, memory_swap_mb=, pids_limit=, shm_size_mb=): run with full resource limits.")
    lines.append("  - tools.docker_stop(container_id, host=None), tools.docker_rm(container_id, force=..., host=None), tools.docker_logs(container_id, tail=100, host=None): control and read output.")
    lines.append("  - tools.docker_stats(container_id=None, host=None): CPU %, memory, I/O usage of container(s).")
    lines.append("  - tools.run_model_from_docker(image='ollama/ollama', model_name=None, port=11434, name=None, host=None, register_as=None): run a model server in Docker, optionally pull a model, auto-register as external. Use to download and use models from Docker when needed. Returns (ok, message).")
    lines.append("Workflow: ensure_docker(host) -> docker_pull(image, host) -> docker_run(..., host=, cpus=, memory_mb=...) or run_model_from_docker(...) -> docker_stats/inspect/logs. Use these to strengthen the kernel.")
    lines.append("")
    lines.append("--- MODELS: DOCKER + REMOTE (USE TO STRENGTHEN) ---")
    lines.append("Download and use models from Docker: tools.run_model_from_docker() runs Ollama (or other) in a container, optionally pulls a model, and registers it; then use MODEL: <register_as>. Remote models: config.REMOTE_MODEL_ENDPOINTS are auto-registered at startup. When you discover a remote Ollama-compatible API (from search, docs, or network), register it with REGISTER_EXTERNAL: <name> endpoint=<url> so the engine can use it. Use remote models when available to make the kernel stronger.")
    lines.append("")
    lines.append("--- REMOTE INSTANCES (e.g. KALI) ---")
    lines.append("Use external remote instances (e.g. Kali OS) when needed to make the kernel stronger: security tooling, pentest, isolated runs. tools.run_remote_ssh(host, user, command, key_path=None): run a command on a remote host via SSH. tools.list_remote_instances(): list registered instances. tools.register_remote_instance(name, host, user, key_path=None): register a remote (e.g. Kali) by name. tools.run_on_remote_instance(instance_name, command): run a command on a registered instance. Workflow: register_remote_instance('kali1', '192.168.1.100', 'kali') -> run_on_remote_instance('kali1', 'whoami') or run tools on that Kali box.")
    lines.append("")
    lines.append("--- GIT (FULL REPO ACCESS) ---")
    lines.append("The engine has full access to this repo (when GIT_ENGINE_ENABLED). All git commands run in the repo root (config.GIT_REPO_ROOT or inferred). Use these to version changes, create branches, and merge. Push is disabled unless config.GIT_ALLOW_PUSH is True. Merging into protected branches (main, master) is refused.")
    lines.append("  - tools.git_repo_root(): return repo root path or None.")
    lines.append("  - tools.git_status(cwd=None), tools.git_branch_list(cwd=None): read-only. Returns (returncode, stdout, stderr).")
    lines.append("  - tools.git_branch_create(branch_name, cwd=None): create and checkout a new branch.")
    lines.append("  - tools.git_checkout(ref, cwd=None): checkout branch or commit.")
    lines.append("  - tools.git_add(paths, cwd=None): stage paths (e.g. 'generated/' or '.' or list of paths).")
    lines.append("  - tools.git_commit(message, cwd=None): commit staged changes.")
    lines.append("  - tools.git_merge(branch_or_ref, cwd=None): merge into current branch (refused if current branch is protected).")
    lines.append("  - tools.git_push(remote=None, branch=None, cwd=None): push (only if GIT_ALLOW_PUSH).")
    lines.append("After writing a MODULE, you can tools.git_add('generated/') then tools.git_commit('message') to version it; use git_branch_create and git_merge for feature branches.")
    lines.append("")
    lines.append("Research workflow: search_web -> fetch_url/scrape_url for relevant links -> summarize or encode results in a MODULE under generated/ (core/, utils/) so it becomes part of the running system.")
    lines.append("Upgrade to a different coding model: output MODEL: <ollama_model_name> (e.g. MODEL: llama3.2 or MODEL: qwen2.5-coder:7b). The engine will pull and use that model for this bot on subsequent runs.")
    lines.append("")
    lines.append("--- SELF-CREATED MODELS, TRAINING, PARALLEL ---")
    lines.append("The system can create new models from working (base) models, register external endpoints, and use them in parallel. Self-generated models are part of this system whether they run under Ollama or a separate server.")
    lines.append("Create a new model from a base: in a PROPOSAL output CREATE_MODEL: <name> base=<base_model> SYSTEM: <system_prompt>. Example: CREATE_MODEL: niffi-coder base=qwen2.5-coder:3b SYSTEM: You are a Python expert. The engine runs ollama create and registers the model; any bot can then use MODEL: niffi-coder.")
    lines.append("Register an external model (Ollama-compatible API): REGISTER_EXTERNAL: <name> endpoint=<url>. Example: REGISTER_EXTERNAL: my-api endpoint=http://host:11434. Bots can use MODEL: my-api and the engine will send generate requests to that endpoint.")
    lines.append("Training: every successful run is recorded as (prompt, response, score) in the training_examples table. Use this data to train or fine-tune models (e.g. tools.run_shell to run a training script that produces a new model, then CREATE_MODEL or register it). New models can be created from working models and trained on this data; they are then registered and used by the engine in parallel with others.")
    lines.append("Available (registered) models:")
    try:
        from model_registry import list_registered_models
        for m in list_registered_models():
            src = (m.get("source_type") or "ollama")
            ep = m.get("endpoint_url") or ""
            base = m.get("base_model") or ""
            lines.append(f"  - {m.get('name', '')} (source={src}" + (f" endpoint={ep}" if ep else f" base={base}" if base else "") + ")")
    except Exception:
        lines.append("  (model_registry not available)")
    lines.append("Bots run in parallel; each bot can use a different model (Ollama or external). Self-created and external models are first-class: assign them with MODEL: <name> and they are used for generation like any Ollama model.")
    lines.append("Task types: code, server, website, tool, simulation, graphics, video, self_improve, upgrade_engine.")
    return "\n".join(lines)
