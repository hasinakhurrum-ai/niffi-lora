# Niffi NextGen Kernel (Implemented Scaffolding)

This repo now includes **deterministic ticks**, **event sourcing**, a **modular ECS**, **resource-aware scheduling hooks**, **model performance logging**, **distributed worker scaffolding**, and a **formal self-evaluator**.

> This is an incremental upgrade: the existing engine loop still works as-is, but now it is wrapped by a deterministic `tick` and emits structured events into SQLite so you can replay/inspect the engine.

## What’s implemented

### 1) Deterministic simulation (engine-level)
- Each engine cycle is a **tick** stored in `ticks`.
- A deterministic seed is derived from `(ENGINE_SEED_SALT, tick)` and applied to:
  - `PYTHONHASHSEED`
  - `random.seed(seed)` (process-global, best effort)
- Tick begin/end events are appended into `events`.

Module: `generated/core/tick_engine.py`

### 2) Event-sourced architecture
- Append-only `events` table with `(tick, seq)` ordering.
- Per-tick hash chain for integrity (`verify_chain()`).

Module: `generated/core/event_sourcing.py`

### 3) Fully modular ECS
- SQLite-backed entities and components:
  - `ecs_entities`
  - `ecs_components`
- Plugin-ready `SystemRegistry` to register and run systems per tick.

Module: `generated/core/ecs.py`

### 4) Distributed (scaffold)
- `WorkerPool` abstraction for thread/process execution.
- Remote workers are intentionally left as a future extension (HTTP RPC interface).

Module: `generated/core/distributed.py`

### 5) Self-evaluating architecture (formal)
- Rule-based evaluator checks schema + subsystems.
- Writes to `architecture_state` component `core_self_eval`.
- Emitted as a `SELF_EVAL` event every `SELF_EVAL_EVERY_TICKS` (default 20).

Module: `generated/core/self_evaluator.py`

### 6) Model performance aware
- Every LLM call is logged into `llm_calls` (latency/status + hashes).
- Rolling `model_health` is updated with EMA latency + approximate failure rate.

Module: `generated/core/model_metrics.py`  
Patch: `bot_runtime.py` uses `_generate(...)` wrapper.

### 7) Resource-aware
- Resource snapshots recorded into `resource_snapshots`.
- Concurrency cap can be reduced under pressure.

Module: `generated/core/resource_manager.py`  
Patch: `main.py` consults it each tick.

## DB schema additions
Added tables:
- `ticks`, `events`
- `ecs_entities`, `ecs_components`
- `resource_snapshots`
- `llm_calls`

(See `db.py`)

## How to run
- Start engine: `python -u main.py`
- Start TUI: `python -m tui.run` (if present in your environment)

## Next steps (recommended)
1. Emit structured lifecycle events (THINK/PLAN/CODE/APPLY/EXEC/TEST/...) from `bot_runtime` and `sandbox`.
2. Implement **event-sourced derived state** (ECS state derived from events instead of direct writes).
3. Introduce a `projects` table (project_id) so multi-project is first-class (not just bot name).
4. Add remote worker RPC and task leasing to support true distributed execution.
