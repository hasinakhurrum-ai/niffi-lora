"""Model discovery: fetch available models from Ollama /api/tags. Cached for performance."""

from __future__ import annotations

import time
from typing import Optional

try:
    import requests
except ImportError:
    requests = None

import config

_CACHE: dict = {"models": [], "ts": 0.0}


def fetch_ollama_models(api_base: str | None = None) -> list[str]:
    """Fetch model names from Ollama /api/tags. Returns empty list on failure."""
    if not requests:
        return []
    base = (api_base or getattr(config, "OLLAMA_URL", "http://localhost:11434")).rstrip("/")
    url = f"{base}/api/tags"
    ttl = getattr(config, "DISCOVERY_TTL_S", 300)
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json()
        models = data.get("models") or []
        names = [m.get("name", "") for m in models if m.get("name")]
        _CACHE["models"] = names
        _CACHE["ts"] = time.time()
        return names
    except Exception:
        return _CACHE.get("models", [])  # return stale cache if any


def get_discovered_models(force_refresh: bool = False) -> list[str]:
    """Return cached discovered models. Refresh if stale or force_refresh."""
    ttl = getattr(config, "DISCOVERY_TTL_S", 300)
    now = time.time()
    if force_refresh or not _CACHE["models"] or (now - _CACHE["ts"]) > ttl:
        fetch_ollama_models()
    return list(_CACHE.get("models", []))


def pick_first_available(preferred: list[str] | None = None) -> str | None:
    """Return first model that exists in discovered. Prefer order from preferred if given."""
    discovered = get_discovered_models()
    if not discovered:
        return None
    if preferred:
        for p in preferred:
            p_base = p.split(":")[0]
            for d in discovered:
                if d.split(":")[0] == p_base or d == p:
                    return d
    return discovered[0] if discovered else None
