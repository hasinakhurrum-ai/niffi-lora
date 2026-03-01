"""Model routing policy for Niffi DevOS.

select_model_for_task(bot, task_type) chooses a concrete model name (and api_base)
based on:
- bot.domain (core vs project)
- task_type (self_improve, upgrade_engine, code, server, ...)
- DB-stored JSON policy in model_policies
- fallback to bot['model'] or config.DEFAULT_MODEL
"""

from __future__ import annotations

from typing import Any, Tuple

import json

import config
import db

try:
    from model_registry import get_model_endpoint
except ImportError:
    def get_model_endpoint(name: str) -> Tuple[str | None, str]:
        return (None, name or (config.DEFAULT_MODEL or "qwen2.5-coder:3b"))


# Valid purposes for per_purpose routing (planning, code, repair, reflect, proposal).
PURPOSES = frozenset({"planning", "code", "repair", "reflect", "proposal"})


def select_model_for_task(
    bot: dict,
    task_type: str | None,
    purpose: str | None = None,
) -> tuple[str | None, str]:
    """
    Decide which concrete model to use for a (bot, task_type, purpose).

    Resolution order: per_purpose[purpose] -> per_task_type[task_type] -> default_model.

    Policy JSON shape (per scope):
    {
      "default_model": "qwen2.5-coder:3b",
      "per_task_type": {
        "code": "qwen2.5-coder:7b",
        "deploy": "llama3:8b",
        "self_improve": "qwen2.5:14b",
        ...
      },
      "per_purpose": {
        "planning": "qwen2.5:14b",
        "code": "qwen2.5-coder:7b",
        "repair": "qwen2.5-coder:7b",
        "reflect": "llama3:8b",
        "proposal": "llama3:8b"
      }
    }

    Scope is "core" when bot.domain == "system", else "project".
    """
    domain = (bot.get("domain") or "").strip()
    scope = "core" if domain == "system" else "project"
    policy = db.get_model_policy(scope) or {}
    per_task = policy.get("per_task_type") or {}
    per_purpose = policy.get("per_purpose") or {}
    default_model = policy.get("default_model") or (bot.get("model") or config.DEFAULT_MODEL)

    logical_name = None
    if purpose and purpose in PURPOSES:
        logical_name = per_purpose.get(purpose)
    if not logical_name and task_type:
        logical_name = per_task.get(task_type)
    if not logical_name:
        logical_name = default_model

    return get_model_endpoint(logical_name.strip())


def apply_model_policy_updates_from_text(text: str) -> None:
    """
    Scan LLM output text for MODEL_POLICY_UPDATE blocks and apply them.

    Expected block format:

    MODEL_POLICY_UPDATE:
    {
      "scope": "core" | "project",
      "default_model": "model_name",
      "per_task_type": { "self_improve": "llama3:70b", "test": "qwen2.5-coder:7b", ... },
      "per_purpose": { "planning": "qwen2.5:14b", "repair": "qwen2.5-coder:7b", ... }
    }
    """
    import re

    pattern = re.compile(
        r"MODEL_POLICY_UPDATE:\s*(\{.*?\})",
        re.IGNORECASE | re.DOTALL,
    )
    matches = pattern.findall(text or "")
    for block in matches:
        try:
            policy = json.loads(block)
        except Exception:
            continue
        scope = (policy.get("scope") or "").strip()
        if scope not in ("core", "project"):
            continue
        default_model = (policy.get("default_model") or "").strip()
        if not default_model:
            continue
        per_task = policy.get("per_task_type") or {}
        per_purpose_raw = policy.get("per_purpose") or {}

        # Validate task types against allowed sets.
        allowed_core = set(getattr(config, "CORE_TASK_TYPES", ()))
        allowed_proj = set(getattr(config, "PROJECT_TASK_TYPES", ()))
        cleaned_per_task: dict[str, str] = {}
        for k, v in per_task.items():
            ttype = (k or "").strip()
            model_name = (v or "").strip()
            if not model_name:
                continue
            if scope == "core" and allowed_core and ttype not in allowed_core:
                continue
            if scope == "project" and allowed_proj and ttype not in allowed_proj:
                continue
            cleaned_per_task[ttype] = model_name

        cleaned_per_purpose: dict[str, str] = {}
        for k, v in per_purpose_raw.items():
            p = (k or "").strip()
            model_name = (v or "").strip()
            if not model_name or p not in PURPOSES:
                continue
            cleaned_per_purpose[p] = model_name

        final_policy = {
            "default_model": default_model,
            "per_task_type": cleaned_per_task,
            "per_purpose": cleaned_per_purpose,
        }
        db.upsert_model_policy(scope, json.dumps(final_policy))

