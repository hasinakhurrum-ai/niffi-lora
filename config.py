"""Central config. OS-aware via env (Windows/Linux/Darwin)."""

import os

try:
    import env
except ImportError:
    env = None

# OS (dynamic)
IS_WINDOWS = getattr(env, "IS_WINDOWS", os.name == "nt")
OS_NAME = getattr(env, "OS_NAME", "windows" if os.name == "nt" else "linux")

# Paths
STATE_DIR = "state"
CREATIONS_DIR = "creations"
# Each project (bot) has its own directory: projects/<bot_name>/ with venv, runs, creations
PROJECTS_DIR = "projects"
DB_PATH = os.path.join(STATE_DIR, "lab.db")
LEVEL_PATH = os.path.join(STATE_DIR, "level.json")
GROWTH_LOG_PATH = os.path.join(STATE_DIR, "growth.log")
DAEMON_PID_PATH = os.path.join(STATE_DIR, "daemon.pid")
DAEMON_LOG_PATH = os.path.join(STATE_DIR, "daemon.log")
# Unified engine log (foreground + daemon); TUI "Show logs" reads these
ENGINE_LOG_PATH = os.path.join(STATE_DIR, "engine.log")
ENGINE_ERROR_LOG_PATH = os.path.join(STATE_DIR, "error.log")  # Errors only; set None to disable
ENGINE_LOG_LEVEL = "INFO"
ENGINE_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB before rotate
ENGINE_LOG_BACKUP_COUNT = 2
OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5-coder:3b"
# Optional fallback chain for dynamic routing (model performance aware).
# Keys are scopes ("core"/"project") or task types; values are ordered model names.
MODEL_FALLBACKS = {
    "core": ["qwen2.5-coder:3b", "llama3.1:8b"],
    "project": ["qwen2.5-coder:3b", "llama3.1:8b"],
}
# Directory for Modelfiles when creating new models from base (ollama create)
MODELS_DIR = os.path.join(STATE_DIR, "models")
OLLAMA_CREATE_TIMEOUT_S = 600

# Model discovery & self-driven selection (project & core agents)
DISCOVERY_TTL_S = 300
DISCOVERY_EVERY_N_CYCLES = 10
DISCOVERY_MAX_POOL = 15
MODEL_NUM_PREDICT = 8192
MODEL_RETRY_ON_TIMEOUT = True
MODEL_RETRY_COUNT = 2
ALLOW_MODEL_PULL = True

# Execution
TIMEOUT_S = 10
SERVER_TIMEOUT_S = 30
SIMULATION_TIMEOUT_S = 60
MAX_STDOUT_CHARS = 20000
MAX_STDERR_CHARS = 20000
STREAM_LLM = True
STREAM_PROGRAM_OUTPUT = True
REPAIR_ATTEMPTS = 1
# When a generated module fails to compile, ask the model for a fix and retry (self_improve / upgrade_engine)
MODULE_COMPILE_REPAIR_ATTEMPTS = 3

# Context
MAX_CONTEXT_MESSAGES = 10
# Number of tasks to run in parallel per cycle (one task per bot; set to bot count to run all agents at once)
BOTS_CONCURRENCY = 5
TASK_PROPOSAL_EVERY_N_RUNS = 3
# Core gets higher priority so kernel improvement is fast-paced; project tasks fill remaining slots.
USER_TASK_PRIORITY = 3
CORE_TASK_PRIORITY = 8
# Reserve this many slots per cycle for core (system) tasks when available (0 = use get_next_tasks only).
CORE_FIRST_SLOTS = 1

# Global set of task types the engine understands.
TASK_TYPES = (
    "code", "server", "website", "tool",
    "simulation", "graphics", "video",
    "test", "deploy",
    "self_improve", "upgrade_engine",
    "optimize_runtime", "security_patch",
    "performance_tune", "proposal_review",
    "design_project",
    "quantum_circuit",
)

# Strict DevOS roles:
# - CORE agents may only run core/system task types (no project/app work).
# - PROJECT agents may only run project/app task types (no engine governance).
CORE_TASK_TYPES = (
    "self_improve",
    "upgrade_engine",
    "optimize_runtime",
    "security_patch",
    "performance_tune",
    "proposal_review",
    "simulation",       # system-level simulations are allowed for core
    "design_project",   # core designs projects but does not implement them
    "quantum_circuit",  # design/run quantum circuits for kernel (Phase 4)
)

PROJECT_TASK_TYPES = (
    "code", "server", "website", "tool",
    "simulation", "graphics", "video",
    "test", "deploy",
)

APPLY_PROPOSALS = True
# Apply one unapplied proposal every N cycles even when the queue is not empty (0 = only when queue is empty).
APPLY_PROPOSALS_EVERY_N_CYCLES = 3
GENERATED_MODULES_DIR = "generated"
# Subdirs the engine expects under generated/: core (logic), plugins (on_cycle etc), utils (helpers)
GENERATED_SUBDIRS = ("core", "plugins", "utils")
PRINT_ASK_RESPONSE_CODE = True

# Creations & growth (paths set above)

# Fetch: when FETCH_ALLOWLIST_ONLY is False, any URL can be fetched (engine decides when to use internet).
FETCH_ENABLED = True
FETCH_ALLOWLIST_ONLY = False  # True = only FETCH_ALLOWED_DOMAINS; False = no restriction
FETCH_ALLOWED_DOMAINS = (
    "raw.githubusercontent.com", "api.github.com", "pypi.org", "docs.python.org",
    "api.duckduckgo.com", "en.wikipedia.org", "stackoverflow.com",
)
FETCH_TIMEOUT_S = 15
FETCH_MAX_BYTES = 500_000

