"""
Bot runtime: think → code, validate, run (code/server/website/tool), reflect, propose tasks.
Supports task_type: code, server, website, tool, self_improve, upgrade_engine.
Console: print ASK, RESPONSE, CODE when PRINT_ASK_RESPONSE_CODE is True.
"""

import py_compile
import re
import sys
from pathlib import Path

import config
import db
import engine_log
import prompts
from ollama_client import generate as ollama_generate, generate_with_fallback as _ollama_fallback, ensure_model as _ollama_ensure_model
from sandbox import ensure_bot_venv, get_project_workspace, get_run_workspace, run_candidate

try:
    from model_registry import get_model_endpoint, ensure_model
except ImportError:
    def get_model_endpoint(name):
        return (None, name or config.DEFAULT_MODEL)
    def ensure_model(name):
        _ollama_ensure_model(name or config.DEFAULT_MODEL)
from scorer import compute_score
from validator import clean_output, enforce_contract, is_valid_python, get_python_parse_error

try:
    from self_description import get_self_description
except ImportError:
    def get_self_description() -> str:
        return "Engine: main, config, db, ollama_client, scheduler, bot_runtime, validator, sandbox, scorer."


REASONING_CODE_SEP = re.compile(r"\n\s*CODE:\s*\n", re.IGNORECASE)
PROPOSAL_RE = re.compile(r"PROPOSAL:\s*(.+)", re.IGNORECASE | re.DOTALL)
MODULE_CODE_RE = re.compile(r"MODULE:\s*([^\s]+\.py)\s*\n\s*CODE:\s*\n?(.+)", re.IGNORECASE | re.DOTALL)
# Fallback: allow flexible whitespace/newlines between MODULE path and CODE block
MODULE_CODE_RE_LOOSE = re.compile(r"MODULE:\s*([^\s]+\.py)\s+CODE:\s*[\r\n]*(.+)", re.IGNORECASE | re.DOTALL)
TASK_PROPOSAL_RE = re.compile(r"TYPE:\s*(\w+)\s*\n\s*TASK:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _parse_module_from_text(text: str) -> tuple[str, str] | None:
    """Try strict then loose MODULE path CODE block parsing. Returns (rel_path, code) or None."""
    m = MODULE_CODE_RE.search(text)
    if m:
        return m.group(1).strip(), clean_output(m.group(2))
    m = MODULE_CODE_RE_LOOSE.search(text)
    if m:
        return m.group(1).strip(), clean_output(m.group(2))
    return None
DESIGN_PROJECT_RE = re.compile(r"DESIGN_PROJECT:\s*(\{.*?\})", re.IGNORECASE | re.DOTALL)
MODEL_SUGGEST_RE = re.compile(r"MODEL:\s*([a-zA-Z0-9_.\-:]+)", re.IGNORECASE)
# Detect port in server/website code for console hint
PORT_RE = re.compile(r"(?:port|PORT)\s*=\s*(\d{2,5})|\.bind\s*\(\s*[^)]*,\s*(\d{2,5})\s*\)|:(\d{2,5})\s*(?:\)|\s|$)|listen\s*\(\s*(\d{2,5})\s*\)|\.run\s*\(\s*port\s*=\s*(\d{2,5})")


def _detect_port_from_code(code: str) -> int | None:
    """Try to detect server port from code. Returns first plausible port (e.g. 8080, 5000) or None."""
    for m in PORT_RE.finditer(code):
        for g in m.groups():
            if g:
                p = int(g)
                if 1024 <= p <= 65535:
                    return p
    return None


def _sanitize_module_path(rel_path: str) -> str | None:
    """Allow path like core/run_analytics.py or plugins/plugin.py. No .., no backslash, no absolute."""
    s = rel_path.strip().replace("\\", "/")
    if ".." in s or s.startswith("/") or not s.endswith(".py"):
        return None
    if not all(c.isalnum() or c in "/_." for c in s):
        return None
    return s

def _parse_think_and_code(raw: str) -> tuple[str, str]:
    """Split raw response into reasoning and code. If no CODE: marker, treat whole as code."""
    raw = raw.strip()
    if "CODE:" in raw.upper():
        parts = REASONING_CODE_SEP.split(raw, maxsplit=1)
        reasoning = (parts[0].strip() if parts[0] else "").replace("REASONING:", "").strip()
        code = parts[1].strip() if len(parts) > 1 else raw
    else:
        reasoning = ""
        code = raw
    return reasoning, code


def _console_ask(prompt: str) -> None:
    """Print exactly what is being sent to the LLM (you see what is asked)."""
    if getattr(config, "PRINT_ASK_RESPONSE_CODE", True):
        sys.stdout.flush()
        sys.stderr.flush()
        print("\n" + "=" * 60, flush=True)
        print("=== ASK (what is sent to the LLM)", flush=True)
        print("=" * 60, flush=True)
        print(prompt, flush=True)
        print("=" * 60 + "\n", flush=True)
        sys.stdout.flush()


def _console_response(raw: str) -> None:
    """Print exactly what the LLM returned (you see everything out from LLM)."""
    if getattr(config, "PRINT_ASK_RESPONSE_CODE", True):
        sys.stdout.flush()
        print("\n" + "=" * 60, flush=True)
        print("=== RESPONSE (full output from LLM)", flush=True)
        print("=" * 60, flush=True)
        print(raw, flush=True)
        print("=" * 60 + "\n", flush=True)
        sys.stdout.flush()


def _console_code(code: str) -> None:
    """Print the code produced by the LLM (extracted from response)."""
    if getattr(config, "PRINT_ASK_RESPONSE_CODE", True) and code:
        sys.stdout.flush()
        print("\n" + "=" * 60, flush=True)
        print("=== CODE (code produced by LLM)", flush=True)
        print("=" * 60, flush=True)
        print(code, flush=True)
        print("=" * 60 + "\n", flush=True)
        sys.stdout.flush()


def _log(msg: str) -> None:
    """Log to engine.log + console."""
    engine_log.log_info(msg)


def _build_user_prompt(bot_id: int, task_prompt: str, task_type: str) -> str:
    # Core/kernel agents use a data-driven prompt assembled from DB state.
    bot = db.get_bot(bot_id=bot_id)
    domain = (bot.get("domain") or "").strip() if bot else ""
    if domain == "system":
        from core_prompt import build_core_prompt

        return build_core_prompt(bot_id, task_prompt, task_type)

    # Project / non-core bots use the existing history + task-type-specific instructions.
    messages = db.get_messages_for_bot(bot_id, limit=config.MAX_CONTEXT_MESSAGES)
    best = db.get_best_run(bot_id)
    parts: list[str] = []
    try:
        from level import get_level

        lev = get_level()
        parts.append(
            f"Level: {lev.get('level', 1)} | Creations so far: {lev.get('creations_count', 0)} | Goal: {lev.get('goal', '')}"
        )
        parts.append("")
    except Exception:
        pass
    parts.append(task_prompt)
    parts.append("\n" + prompts.get_prompt("instruction_follow", "runtime"))
    if best:
        parts.append(f"\nPrevious best score: {best.get('score')}. Use as reference.")
    # Include last reflection if any
    import json

    for m in reversed(messages):
        meta = m.get("meta_json")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if isinstance(meta, dict) and meta.get("phase") == "reflection":
            parts.append(f"\nYour last reflection: {m.get('content', '')[:500]}")
            break
    suffix_key = f"output_suffix_{task_type}" if task_type in ("code", "server", "website", "tool", "simulation", "graphics", "video", "test", "deploy", "quantum_circuit") else "output_suffix_default"
    parts.append(prompts.get_prompt(suffix_key, "runtime"))
    return "\n".join(parts)


def _generate(
    model_name: str,
    prompt: str,
    *,
    purpose: str = "TASK",
    stream: bool = False,
    temperature: float = 0.7,
    api_base: str | None = None,
    bot: dict | None = None,
    task_type: str | None = None,
) -> str:
    """Call LLM; use model pool + retry on timeout when MODEL_RETRY_ON_TIMEOUT and bot/task_type available."""
    retry_on = getattr(config, "MODEL_RETRY_ON_TIMEOUT", True)
    if retry_on and bot is not None:
        try:
            from model_selector import get_model_pool_for_retry

            pool = get_model_pool_for_retry(bot, task_type, purpose=purpose, exclude=None)
            if pool:
                return _ollama_fallback(
                    pool,
                    prompt,
                    stream=stream,
                    temperature=temperature,
                )
        except Exception:
            pass
    return ollama_generate(
        model_name,
        prompt,
        stream=stream,
        temperature=temperature,
        api_base=api_base,
    )


def _bot_model(
    bot: dict,
    task_type: str | None = None,
    purpose: str | None = None,
) -> tuple[str | None, str]:
    """Resolve bot's model via model_selector (per_purpose -> per_task_type -> default)."""
    try:
        from model_selector import get_model_for_task
        return get_model_for_task(bot, task_type, purpose)
    except Exception:
        return get_model_endpoint(bot.get("model") or config.DEFAULT_MODEL)


def _run_one(bot: dict, user_prompt: str, iteration: int, task_type: str, _log_bot=None) -> tuple[dict, str, str, bool]:
    """Generate (think+code), validate, run. Returns (result_dict, code, reasoning, ran_in_sandbox)."""
    if _log_bot is None:
        _log_bot = _log
    api_base, model_name = _bot_model(bot, task_type, purpose='code')
    bot_name = bot["name"]
    ensure_model(model_name)
    full_prompt = f"{bot.get('system_prompt') or ''}\n\n{user_prompt}"
    _console_ask(full_prompt)
    stream = config.STREAM_LLM
    raw = _generate(model_name, full_prompt, purpose='code', stream=stream, temperature=0.7, api_base=api_base, bot=bot, task_type=task_type)
    _console_response(raw)
    db.insert_message(bot["id"], "user", user_prompt)

    reasoning, code_raw = _parse_think_and_code(raw)
    if reasoning:
        db.insert_message(bot["id"], "assistant", reasoning, meta_json={"phase": "reasoning", "streaming": stream})
    db.insert_message(bot["id"], "assistant", code_raw, meta_json={"phase": "code", "streaming": stream})

    gen_dir = Path(getattr(config, "GENERATED_MODULES_DIR", "generated"))

    def _write_and_compile_module(rel_path: str, code: str) -> tuple[bool, str]:
        """
        Write module under generated/ and compile it. Return (ok, message).

        For core/engine modules, this also runs core_tests.run_core_tests()
        to gate application of upgrades on a quick self-test pass.
        """
        safe = _sanitize_module_path(rel_path)
        if not safe or not is_valid_python(code):
            return False, "Invalid path or invalid Python"
        out_path = gen_dir / safe
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(code, encoding="utf-8")
        try:
            py_compile.compile(str(out_path), doraise=True)
        except py_compile.PyCompileError as e:
            return False, f"Compile error: {e}"
        # Run core self-tests when writing engine modules. If tests fail, revert this module.
        try:
            from core_tests import run_core_tests

            ok, msg = run_core_tests()
            if not ok:
                try:
                    out_path.unlink()
                except OSError:
                    pass
                return False, f"Core tests failed: {msg}"
        except Exception as e:
            # If self-tests themselves error, treat as failure and avoid enabling this module.
            try:
                out_path.unlink()
            except OSError:
                pass
            return False, f"Core tests error: {e}"
        return True, str(out_path)

    def _write_module_with_repair(rel_path: str, code_from_mod: str) -> tuple[bool, str, str]:
        """Write module, retry with repair prompts on compile failure. Returns (ok, msg, final_code)."""
        _console_code(code_from_mod)
        ok, msg = _write_and_compile_module(rel_path, code_from_mod)
        max_repair = getattr(config, "MODULE_COMPILE_REPAIR_ATTEMPTS", 2)
        attempt = 0
        current_code = code_from_mod
        current_path = rel_path
        while not ok and attempt < max_repair:
            attempt += 1
            _log_bot(f"[EVOLVE] Compile failed for {current_path}, asking model for fix (attempt {attempt}/{max_repair})...")
            repair_prompt = prompts.get_prompt("module_compile_repair", "runtime", path=current_path, error=msg, code=current_code[:3000])
            _console_ask("[Compile repair] " + repair_prompt[:500] + "...")
            api_base_repair, model_repair = _bot_model(bot, task_type, purpose='repair')
            raw_repair = _generate(model_repair, repair_prompt, purpose='repair', stream=config.STREAM_LLM, temperature=0.3, api_base=api_base_repair, bot=bot, task_type=task_type)
            _console_response(raw_repair)
            mod2 = _parse_module_from_text(raw_repair)
            if mod2:
                current_path, current_code = mod2[0], mod2[1]
            else:
                current_code = clean_output(raw_repair)
            _console_code(current_code)
            ok, msg = _write_and_compile_module(current_path, current_code)
        return ok, msg, current_code

    # self_improve: no execution, parse PROPOSAL or MODULE (write module if present); MODEL: to switch LLM
    if task_type == "self_improve":
        m = PROPOSAL_RE.search(raw)
        proposal_text = m.group(1).strip() if m else raw[:2000]
        mod_parsed = _parse_module_from_text(raw)
        out = {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False, "duration": 0, "proposal": proposal_text}
        model_sug = MODEL_SUGGEST_RE.search(raw)
        if model_sug:
            out["suggested_model"] = model_sug.group(1).strip()
        if mod_parsed:
            rel_path, code_from_mod = mod_parsed[0], mod_parsed[1]
            ok, msg, _ = _write_module_with_repair(rel_path, code_from_mod)
            if ok:
                out["stdout"], out["written_module"] = f"Wrote {msg}", msg
                return out, "", reasoning, False
            out["stderr"] = msg or "Compile failed after repair attempts"
        return out, "", reasoning, False

    # upgrade_engine: parse MODULE: path CODE: ... and write to generated/ (organized structure); MODEL: to switch LLM
    if task_type == "upgrade_engine":
        out = {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False, "duration": 0}
        model_sug = MODEL_SUGGEST_RE.search(raw)
        if model_sug:
            out["suggested_model"] = model_sug.group(1).strip()
        mod_parsed = _parse_module_from_text(raw)
        if mod_parsed:
            rel_path, code_from_mod = mod_parsed[0], mod_parsed[1]
            ok, msg, code_from_mod = _write_module_with_repair(rel_path, code_from_mod)
            if ok:
                out["stdout"], out["written_module"] = f"Wrote {msg}", msg
                return out, code_from_mod, reasoning, False
            out["returncode"] = -1
            out["stderr"] = msg or "Invalid Python in MODULE block after repair attempts"
            return out, code_from_mod, reasoning, False
        m = PROPOSAL_RE.search(raw)
        out["proposal"] = m.group(1).strip() if m else raw[:2000]
        return out, "", reasoning, False

    code = clean_output(code_raw)
    _console_code(code)
    if not is_valid_python(code):
        parse_err = get_python_parse_error(code) or "syntax error"
        stderr_msg = f"Invalid Python: {parse_err}"
        # One syntax-repair attempt for runnable task types
        if task_type in ("code", "server", "website", "tool", "test", "deploy") and getattr(config, "REPAIR_ATTEMPTS", 0):
            repair_prompt = prompts.get_prompt("syntax_repair", "runtime", parse_err=parse_err, code=code[:2500])
            _console_ask("[Syntax repair] " + repair_prompt[:400] + "...")
            api_base, model_name = _bot_model(bot, task_type, purpose='repair')
            raw_repair = _generate(model_name, repair_prompt, purpose='repair', stream=config.STREAM_LLM, temperature=0.3, api_base=api_base, bot=bot, task_type=task_type)
            _console_response(raw_repair)
            code2 = clean_output(raw_repair)
            _console_code(code2)
            if is_valid_python(code2):
                code = enforce_contract(code2, task_type)
            else:
                return {"returncode": -1, "stdout": "", "stderr": stderr_msg, "timed_out": False, "duration": 0}, code, reasoning, False
        else:
            return {"returncode": -1, "stdout": "", "stderr": stderr_msg, "timed_out": False, "duration": 0}, code, reasoning, False
    code = enforce_contract(code, task_type)

    py_exe = ensure_bot_venv(bot_name)
    workspace = get_project_workspace(bot_name)
    run_mode = "server" if task_type == "server" else "normal"
    timeout_s = config.TIMEOUT_S
    if task_type == "server":
        timeout_s = getattr(config, "SERVER_TIMEOUT_S", 30)
    elif task_type == "simulation":
        timeout_s = getattr(config, "SIMULATION_TIMEOUT_S", 60)
    result = run_candidate(
        code,
        workspace,
        py_exe,
        timeout_s=timeout_s,
        stream_stdout=config.STREAM_PROGRAM_OUTPUT,
        run_mode=run_mode,
    )
    result["duration"] = result.get("duration") or 0
    result["returncode"] = result.get("returncode", -1)
    result["timed_out"] = result.get("timed_out", False)
    return result, code, reasoning, True


def _reflect(bot: dict, result: dict, score: float, valid: bool, _log_fn: None = None) -> str:
    """Call LLM for reflection; store in messages; return reflection text."""
    (_log_fn or _log)("[BACKGROUND] Reflecting on run...")
    summary = f"returncode={result.get('returncode')}, timed_out={result.get('timed_out')}, score={score}, valid={valid}\nstdout: {(result.get('stdout') or '')[:300]}\nstderr: {(result.get('stderr') or '')[:300]}"
    prompt = prompts.get_prompt("reflection_prompt", "runtime", summary=summary)
    _console_ask("[Reflection] " + prompt[:500] + "..." if len(prompt) > 500 else "[Reflection] " + prompt)
    api_base, model_name = _bot_model(bot, "reflection", purpose='reflect')
    ref = _generate(model_name, prompt, purpose='reflect', stream=False, temperature=0.7, api_base=api_base, bot=bot, task_type="reflection")
    _console_response("[Reflection] " + ref.strip())
    db.insert_message(bot["id"], "assistant", ref.strip(), meta_json={"phase": "reflection"})
    return ref.strip()


def _propose_next_task(bot: dict, _log_fn: None = None) -> None:
    """Ask LLM to propose next task; insert into tasks."""
    log = _log_fn or _log
    api_base, model_name = _bot_model(bot, "proposal", purpose='proposal')
    # DevOS: limit proposed TYPEs based on bot domain (core vs project).
    domain = (bot.get("domain") or "").strip()
    if domain == "system":
        allowed_types = "self_improve|upgrade_engine|optimize_runtime|security_patch|performance_tune|proposal_review|simulation|design_project"
        examples = prompts.get_prompt("task_proposal_examples_core", "runtime")
    else:
        allowed_types = "code|server|website|tool|simulation|graphics|video|test|deploy"
        examples = prompts.get_prompt("task_proposal_examples_project", "runtime")
    prompt = prompts.get_prompt("task_proposal_core" if domain == "system" else "task_proposal_project", "runtime", allowed_types=allowed_types, examples=examples)
    _console_ask("[Task proposal] " + prompt)
    raw = _generate(model_name, prompt, purpose='proposal', stream=False, temperature=0.8, api_base=api_base, bot=bot, task_type="proposal")
    _console_response("[Task proposal] " + raw)
    m = TASK_PROPOSAL_RE.search(raw)
    if m:
        ttype = m.group(1).strip().lower()
        task_desc = m.group(2).strip().split("\n")[0]
        # Fallback / safety: if model proposes an unsupported TYPE, coerce to a safe default.
        if domain == "system":
            if ttype not in config.CORE_TASK_TYPES:
                ttype = "self_improve"
        else:
            if ttype not in config.PROJECT_TASK_TYPES:
                ttype = "code"
        db.insert_task(bot["id"], task_desc, priority=0, task_type=ttype)
        log(f"[BACKGROUND] New task proposed: {ttype} | {task_desc[:60]}...")


def _collect_website_artifacts(workspace, run_id: int) -> None:
    try:
        for p in workspace.glob("*.html"):
            db.insert_artifact(run_id, "website", str(p))
        for p in workspace.glob("*.css"):
            db.insert_artifact(run_id, "website", str(p))
    except Exception:
        pass


def run_task(task_id: int, bot_id: int, prompt: str, task_type: str = "code") -> None:
    globals()['_NIFFI_BOT_ID'] = bot_id
    globals()['_NIFFI_TASK_ID'] = task_id

    """Execute one task: think→code, run, score, reflect, persist; optionally propose next task."""
    bot = db.get_bot(bot_id=bot_id)
    if not bot:
        db.set_task_state(task_id, "failed")
        return
    bot_name = bot.get("name") or f"bot_{bot_id}"
    def _log_bot(msg: str) -> None:
        _log(f"[{bot_name}] {msg}")

    db.set_bot_status(bot_id, "running")
    iteration = db.get_last_iteration(bot_id) + 1
    user_prompt = _build_user_prompt(bot_id, prompt, task_type)

    result, code, reasoning, ran = _run_one(bot, user_prompt, iteration, task_type, _log_bot)

    # self_improve: store proposal, optionally write module; apply MODEL: to switch LLM
    if task_type == "self_improve":
        if result.get("proposal"):
            db.insert_proposal(bot_id, result["proposal"])
        if result.get("suggested_model"):
            db.update_bot_model(bot_id, result["suggested_model"])
            ensure_model(result["suggested_model"])
            _log_bot(f"[BACKGROUND] Bot {bot_id} model switched to {result['suggested_model']}; next runs use this LLM.")
        if result.get("written_module"):
            _log_bot(f"[SELF-IMPROVE] Added {result['written_module']} (compiled). Engine will load it next cycle.")
            if (bot.get("domain") or "").strip() == "system":
                _log(f"[KERNEL] Improved: module {result['written_module']}")
        _log_bot(f"[BACKGROUND] self_improve done | task {task_id} | proposal stored")
        db.set_task_state(task_id, "done")
        db.set_bot_status(bot_id, "idle")
        run_id = db.insert_run(
            bot_id=bot_id, task_id=task_id, iteration=iteration,
            sandbox_path="", code_path="", stdout=result.get("stdout", ""), stderr="", returncode=0, duration_ms=0,
            score=1.0, status="ok", meta_json={"task_type": "self_improve", "proposal": (result.get("proposal") or "")[:500], "written_module": result.get("written_module")},
        )
        _reflect(bot, result, 1.0, True, _log_bot)
        core_priority = getattr(config, "CORE_TASK_PRIORITY", 3)
        db.insert_task(bot_id, prompts.get_prompt("self_improve_followup", "runtime"), priority=core_priority, task_type="self_improve")
        _concrete = [
            prompts.get_prompt("self_improve_concrete_1", "runtime"),
            prompts.get_prompt("self_improve_concrete_2", "runtime"),
            prompts.get_prompt("self_improve_concrete_3", "runtime"),
        ]
        concrete = _concrete[iteration % len(_concrete)]
        db.insert_task(bot_id, concrete, priority=core_priority, task_type="self_improve")
        _propose_next_task(bot, _log_bot)
        return

    # optimize_runtime: core-driven runtime/model policy optimization.
    if task_type == "optimize_runtime":
        try:
            # Let model_policy parse and apply any MODEL_POLICY_UPDATE blocks from reasoning+code.
            from model_policy import apply_model_policy_updates_from_text

            combined = (reasoning or "") + "\n" + (code or "")
            apply_model_policy_updates_from_text(combined)
            _log_bot(f"[BACKGROUND] optimize_runtime applied any MODEL_POLICY_UPDATE blocks for task {task_id}.")
        except Exception as e:
            _log_bot(f"[BACKGROUND] optimize_runtime governance error for task {task_id}: {e}")
        # Fall through to generic scoring / run logging (even if no code was executed).

    # design_project: core designs a new project but does not implement it.
    if task_type == "design_project":
        spec = None
        try:
            combined = (reasoning or "") + "\n" + (code or "")
            m = DESIGN_PROJECT_RE.search(combined)
            if m:
                import json as _json

                spec = _json.loads(m.group(1))
        except Exception as e:
            _log_bot(f"[BACKGROUND] design_project parsing error for task {task_id}: {e}")

        if spec:
            # Store in upgrade_backlog so governance / project manager can act on it.
            title = spec.get("project_name") or spec.get("name") or f"project_for_task_{task_id}"
            goal = spec.get("goal") or ""
            try:
                import json as _json

                proposal_text = _json.dumps(spec, indent=2)
            except Exception:
                proposal_text = str(spec)
            try:
                upg_id = db.insert_upgrade_backlog(
                    title=title,
                    problem=goal,
                    proposal_text=proposal_text,
                    priority=0,
                    status="new",
                )
                _log_bot(f"[BACKGROUND] design_project recorded in upgrade_backlog id={upg_id} title={title!r}.")
            except Exception as e:
                _log_bot(f"[BACKGROUND] design_project failed to write upgrade_backlog: {e}")
        else:
            # No structured DESIGN_PROJECT block; store raw reasoning as a low-priority upgrade idea.
            try:
                snippet = (reasoning or code or prompt or "")[:1000]
                upg_id = db.insert_upgrade_backlog(
                    title=f"project_design_task_{task_id}",
                    problem="",
                    proposal_text=snippet,
                    priority=-1,
                    status="new",
                )
                _log_bot(f"[BACKGROUND] design_project (unstructured) recorded as upgrade_backlog id={upg_id}.")
            except Exception as e:
                _log_bot(f"[BACKGROUND] design_project failed to store unstructured proposal: {e}")

        # Mark task and bot as finished; record a run entry.
        db.set_task_state(task_id, "done")
        db.set_bot_status(bot_id, "idle")
        run_id = db.insert_run(
            bot_id=bot_id,
            task_id=task_id,
            iteration=iteration,
            sandbox_path="",
            code_path="",
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            returncode=0,
            duration_ms=0,
            score=1.0,
            status="ok",
            meta_json={"task_type": "design_project"},
        )
        # No reflection or auto-next-task by default; governance loop can schedule follow-ups explicitly.
        return

    # upgrade_engine: write module to generated/, no sandbox run; apply MODEL: to switch LLM
    if task_type == "upgrade_engine":
        if result.get("suggested_model"):
            db.update_bot_model(bot_id, result["suggested_model"])
            ensure_model(result["suggested_model"])
            _log_bot(f"[BACKGROUND] Bot {bot_id} model switched to {result['suggested_model']}; next runs use this LLM.")
        if result.get("written_module"):
            _log_bot(f"[SELF-IMPROVE] Added {result['written_module']} (compiled). Engine will load it next cycle.")
            if (bot.get("domain") or "").strip() == "system":
                _log(f"[KERNEL] Improved: module {result['written_module']}")
        score = 1.0 if result.get("returncode") == 0 else 0.0
        valid = result.get("returncode") == 0
        _log_bot(f"[BACKGROUND] upgrade_engine done | task {task_id} | valid={valid} | written={result.get('written_module', '')}")
        db.set_task_state(task_id, "done" if valid else "failed")
        db.set_bot_status(bot_id, "idle")
        run_id = db.insert_run(
            bot_id=bot_id, task_id=task_id, iteration=iteration,
            sandbox_path="", code_path="", stdout=result.get("stdout", ""), stderr=result.get("stderr", ""),
            returncode=result.get("returncode", 0), duration_ms=0, score=score, status="ok" if valid else "crash",
            meta_json={"task_type": "upgrade_engine", "written_module": result.get("written_module")},
        )
        if result.get("proposal"):
            db.insert_proposal(bot_id, result["proposal"])
        _reflect(bot, result, score, valid, _log_bot)
        core_priority = getattr(config, "CORE_TASK_PRIORITY", 3)
        db.insert_task(bot_id, prompts.get_prompt("upgrade_engine_followup", "runtime"), priority=core_priority, task_type="upgrade_engine")
        _concrete = [
            prompts.get_prompt("upgrade_concrete_1", "runtime"),
            prompts.get_prompt("upgrade_concrete_2", "runtime"),
        ]
        concrete = _concrete[iteration % len(_concrete)]
        db.insert_task(bot_id, concrete, priority=core_priority, task_type="upgrade_engine")
        _propose_next_task(bot, _log_bot)
        return

    score, valid = compute_score(result, task_type)
    duration_ms = int((result.get("duration") or 0) * 1000)
    status = "ok" if valid and not result.get("timed_out") and result.get("returncode") == 0 else ("timeout" if result.get("timed_out") else "crash")

    if not valid and config.REPAIR_ATTEMPTS and task_type in ("code", "test", "deploy"):
        repair_prompt = prompts.get_prompt("repair_prompt", "runtime", feedback=result.get("stderr", "")[:800], code=code[:2000])
        _console_ask("[Repair] " + repair_prompt[:800] + "..." if len(repair_prompt) > 800 else "[Repair] " + repair_prompt)
        api_base, model_name = _bot_model(bot, task_type, purpose='repair')
        raw2 = _generate(model_name, repair_prompt, purpose='repair', stream=config.STREAM_LLM, temperature=0.3, api_base=api_base, bot=bot, task_type=task_type)
        _console_response(raw2)
        code2 = enforce_contract(clean_output(raw2), "code")
        _console_code(code2)
        if is_valid_python(code2):
            result2 = run_candidate(
                code2, get_project_workspace(bot["name"]), ensure_bot_venv(bot["name"]),
                timeout_s=config.TIMEOUT_S, stream_stdout=config.STREAM_PROGRAM_OUTPUT,
            )
            score2, valid2 = compute_score(result2, "code")
            if valid2:
                result, code, status = result2, code2, "repaired"
                score, valid, duration_ms = score2, True, int((result2.get("duration") or 0) * 1000)
                ran = True

    workspace = get_project_workspace(bot["name"]) if ran else None
    code_path = str(workspace / "candidate.py") if workspace else ""
    sandbox_path = str(workspace) if workspace else ""
    run_id = db.insert_run(
        bot_id=bot_id, task_id=task_id, iteration=iteration,
        sandbox_path=sandbox_path, code_path=code_path,
        stdout=(result.get("stdout") or "")[:config.MAX_STDOUT_CHARS],
        stderr=(result.get("stderr") or "")[:config.MAX_STDERR_CHARS],
        returncode=result.get("returncode", -1), duration_ms=duration_ms,
        score=score if valid else None, status=status,
        meta_json={"task_type": task_type},
    )
    if valid and score is not None:
        try:
            from tools import collect_training_example
            response_text = (reasoning + "\nCODE:\n" + code) if reasoning else code
            collect_training_example(user_prompt, response_text[:50000], score, run_id, bot_id)
        except Exception:
            pass
    if result.get("log_path"):
        db.insert_artifact(run_id, "log", result["log_path"])
    if task_type == "website" and workspace:
        _collect_website_artifacts(workspace, run_id)

    _reflect(bot, result, score, valid, _log_bot)

    if valid:
        _log_bot(f"[BACKGROUND] Task {task_id} SUCCESS | type={task_type} score={score}")
        db.set_task_state(task_id, "done")
        db.set_bot_status(bot_id, "idle")
        if task_type in ("code", "server", "website", "tool", "simulation", "graphics", "video", "test", "deploy") and workspace:
            try:
                from creations import register_creation, growth_log
                from level import increment_creations
                cid = register_creation(
                    run_id=run_id,
                    bot_id=bot_id,
                    task_type=task_type,
                    workspace_path=str(workspace),
                    code_path=code_path,
                    title=prompt[:80] if prompt else task_type,
                )
                if cid is not None:
                    n = increment_creations()
                    growth_log(f"[GROW] Creations count: {n} | New: {task_type} id={cid}")
                    port = _detect_port_from_code(code) if task_type in ("server", "website") else None
                    _log_bot(f"[CREATION] id={cid} type={task_type} | path=creations/{cid} | To run: python showcase.py run {cid}")
                    if port:
                        _log_bot(f"[CREATION] Endpoint: http://localhost:{port} (start server with showcase run {cid} then open in browser)")
                    _log_bot(f"[BACKGROUND] CREATION registered | id={cid} type={task_type} | total creations={n}")
            except Exception as e:
                pass
        if task_type == "code":
            db.insert_task(bot_id, prompts.get_prompt("code_success_followup", "runtime", score=score), priority=0, task_type="code")
            if iteration > 0 and iteration % getattr(config, "TASK_PROPOSAL_EVERY_N_RUNS", 3) == 0:
                _propose_next_task(bot, _log_bot)
    else:
        _log_bot(f"[BACKGROUND] Task {task_id} FAILED | type={task_type} status={status}")
        db.set_task_state(task_id, "failed")
        db.set_bot_status(bot_id, "idle")

        # Automatic repair loop for PROJECT bots: enqueue an explicit "repair last failure" task.
        # Core/system bots rely on self_improve/upgrade_engine instead.
        domain = (bot.get("domain") or "").strip()
        if domain != "system":
            err_snip = (result.get("stderr") or "")[:800]
            out_snip = (result.get("stdout") or "")[:400]
            repair_instruction = prompts.get_prompt(
                "repair_after_failure", "runtime",
                prompt=(prompt or "")[:400], err_snip=err_snip, out_snip=out_snip,
            )
            # Use the same task_type when possible; otherwise fall back to 'code'.
            next_type = task_type if task_type in getattr(config, "PROJECT_TASK_TYPES", ()) else "code"
            try:
                db.insert_task(bot_id, repair_instruction, priority=0, task_type=next_type)
                _log_bot(f"[BACKGROUND] Queued repair task after failure {task_id} | type={next_type}")
            except Exception as e:
                _log_bot(f"[BACKGROUND] Failed to queue repair task for {task_id}: {e}")

        # Ask the agent to propose a next step (may be another repair or a different direction).
        _propose_next_task(bot, _log_bot)