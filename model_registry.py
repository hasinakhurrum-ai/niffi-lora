"""Model registry: self-created and external models used in parallel by bots."""

import config
import db

try:
    from ollama_client import ensure_model as _ollama_ensure_model
except ImportError:
    def _ollama_ensure_model(model: str, api_base: str | None = None):
        pass


def insert_model(
    name: str,
    source_type: str = "ollama",
    endpoint_url: str | None = None,
    base_model: str | None = None,
) -> int:
    """Register a model. If name exists, updates it. Returns row id."""
    return db.insert_model(name, source_type, endpoint_url, base_model)


def get_model(name: str) -> dict | None:
    return db.get_model(name)


def list_models() -> list[dict]:
    return db.list_models()


def get_model_endpoint(model_name: str) -> tuple[str | None, str]:
    """Resolve model to (api_base, model_name). api_base is None for default Ollama."""
    row = get_model(model_name.strip())
    if row and (row.get("source_type") or "").lower() == "external":
        url = (row.get("endpoint_url") or "").strip().rstrip("/")
        if url:
            return (url, model_name.strip())
    return (None, model_name.strip() or (config.DEFAULT_MODEL or "qwen2.5-coder:3b"))


def ensure_model(model_name: str) -> None:
    """Ensure model is available. For ollama: pull if missing. For external: no-op."""
    api_base, name = get_model_endpoint(model_name)
    _ollama_ensure_model(name, api_base)


def list_registered_models() -> list[dict]:
    """Alias for list_models for backward compatibility."""
    return list_models()
