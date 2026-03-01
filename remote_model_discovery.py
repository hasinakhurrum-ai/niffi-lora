"""
Remote model discovery: periodically ping registered external model endpoints and log availability.
Phase 5. Call from main every REMOTE_MODEL_DISCOVERY_EVERY_N_CYCLES.
"""

import os

try:
    import config
    import db
except ImportError:
    config = None
    db = None


def discover_remote_models() -> list[dict]:
    """Ping each registered external model endpoint (GET /api/tags). Return list of {name, url, ok}."""
    if db is None:
        return []
    try:
        models = db.list_models()
    except Exception:
        return []
    results = []
    for m in models:
        if (m.get("source_type") or "").lower() != "external":
            continue
        url = (m.get("endpoint_url") or "").strip().rstrip("/")
        if not url:
            continue
        name = m.get("name") or ""
        ok = False
        try:
            import urllib.request
            req = urllib.request.Request(url + "/api/tags", headers={"User-Agent": "NiffiLab/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status == 200:
                    ok = True
        except Exception:
            pass
        results.append({"name": name, "url": url, "ok": ok})
    return results