# Web search (uses DuckDuckGo API via fetch_url; api.duckduckgo.com must be in FETCH_ALLOWED_DOMAINS)
SEARCH_ENABLED = True
SEARCH_MAX_RESULTS = 5

# pip install into bot venv (tools.pip_install)
PIP_INSTALL_TIMEOUT_S = 120

# OS command execution (tools.run_shell)
SHELL_TIMEOUT_S = 120

# Docker: sense and install if missing (OS-agnostic); full control for engine advancement.
DOCKER_INSTALL_ENABLED = True  # If Docker not present, attempt install (winget/choco/brew/get.docker.com).
DOCKER_TIMEOUT_S = 300        # Timeout for docker pull / docker run.
DOCKER_INSPECT_MAX_CHARS = 15000  # Truncate inspect JSON to this length when returning to LLM.
# Remote Docker: set to tcp://host:2375 or ssh://user@host to control remote daemon. Empty = local only.
DOCKER_HOST = os.environ.get("DOCKER_HOST", "")
# Permission: on Linux, if docker fails with permission denied, retry with sudo (sudo -n). Set False to disable.
DOCKER_USE_SUDO_FALLBACK = True

# Remote model endpoints: list of {"name": "my-remote", "url": "http://host:11434"} to auto-register at startup.
# Bots can then use MODEL: my-remote. When you discover a remote Ollama-compatible API, register it via REGISTER_EXTERNAL or add here.
REMOTE_MODEL_ENDPOINTS = []
# Phase 5: ping external endpoints every N cycles and log availability (remote_model_discovery.discover_remote_models).
REMOTE_MODEL_DISCOVERY_EVERY_N_CYCLES = 20
# When resource_manager is absent, cap concurrency to this. None = no cap.
RESOURCE_CONCURRENCY_CAP = None

# Remote instance roles (Phase 2): which task types can use which role. role -> list of task_type.
# e.g. pentest only for self_improve, security_patch, upgrade_engine.
REMOTE_INSTANCE_ROLE_POLICY = {
    "pentest": ["self_improve", "security_patch", "upgrade_engine", "proposal_review"],
    "sandbox": ["code", "test", "server", "tool", "simulation", "graphics", "video", "deploy"],
    "general": None,  # None = allow all
}

# Sandboxing (Phase 2): when True, run bot-generated code inside a Docker container instead of host venv.
RUN_GENERATED_IN_DOCKER = False
RUN_GENERATED_DOCKER_IMAGE = "python:3.11-slim"

# --- Hardware / resource profile (DevOS kernel constraints) ---
# These values describe the host and guide model selection & concurrency.
HARDWARE_CLASS = "cpu_16gb"  # e.g. cpu_16gb, cpu_32gb, gpu_24gb
CPU_ONLY = True
TOTAL_RAM_GB = 16
GPU_VRAM_GB = 0

# Concurrency / scheduling limits for LLM-heavy work and projects.
MAX_LLM_PARALLEL = 2
MAX_PROJECTS_RUNNING = 2

# Governance priorities: how aggressively the core vs projects should act.
CORE_PRIORITY = "high"     # high | medium | low
PROJECT_PRIORITY = "medium"

# LLM response size guardrail (planner / core should respect this in prompts).
MAX_TOKEN_RESPONSE = 2048

# Governance switches for what the core is allowed to do automatically.
ALLOW_MODEL_PULL = True

# Deterministic core
ENGINE_SEED_SALT = os.environ.get('NIFFI_ENGINE_SEED_SALT', 'niffi')
SELF_EVAL_EVERY_TICKS = int(os.environ.get('NIFFI_SELF_EVAL_EVERY_TICKS', '20'))

# Config profile: dev | quantum | fleet | security (overrides applied from profiles.py)
CONFIG_PROFILE = os.environ.get("NIFFI_PROFILE", "full")

# Health endpoint (Phase 1)
HEALTH_ENABLED = True
HEALTH_PORT = int(os.environ.get("NIFFI_HEALTH_PORT", "8765"))
# Phase 6: webhook in (POST /incoming/task, /incoming/proposal)
WEBHOOK_IN_ENABLED = False
# Phase 6: webhook out - POST to this URL on task end (async). JSON: task_id, bot_id, status, score.
WEBHOOK_OUT_URL = os.environ.get("NIFFI_WEBHOOK_OUT_URL", "")

# Phase 7: graceful drain - when True, stop taking new tasks, finish queue, then exit.
DRAIN_MODE = False
# Phase 7: self-report every N cycles (append metrics summary to engine log). 0 = disabled.
SELF_REPORT_EVERY_N_CYCLES = 0
# Phase 7: git commit generated/ every N cycles. 0 = disabled.
GIT_COMMIT_EVERY_N_CYCLES = 0

# Git: full repo access (branch, add, commit, merge, optional push). All commands run in GIT_REPO_ROOT.
GIT_ENGINE_ENABLED = True
GIT_REPO_ROOT = os.environ.get("NIFFI_GIT_REPO_ROOT", "")  # Empty = infer from tools.py location (repo containing .git).
GIT_ALLOW_PUSH = False
GIT_PROTECTED_BRANCHES = ("main", "master")  # Refuse merging into these branches via automation.
GIT_AUDIT = True   # Emit audit_log for every mutating git op (branch, checkout, add, commit, merge, push).
GIT_TIMEOUT_S = 30

# Quantum (Phase 4); install qiskit, qiskit-aer to use
QUANTUM_ENABLED = False
QUANTUM_DEFAULT_BACKEND = "qasm_simulator"
