"""
Model selector: single entry point for model choice.
Combines policy, discovery, health, fallbacks. Project and core agents use this.
"""

from __future__ import annotations

import config
import db

try:
    from model_registry import get_model_endpoint
except ImportError:
    def get_model_endpoint(name: str) -> tuple[str | None, str]:
        return (None, name or (config.DEFAULT_MODEL or "qwen2.5-coder:3b"))


# Task types that benefit from code-specialized models (*coder*, *code*, deepseek-coder, etc.)
_CODE_HEAVY_TASK_TYPES = frozenset({
    "code", "server", "website", "tool", "self_improve", "upgrade_engine",
    "optimize_runtime", "security_patch", "performance_tune", "test",
})
_CODE_PURPOSES = frozenset({"code", "repair"})


def _prefer_code_models(
    candidates: list[str],
    task_type: str | None,
    purpose: str | None = None,
) -> list[str]:
    """Reorder candidates: prefer *coder*/*code* models for code-heavy tasks or purposes."""
    code_heavy = (task_type and task_type in _CODE_HEAVY_TASK_TYPES) or (purpose and purpose in _CODE_PURPOSES)
    if not code_heavy:
        return candidates
    code_like = [c for c in candidates if "coder" in c.lower() or "code" in c.lower() or "deepseek" in c.lower()]
    rest = [c for c in candidates if c not in code_like]
    return code_like + rest if code_like else candidates


def get_model_for_task(
    bot: dict,
    task_type: str | None = None,
    purpose: str | None = None,
) -> tuple[str | None, str]:
    """
    Resolve (api_base, model_name) for (bot, task_type, purpose).
    Uses policy (per_purpose -> per_task_type -> default), discovery, health, fallbacks.
    """
    try:
        from model_policy import select_model_for_task
        api_base, logical = select_model_for_task(bot, task_type, purpose)
    except Exception:
        logical = bot.get("model") or config.DEFAULT_MODEL
        api_base, logical = get_model_endpoint(logical)

    # For external endpoints, return as-is (no discovery)
    if api_base:
        return (api_base, logical)

    # Local Ollama: ensure we pick an available model
    try:
        from generated.core import model_discovery
        discovered = model_discovery.get_discovered_models()
    except Exception:
        discovered = []

    domain = (bot.get("domain") or "").strip()
    scope = "core" if domain == "system" else "project"
    fallbacks = list((getattr(config, "MODEL_FALLBACKS", {}) or {}).get(scope, []))
    max_pool = getattr(config, "DISCOVERY_MAX_POOL", 15)

    # Build candidate pool: preferred + fallbacks + discovered (deduped, limited)
    seen = set()
    candidates = []
    for m in [logical] + fallbacks:
        if m and m not in seen:
            seen.add(m)
            candidates.append(m)
    for m in (discovered or [])[:max_pool]:
        if m and m not in seen:
            seen.add(m)
            candidates.append(m)
    candidates = candidates[:max_pool]
    candidates = _prefer_code_models(candidates, task_type, purpose)

    # Filter to available (in discovered) if we have discovery; otherwise trust config
    available = set(discovered) if discovered else set(candidates)
    if available:
        # Prefer models we know exist; if logical not available, use first available
        if logical not in available:
            logical = next((c for c in candidates if c in available), candidates[0] if candidates else logical)
        candidates = [c for c in candidates if c in available] or candidates

    # Health-aware: pick best from candidates
    try:
        from generated.core import model_metrics
        chosen = model_metrics.choose_model_for_bot(logical, [c for c in candidates if c != logical])
        if chosen and chosen in (candidates or [chosen]):
            return (api_base, chosen)
    except Exception:
        pass

    # Final fallback: if still no model and discovered is empty, try one more fetch
    if not discovered:
        try:
            from generated.core import model_discovery
            discovered = model_discovery.fetch_ollama_models()
            if discovered:
                return (api_base, discovered[0])
        except Exception:
            pass

    return (api_base, logical)


def get_model_pool_for_retry(
    bot: dict,
    task_type: str | None,
    purpose: str | None = None,
    exclude: str | None = None,
) -> list[tuple[str | None, str]]:
    """Return ordered list of (api_base, model_name) for retry chain. Excludes failed model."""
    _, first = get_model_for_task(bot, task_type, purpose)
    try:
        from model_policy import select_model_for_task
        _, logical = select_model_for_task(bot, task_type, purpose)
    except Exception:
        logical = bot.get("model") or config.DEFAULT_MODEL
    domain = (bot.get("domain") or "").strip()
    scope = "core" if domain == "system" else "project"
    fallbacks = list((getattr(config, "MODEL_FALLBACKS", {}) or {}).get(scope, []))
    try:
        from generated.core import model_discovery
        discovered = model_discovery.get_discovered_models()
    except Exception:
        discovered = []
    seen = set()
    pool = []
    for m in [logical] + fallbacks + (discovered or []):
        if m and m not in seen and m != exclude:
            seen.add(m)
            pool.append((get_model_endpoint(m)[0], m))
    # Prefer code-specialized models for code-heavy tasks
    model_names = [p[1] for p in pool]
    reordered = _prefer_code_models(model_names, task_type, purpose)
    if reordered != model_names:
        pool = [(get_model_endpoint(m)[0], m) for m in reordered]
    return pool[: getattr(config, "MODEL_RETRY_COUNT", 3) + 1]
