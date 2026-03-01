"""Ollama HTTP API: streaming and non-streaming generate, model ensure/pull. Supports external api_base."""

import json
import requests
import time

import config
import engine_log


def _base(api_base: str | None) -> str:
    return (api_base or config.OLLAMA_URL).rstrip("/")


def _tags_url(api_base: str | None = None) -> str:
    return f"{_base(api_base)}/api/tags"


def _pull_url(api_base: str | None = None) -> str:
    return f"{_base(api_base)}/api/pull"


def _generate_url(api_base: str | None = None) -> str:
    return f"{_base(api_base)}/api/generate"


def ensure_model(model: str, api_base: str | None = None) -> None:
    """Pull model if not present. No-op if api_base is set (external)."""
    if api_base:
        return
    try:
        r = requests.get(_tags_url(api_base), timeout=10)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        base = model.split(":")[0]
        if any(n.split(":")[0] == base for n in names):
            return
    except requests.RequestException:
        pass
    try:
        r = requests.post(_pull_url(api_base), json={"name": model}, timeout=600, stream=True)
        r.raise_for_status()
    except Exception as e:
        engine_log.log_error(f"[LLM] ensure_model pull failed for {model}: {e}", exc_info=True)
        raise
    for _ in r.iter_content(chunk_size=1024):
        pass


def generate(
    model: str,
    prompt: str,
    *,
    stream: bool = False,
    temperature: float = 0.7,
    api_base: str | None = None,
    num_predict: int | None = None,
) -> str:
    """
    Generate via Ollama or external Ollama-compatible API.
    api_base: if set, use this URL instead of config.OLLAMA_URL.
    If stream=True, print tokens to console and return full response.
    num_predict: max tokens to generate (prevents runaway output); uses config.MODEL_NUM_PREDICT if not set.
    """
    base = _base(api_base)
    opts = {"temperature": temperature}
    np = num_predict if num_predict is not None else getattr(config, "MODEL_NUM_PREDICT", 8192)
    if np > 0:
        opts["num_predict"] = np
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": opts,
    }
    url = f"{base}/api/generate"
    start = time.perf_counter()
    status = "ok"
    try:
        if not stream:
            r = requests.post(url, json=payload, timeout=120)
            r.raise_for_status()
            text = (r.json().get("response") or "").strip()
            return text

        r = requests.post(url, json=payload, timeout=120, stream=True)
        r.raise_for_status()
        buf = []
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                data = json.loads(line)
                chunk = data.get("response") or ""
                if chunk:
                    print(chunk, end="", flush=True)
                    buf.append(chunk)
                if data.get("done"):
                    break
            except json.JSONDecodeError:
                continue
        print()
        return "".join(buf).strip()
    except Exception as e:
        status = "error"
        engine_log.log_error(f"[LLM] generate failed model={model} api_base={base}: {e}", exc_info=True)
        raise
    finally:
        try:
            from generated.core import model_metrics

            latency_ms = (time.perf_counter() - start) * 1000.0
            model_metrics.record_llm_call(
                model_name=model,
                latency_ms=int(latency_ms),
                status=status,
                prompt=(prompt or "")[:500],
            )
        except Exception:
            pass


def generate_with_fallback(
    model_pool: list[tuple[str | None, str]],
    prompt: str,
    *,
    stream: bool = False,
    temperature: float = 0.7,
) -> str:
    """
    Try each (api_base, model) in pool until one succeeds.
    On timeout/error, record failure and try next. Used by project & core agents.
    """
    retry_count = getattr(config, "MODEL_RETRY_COUNT", 2)
    retry_on_timeout = getattr(config, "MODEL_RETRY_ON_TIMEOUT", True)
    last_err: Exception | None = None
    for api_base, model in model_pool[: retry_count + 1]:
        try:
            return generate(
                model, prompt, stream=stream, temperature=temperature, api_base=api_base
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, OSError) as e:
            last_err = e
            engine_log.log_warn(f"[LLM] fallback: model={model} failed ({type(e).__name__}): {e}")
            if retry_on_timeout:
                try:
                    from generated.core import model_metrics

                    model_metrics.record_llm_call(
                        model_name=model,
                        latency_ms=120000,
                        status="timeout",
                        bot_id=0,
                        task_id=None,
                        purpose="TASK",
                        prompt=prompt[:100],
                        response=None,
                    )
                except Exception:
                    pass
                continue
            raise
        except Exception as e:
            last_err = e
            engine_log.log_error(f"[LLM] fallback: model={model} error: {e}", exc_info=True)
            if retry_on_timeout:
                try:
                    from generated.core import model_metrics

                    model_metrics.record_llm_call(
                        model_name=model,
                        latency_ms=0,
                        status="error",
                        bot_id=0,
                        task_id=None,
                        purpose="TASK",
                        prompt=prompt[:100],
                        response=None,
                    )
                except Exception:
                    pass
                continue
            raise
    if last_err:
        engine_log.log_error(f"[LLM] all models in pool failed. last_err: {last_err}")
        raise last_err
    engine_log.log_error("[LLM] no models in pool")
    raise RuntimeError("No models in pool")
