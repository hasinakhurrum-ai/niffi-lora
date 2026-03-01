# Niffi — Autonomous Lab

Multi-bot orchestrator that **thinks** (reasoning + reflection), **creates** (code, servers, websites, tools, simulations, graphics, video), **self-improves** (proposals + modules in `generated/`), and **grows** (level, creations count, growth log). Creations are stored so you can **see and run** everything it built. Optional **daemon** mode and **restricted fetch** from the internet.

## Run

1. **Ollama** running. Default: `http://localhost:11434`.

2. **Python 3.10+**, venv:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Foreground:**
   ```bash
   python main.py
   ```
   Stop with Ctrl+C.

4. **Daemon (background):**
   ```bash
   python main.py --daemon
   ```
   Logs go to `state/daemon.log`, PID to `state/daemon.pid`. Stop by killing that PID.

## See and run what it created

- **List creations:** `python showcase.py` (or `python -m showcase` from project root)
- **Run one:** `python showcase.py run <id>`

Creations are copied under `creations/<id>/` with a manifest; each has a type (code, server, website, tool, simulation, graphics, video) and a run command.

## What it does

- **Task types:** `code`, `server`, `website`, `tool`, `simulation`, `graphics`, `video`, `self_improve`, `upgrade_engine`.
- **Think → code:** REASONING then CODE (or PROPOSAL / MODULE: name.py CODE:). Console shows ASK, RESPONSE, CODE when `PRINT_ASK_RESPONSE_CODE = True`.
- **Level & growth:** `state/level.json` holds level, goal, creations count. `state/growth.log` is appended on each new creation so you see it growing.
- **Creations:** Every successful run (code/server/website/tool/simulation/graphics/video) is registered in DB and copied to `creations/<id>/`; you can list and run them from the showcase.
- **Self-improve:** Proposals (text) and MODULE: code go to DB / `generated/`. When idle, one proposal can be applied to a bot’s system_prompt. Generated modules are listed in the self-description so the lab can extend itself.
- **Generated plugin:** If `generated/plugin.py` exists and defines `on_cycle()`, it is called each loop so the engine can use its own code.
- **Fetch (allowlist):** `tools.fetch_url(url)` is available for allowlisted domains only (`FETCH_ALLOWED_DOMAINS` in config).

## Layout

| Path | Purpose |
|------|--------|
| `main.py` | Orchestrator; `--daemon` for background |
| `creations.py` | Register creations, manifest, growth_log |
| `showcase.py` | List and run creations |
| `level.py` | get_level / set_level / increment_creations |
| `tools.py` | fetch_url (allowlist only) |
| `state/level.json` | Level, goal, creations_count |
| `state/growth.log` | Append-only growth log |
| `state/daemon.pid`, `state/daemon.log` | Daemon PID and log |
| `creations/` | One dir per creation (id, manifest.json) |
| `generated/` | Bot-written modules (engine upgrades) |

## Config

- **TIMEOUT_S**, **SERVER_TIMEOUT_S**, **SIMULATION_TIMEOUT_S**
- **TASK_TYPES**: includes simulation, graphics, video
- **CREATIONS_DIR**, **LEVEL_PATH**, **GROWTH_LOG_PATH**, **DAEMON_PID_PATH**, **DAEMON_LOG_PATH**
- **FETCH_ENABLED**, **FETCH_ALLOWED_DOMAINS**, **FETCH_TIMEOUT_S**, **FETCH_MAX_BYTES**

## Safety

- Timeouts and output truncation; sandbox cwd = workspace only. No unrestricted internet; fetch is allowlist-only. Daemon is a simple background process with log file; no self-replication or “viral” spread.# niffi-lora
