"""Core kernel prompt assembly: build_core_prompt() for the CORE_AGENT.

Prompt content is data-driven from the DB:
- prompt_blocks / prompt_policies define reusable identity/rules text and assembly rules.
- architecture_state holds the latest snapshot of the engine's architecture and metrics.
- capabilities_registry exposes current capabilities and limits.
- upgrade_backlog / upgrade_plans provide recent and active upgrades.
"""

from __future__ import annotations

from typing import Any

import json

import config
import db


def _truncate(text: str, max_chars: int | None) -> str:
    if max_chars is None or max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n... [truncated]"


def _render_architecture_snapshot() -> str:
    rows = db.list_architecture_state()
    if not rows:
        return "Architecture snapshot is not yet populated.\n"
    lines: list[str] = []
    for r in rows:
        comp = r.get("component", "?")
        summary = r.get("summary") or ""
        interfaces = r.get("interfaces") or ""
        known = r.get("known_issues") or ""
        metrics_raw = r.get("metrics_json") or "{}"
        try:
            metrics = json.loads(metrics_raw)
        except Exception:
            metrics = {}
        lines.append(f"- {comp}: {summary}")
        if interfaces:
            lines.append(f"  Interfaces: {interfaces}")
        if known:
            lines.append(f"  Known issues: {known}")
        if metrics:
            flat = ", ".join(f"{k}={v}" for k, v in metrics.items())
            lines.append(f"  Metrics: {flat}")
    return "Current architecture snapshot:\n" + "\n".join(lines) + "\n"


def _render_capabilities() -> str:
    rows = db.list_capabilities()
    if not rows:
        return "Capabilities registry is empty.\n"
    lines: list[str] = ["Available capabilities and limits:"]
    for r in rows:
        cap = r.get("capability", "?")
        status = r.get("status") or "unknown"
        limits_raw = r.get("limits_json") or "{}"
        try:
            limits = json.loads(limits_raw)
        except Exception:
            limits = {}
        limits_str = ", ".join(f"{k}={v}" for k, v in limits.items()) if limits else "no explicit limits"
        lines.append(f"- {cap}: status={status}, limits=({limits_str})")
    return "\n".join(lines) + "\n"


def _render_recent_upgrades(limit: int = 10) -> str:
    rows = db.list_upgrades(limit=limit)
    if not rows:
        return "No upgrades recorded yet.\n"
    lines: list[str] = ["Recent upgrades and proposals:"]
    for r in rows:
        title = r.get("title", "")
        status = r.get("status", "new")
        prio = r.get("priority", 0)
        lines.append(f"- [{status}] (priority={prio}) {title}")
    return "\n".join(lines) + "\n"


def build_core_prompt(bot_id: int, task_prompt: str, task_type: str) -> str:
    """Assemble the CORE_AGENT prompt from DB-driven blocks and live state."""
    scope = "core"
    blocks = db.get_prompt_blocks(scope)
    policy = db.get_prompt_policy(scope) or {}

    # Map blocks by name -> latest version (sorted by version in get_prompt_blocks).
    by_name: dict[str, dict[str, Any]] = {}
    for b in blocks:
        name = b.get("name") or ""
        if not name:
            continue
        if name not in by_name:
            by_name[name] = b

    # Default policy if none stored.
    block_order = policy.get(
        "block_order",
        [
            "core_identity",
            "rules",
            "architecture_snapshot",
            "capabilities",
            "recent_upgrades",
        ],
    )
    budgets = policy.get("budgets", {})
    include_if = policy.get("include_if", {})

    sections: list[str] = []

    for key in block_order:
        max_chars = budgets.get(key)
        # Dynamic blocks
        if key == "architecture_snapshot":
            text = _render_architecture_snapshot()
            sections.append(_truncate(text, max_chars))
        elif key == "capabilities":
            text = _render_capabilities()
            sections.append(_truncate(text, max_chars))
        elif key == "recent_upgrades":
            text = _render_recent_upgrades()
            sections.append(_truncate(text, max_chars))
        else:
            # Static prompt block from DB.
            blk = by_name.get(key)
            if not blk:
                continue
            text = blk.get("content") or ""
            sections.append(_truncate(text, max_chars))

    # Always append the current task at the end so the core knows what to focus on.
    task_section_lines = [
        "=== CURRENT CORE TASK ===",
        f"Task type: {task_type}",
        f"Instruction: {task_prompt}",
    ]
    sections.append("\n".join(task_section_lines))

    return "\n\n".join(sections).strip() + "\n"

