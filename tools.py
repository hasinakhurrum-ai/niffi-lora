"""
Engine-side tools the LLM can use: fetch, search, scrape, pip install, run OS commands.
Allowlist for fetch; run_shell runs any OS command with timeout.
"""

import json
import re
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse, quote

import config

try:
    from env import run_shell_command
except ImportError:
    def run_shell_command(cmd: str, cwd=None, timeout_s=60):
        return (-1, "", "env module not available")


def run_shell(
    cmd: str,
    cwd: str | None = None,
    timeout_s: int | None = None,
    env_overlay: dict | None = None,
) -> tuple[int, str, str]:
    """
    Run an OS command in the system shell. Returns (returncode, stdout, stderr).
    cwd: working directory (default current). timeout_s: max seconds (default from config).
    env_overlay: optional env vars merged over current env (e.g. {"DOCKER_HOST": "tcp://host:2375"} for remote docker).
    """
    timeout_s = timeout_s or getattr(config, "SHELL_TIMEOUT_S", 120)
    return run_shell_command(cmd, cwd=cwd, timeout_s=timeout_s, env_overlay=env_overlay)


def fetch_url(url: str) -> str:
    """
    Fetch URL content as text. If FETCH_ALLOWLIST_ONLY is False, any URL is allowed.
    Returns content or error message.
    """
    if not getattr(config, "FETCH_ENABLED", False):
        return "[fetch disabled]"
    try:
        if getattr(config, "FETCH_ALLOWLIST_ONLY", True):
            parsed = urlparse(url)
            domain = (parsed.netloc or "").lower()
            allowed = getattr(config, "FETCH_ALLOWED_DOMAINS", ())
            if domain not in allowed:
                return f"[domain not allowlisted: {domain}]"
        req = urllib.request.Request(url, headers={"User-Agent": "NiffiLab/1.0"})
        with urllib.request.urlopen(req, timeout=getattr(config, "FETCH_TIMEOUT_S", 15)) as r:
            raw = r.read(getattr(config, "FETCH_MAX_BYTES", 500_000))
        return raw.decode("utf-8", errors="replace")
    except Exception as e:
        return f"[fetch error: {e}]"


def search_web(query: str) -> str:
    """
    Search the web via DuckDuckGo API. Returns a text summary: abstract + related links (title, URL).
    Use for research; then fetch_url(url) to scrape content from allowlisted domains.
    """
    if not getattr(config, "SEARCH_ENABLED", False):
        return "[search disabled]"
    try:
        url = "https://api.duckduckgo.com/?q=" + quote(query) + "&format=json"
        raw = fetch_url(url)
        if raw.startswith("["):
            return raw
        data = json.loads(raw)
        parts = []
        if data.get("Abstract"):
            parts.append(data["Abstract"])
            if data.get("AbstractURL"):
                parts.append(f"Source: {data['AbstractURL']}")
        related = data.get("RelatedTopics") or []
        max_r = getattr(config, "SEARCH_MAX_RESULTS", 5)
        for i, item in enumerate(related):
            if i >= max_r:
                break
            if isinstance(item, dict):
                if item.get("FirstURL") and item.get("Text"):
                    parts.append(f"- {item['Text'][:200]} | {item['FirstURL']}")
            elif isinstance(item, str):
                parts.append(f"- {item[:200]}")
        return "\n".join(parts) if parts else "[no results]"
    except json.JSONDecodeError:
        return "[search: invalid response]"
    except Exception as e:
        return f"[search error: {e}]"


def pip_install(package_name: str, bot_name: str) -> tuple[int, str, str]:
    """
    Install a Python package into the bot's venv. Returns (returncode, stdout, stderr).
    Use so generated code or tasks can add libraries; then use them in generated layers.
    """
    pkg = re.sub(r"[^\w\-.]", "", package_name)
    if not pkg:
        return (-1, "", "invalid package name")
    try:
        from sandbox import ensure_bot_venv
        py = ensure_bot_venv(bot_name)
        cmd = f'"{py}" -m pip install {pkg}'
        timeout = getattr(config, "PIP_INSTALL_TIMEOUT_S", 120)
        return run_shell_command(cmd, timeout_s=timeout)
    except Exception as e:
        return (-1, "", str(e))


def scrape_url(url: str) -> str:
    """Alias for fetch_url: fetch and return URL content as text (allowlisted domains only)."""
    return fetch_url(url)


# --- Docker: sense, install (OS-agnostic), remote, permission, and full resource control ---

def _docker_env(host: str | None = None) -> dict | None:
    """Build env overlay for Docker (DOCKER_HOST). host overrides config.DOCKER_HOST."""
    h = (host if host is not None else getattr(config, "DOCKER_HOST", "") or "")
    h = (h or "").strip()
    return {"DOCKER_HOST": h} if h else None


def _run_docker(cmd: str, timeout_s: int = 30, host: str | None = None) -> tuple[int, str, str]:
    """Run a docker command, optionally against remote host. On Linux permission denied, retry with sudo -n."""
    env = _docker_env(host)
    code, out, err = run_shell_command(cmd, timeout_s=timeout_s, env_overlay=env)
    if code == 0:
        return (code, out, err)
    if getattr(config, "DOCKER_USE_SUDO_FALLBACK", True) and not getattr(config, "IS_WINDOWS", False):
        combined = (out + " " + err).lower()
        if "permission denied" in combined or "permission" in err.lower():
            code2, out2, err2 = run_shell_command("sudo -n " + cmd, timeout_s=timeout_s, env_overlay=env)
            if code2 == 0:
                return (code2, out2, err2)
    return (code, out, err)


def docker_available(host: str | None = None) -> bool:
    """Return True if Docker is installed and usable (local or at host). host = DOCKER_HOST (e.g. tcp://host:2375, ssh://user@host)."""
    code, _, _ = _run_docker("docker info", timeout_s=10, host=host)
    return code == 0


def ensure_docker(host: str | None = None) -> tuple[bool, str]:
    """
    Sense if Docker is present (local or at host); if remote host given, only check reachability.
    If local and missing and DOCKER_INSTALL_ENABLED, try to install (OS-agnostic). Handles permission via sudo fallback.
    Returns (True, message) if Docker is available, else (False, message) with install hint.
    """
    if docker_available(host):
        return (True, f"Docker available at {host}." if host else "Docker is available.")
    if host:
        return (False, f"Remote Docker at {host} not reachable. Check DOCKER_HOST / network / TLS.")
    if not getattr(config, "DOCKER_INSTALL_ENABLED", True):
        return (False, "Docker not found. Set DOCKER_INSTALL_ENABLED=True to attempt install, or install manually.")
    is_win = getattr(config, "IS_WINDOWS", False)
    os_name = (getattr(config, "OS_NAME", "") or "").lower()
    is_darwin = os_name == "darwin"
    if is_win:
        code, _, _ = run_shell_command(
            "winget install Docker.DockerDesktop --accept-package-agreements --accept-source-agreements",
            timeout_s=300,
        )
        if code == 0:
            return (docker_available(), "Docker Desktop installed via winget. Restart terminal or add Docker to PATH.")
        code2, _, _ = run_shell_command("choco install docker-desktop -y", timeout_s=300)
        if code2 == 0:
            return (docker_available(), "Docker Desktop installed via choco. Restart terminal or add Docker to PATH.")
        return (False, "Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and add to PATH.")
    if is_darwin:
        code, _, _ = run_shell_command("brew install --cask docker", timeout_s=180)
        if code == 0:
            return (docker_available(), "Docker installed via Homebrew. Start Docker Desktop from Applications if needed.")
        return (False, "Docker not found. Run: brew install --cask docker")
    code, _, _ = run_shell_command(
        "curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && (sudo -n sh /tmp/get-docker.sh 2>/dev/null || sudo sh /tmp/get-docker.sh || sh /tmp/get-docker.sh)",
        timeout_s=120,
    )
    if code == 0 and docker_available():
        return (True, "Docker installed via get.docker.com.")
    return (False, "Docker not found. On Linux run: curl -fsSL https://get.docker.com | sh (may need sudo).")


def _docker_timeout() -> int:
    return getattr(config, "DOCKER_TIMEOUT_S", 300)


def docker_images(host: str | None = None) -> str:
    """List Docker images (local or at host). Returns readable table or error message."""
    if not docker_available(host):
        return "[Docker not available. Use tools.ensure_docker() or ensure_docker(host=...) for remote.]"
    code, out, err = _run_docker("docker images", timeout_s=30, host=host)
    if code != 0:
        return f"[docker images failed: {err or out}]"
    return out.strip() or "(no images)"


def docker_ps(all_containers: bool = True, host: str | None = None) -> str:
    """List containers (all if all_containers=True, else only running). host = remote DOCKER_HOST."""
    if not docker_available(host):
        return "[Docker not available.]"
    cmd = "docker ps -a" if all_containers else "docker ps"
    code, out, err = _run_docker(cmd, timeout_s=15, host=host)
    if code != 0:
        return f"[docker ps failed: {err or out}]"
    return out.strip() or "(no containers)"


def docker_inspect_container(container_id: str, host: str | None = None) -> str:
    """Inspect a container by id or name. Returns JSON (truncated): config, mounts, env, state. host = remote DOCKER_HOST."""
    if not docker_available(host):
        return "[Docker not available.]"
    cid = (container_id or "").strip()
    if not cid:
        return "[container_id required]"
    code, out, err = _run_docker(f"docker inspect {cid}", timeout_s=10, host=host)
    if code != 0:
        return f"[docker inspect failed: {err or out}]"
    max_len = getattr(config, "DOCKER_INSPECT_MAX_CHARS", 15000)
    if len(out) > max_len:
        return out[:max_len] + "\n... (truncated)"
    return out.strip()


def docker_inspect_image(image_id: str, host: str | None = None) -> str:
    """Inspect an image by id or name:tag. Returns JSON: layers, env, entrypoint. host = remote DOCKER_HOST."""
    if not docker_available(host):
        return "[Docker not available.]"
    iid = (image_id or "").strip()
    if not iid:
        return "[image_id required]"
    code, out, err = _run_docker(f"docker image inspect {iid}", timeout_s=10, host=host)
    if code != 0:
        return f"[docker image inspect failed: {err or out}]"
    max_len = getattr(config, "DOCKER_INSPECT_MAX_CHARS", 15000)
    if len(out) > max_len:
        return out[:max_len] + "\n... (truncated)"
    return out.strip()


def docker_pull(image: str, host: str | None = None) -> tuple[int, str, str]:
    """Pull an image (local or at host). Returns (returncode, stdout, stderr)."""
    if not docker_available(host):
        return (-1, "", "Docker not available. Use tools.ensure_docker() or ensure_docker(host=...) for remote.")
    img = (image or "").strip()
    if not img:
        return (-1, "", "image name required")
    return _run_docker(f"docker pull {img}", timeout_s=_docker_timeout(), host=host)


def docker_run(
    image: str,
    cmd: str | list[str] | None = None,
    detach: bool = True,
    env: list[str] | dict | None = None,
    volumes: list[str] | None = None,
    ports: list[str] | None = None,
    name: str | None = None,
    timeout_s: int | None = None,
    host: str | None = None,
    cpus: float | None = None,
    memory_mb: int | None = None,
    memory_swap_mb: int | None = None,
    pids_limit: int | None = None,
    shm_size_mb: int | None = None,
) -> tuple[int, str, str]:
    """
    Run a container with full resource control. Returns (returncode, stdout, stderr). stdout is container id when detach=True.
    env: list of "KEY=value" or dict; volumes: list of "host_path:container_path"; ports: list of "host:container".
    host: remote DOCKER_HOST (tcp://host:2375, ssh://user@host). Resource limits: cpus, memory_mb, memory_swap_mb, pids_limit, shm_size_mb.
    """
    if not docker_available(host):
        return (-1, "", "Docker not available. Use tools.ensure_docker() or ensure_docker(host=...) for remote.")
    img = (image or "").strip()
    if not img:
        return (-1, "", "image name required")
    timeout_s = timeout_s or _docker_timeout()
    parts = ["docker", "run"]
    if detach:
        parts.append("-d")
    if cpus is not None:
        parts.append(f"--cpus={cpus}")
    if memory_mb is not None:
        parts.append(f"--memory={memory_mb}m")
    if memory_swap_mb is not None:
        parts.append(f"--memory-swap={memory_swap_mb}m")
    if pids_limit is not None:
        parts.append(f"--pids-limit={pids_limit}")
    if shm_size_mb is not None:
        parts.append(f"--shm-size={shm_size_mb}m")
    if name:
        safe_name = (name or "").strip().replace('"', "")
        if safe_name:
            parts.append(f'--name "{safe_name}"')
    if env:
        if isinstance(env, dict):
            for k, v in env.items():
                parts.append(f"-e {k}={v}")
        else:
            for e in env:
                if "=" in str(e):
                    parts.append(f"-e {e}")
    if volumes:
        for v in volumes:
            if v:
                parts.append(f"-v {v}")
    if ports:
        for p in ports:
            if p:
                parts.append(f"-p {p}")
    parts.append(img)
    if cmd is not None:
        if isinstance(cmd, list):
            parts.extend(cmd)
        else:
            parts.append(str(cmd))
    full_cmd = " ".join(parts)
    return _run_docker(full_cmd, timeout_s=timeout_s, host=host)


def docker_stop(container_id: str, host: str | None = None) -> tuple[int, str, str]:
    """Stop a container (local or at host). Returns (returncode, stdout, stderr)."""
    if not docker_available(host):
        return (-1, "", "Docker not available.")
    cid = (container_id or "").strip()
    if not cid:
        return (-1, "", "container_id required")
    return _run_docker(f"docker stop {cid}", timeout_s=60, host=host)


def docker_rm(container_id: str, force: bool = False, host: str | None = None) -> tuple[int, str, str]:
    """Remove a container. Use force=True to remove running. host = remote DOCKER_HOST."""
    if not docker_available(host):
        return (-1, "", "Docker not available.")
    cid = (container_id or "").strip()
    if not cid:
        return (-1, "", "container_id required")
    f = " -f" if force else ""
    return _run_docker(f"docker rm{f} {cid}", timeout_s=30, host=host)


def docker_logs(container_id: str, tail: int = 100, host: str | None = None) -> str:
    """Get logs of a container. Returns last 'tail' lines. host = remote DOCKER_HOST."""
    if not docker_available(host):
        return "[Docker not available.]"
    cid = (container_id or "").strip()
    if not cid:
        return "[container_id required]"
    code, out, err = _run_docker(f"docker logs --tail {tail} {cid}", timeout_s=15, host=host)
    if code != 0:
        return f"[docker logs failed: {err or out}]"
    return (out or "").strip() or "(no logs)"


def docker_stats(container_id: str | None = None, host: str | None = None) -> str:
    """Resource usage of containers: CPU %, memory, block I/O. Pass container_id for one container, else all. host = remote DOCKER_HOST."""
    if not docker_available(host):
        return "[Docker not available.]"
    cid = (container_id or "").strip() or ""
    cmd = f"docker stats --no-stream {cid}".strip()
    code, out, err = _run_docker(cmd, timeout_s=15, host=host)
    if code != 0:
        return f"[docker stats failed: {err or out}]"
    return (out or "").strip() or "(no running containers)"


def _docker_host_to_http_base(host: str | None, port: int = 11434) -> str:
    """Convert DOCKER_HOST (tcp:// or ssh://) to HTTP base URL for a service on given port."""
    if not host or not str(host).strip():
        return f"http://localhost:{port}"
    h = str(host).strip()
    if h.startswith("tcp://"):
        part = h[6:].split("/")[0].split(":")[0]
        return f"http://{part}:{port}"
    if "ssh://" in h or "@" in h:
        part = h.split("@")[-1].strip().split("/")[0].split(":")[0]
        return f"http://{part}:{port}"
    return f"http://{h}:{port}"


def run_model_from_docker(
    image: str = "ollama/ollama",
    model_name: str | None = None,
    port: int = 11434,
    name: str | None = None,
    host: str | None = None,
    register_as: str | None = None,
) -> tuple[bool, str]:
    """
    Run a model server (e.g. Ollama) in Docker, optionally pull a model, and register as external.
    Returns (True, registered_model_name) or (False, error_message).
    register_as: name to register in model registry (default: ollama-docker or ollama-docker-<port>).
    """
    if not docker_available(host):
        return (False, "Docker not available. Use tools.ensure_docker() or ensure_docker(host=...) first.")
    container_name = name or f"ollama-{port}"
    reg_name = (register_as or f"ollama-docker-{port}").strip()
    code, out, err = docker_run(
        image,
        cmd=None,
        detach=True,
        ports=[f"{port}:11434"],
        name=container_name,
        host=host,
        timeout_s=getattr(config, "DOCKER_TIMEOUT_S", 300),
    )
    if code != 0:
        return (False, f"docker run failed: {err or out}")
    import time
    time.sleep(3)
    if model_name and (model_name or "").strip():
        pull_cmd = f"docker exec {container_name} ollama pull {model_name.strip()}"
        code2, _, err2 = _run_docker(pull_cmd, timeout_s=600, host=host)
        if code2 != 0:
            pass
    base_url = _docker_host_to_http_base(host or getattr(config, "DOCKER_HOST", ""), port)
    try:
        rid = register_external_model(reg_name, base_url)
        if rid >= 0:
            return (True, f"Registered as '{reg_name}' at {base_url}. Use MODEL: {reg_name}")
    except Exception as e:
        return (False, f"Register failed: {e}")
    return (True, f"Container running. Register manually: REGISTER_EXTERNAL: {reg_name} endpoint={base_url}")


# --- Remote SSH and instances (e.g. Kali) ---

def run_remote_ssh(
    host: str,
    user: str,
    command: str,
    key_path: str | None = None,
    timeout_s: int = 60,
) -> tuple[int, str, str]:
    """
    Run a command on a remote host via SSH. Use for remote Kali or any SSH-accessible box.
    key_path: optional path to private key; else uses default SSH agent.
    Returns (returncode, stdout, stderr).
    """
    host = (host or "").strip()
    user = (user or "").strip()
    if not host or not user:
        return (-1, "", "host and user required")
    opts = "-o StrictHostKeyChecking=no -o ConnectTimeout=10"
    if key_path and str(key_path).strip():
        opts += f' -i "{str(key_path).strip()}"'
    cmd = f'ssh {opts} {user}@{host} {repr(command)}'
    return run_shell_command(cmd, timeout_s=timeout_s)


def _remote_instances_path() -> Path:
    return Path(getattr(config, "STATE_DIR", "state")) / "remote_instances.json"


def list_remote_instances() -> list[dict]:
    """List registered remote instances (e.g. Kali). Each dict: name, host, user, key_path (optional)."""
    p = _remote_instances_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return list(data.get("instances") or [])
    except Exception:
        return []


def register_remote_instance(
    name: str, host: str, user: str, key_path: str | None = None, role: str | None = None
) -> tuple[bool, str]:
    """Register a remote instance (e.g. Kali) by name. role: pentest | sandbox | general (optional). Returns (True, msg) or (False, error)."""
    name = (name or "").strip()
    host = (host or "").strip()
    user = (user or "").strip()
    role = (role or "").strip() or None
    if not name or not host or not user:
        return (False, "name, host, and user required")
    p = _remote_instances_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    instances = list_remote_instances()
    for i in instances:
        if (i.get("name") or "").strip() == name:
            i["host"] = host
            i["user"] = user
            i["key_path"] = (key_path or "").strip() or None
            i["role"] = role
            p.write_text(json.dumps({"instances": instances}, indent=2), encoding="utf-8")
            return (True, f"Updated instance '{name}'.")
    instances.append({"name": name, "host": host, "user": user, "key_path": (key_path or "").strip() or None, "role": role})
    p.write_text(json.dumps({"instances": instances}, indent=2), encoding="utf-8")
    return (True, f"Registered remote instance '{name}' ({user}@{host}). Use run_on_remote_instance('{name}', 'cmd').")


def run_on_remote_instance(
    instance_name: str, command: str, timeout_s: int = 60, task_type: str | None = None
) -> tuple[int, str, str]:
    """Run a command on a registered remote instance (e.g. Kali). If task_type is set, instance role must allow it (REMOTE_INSTANCE_ROLE_POLICY). Returns (returncode, stdout, stderr)."""
    name = (instance_name or "").strip()
    instances = list_remote_instances()
    for i in instances:
        if (i.get("name") or "").strip() == name:
            role = (i.get("role") or "").strip()
            if task_type and role:
                policy = getattr(config, "REMOTE_INSTANCE_ROLE_POLICY", None) or {}
                allowed = policy.get(role)
                if allowed is not None and task_type not in (allowed if isinstance(allowed, (list, tuple)) else [allowed]):
                    return (-1, "", f"Task type '{task_type}' not allowed for instance role '{role}'. Policy: {policy}")
            return run_remote_ssh(
                i.get("host", ""),
                i.get("user", ""),
                command,
                key_path=i.get("key_path"),
                timeout_s=timeout_s,
            )
    return (-1, "", f"Unknown remote instance: {instance_name}. Use list_remote_instances() and register_remote_instance().")


# --- Quantum (Phase 4; optional qiskit) ---

def get_quantum_backends() -> str:
    """List available quantum backends (simulators). Returns JSON string or error. Requires qiskit, qiskit-aer."""
    if not getattr(config, "QUANTUM_ENABLED", False):
        return "[Quantum disabled. Set config.QUANTUM_ENABLED=True and install qiskit, qiskit-aer.]"
    try:
        from qiskit_aer import Aer
        backends = Aer.backends()
        names = [b.name for b in backends]
        return json.dumps({"backends": names, "default": getattr(config, "QUANTUM_DEFAULT_BACKEND", "qasm_simulator")})
    except ImportError:
        return "[qiskit-aer not installed. pip install qiskit qiskit-aer]"
    except Exception as e:
        return f"[get_quantum_backends error: {e}]"


def run_quantum_circuit(spec: str | dict, backend: str | None = None, shots: int = 1024) -> str:
    """Run a quantum circuit. spec: OpenQASM string or dict with 'qasm' key. backend: simulator name. Returns counts JSON or error."""
    if not getattr(config, "QUANTUM_ENABLED", False):
        return "[Quantum disabled. Set config.QUANTUM_ENABLED=True.]"
    backend = backend or getattr(config, "QUANTUM_DEFAULT_BACKEND", "qasm_simulator")
    try:
        from qiskit import QuantumCircuit
        from qiskit_aer import Aer
        if isinstance(spec, dict):
            qasm = spec.get("qasm") or spec.get("circuit")
            if not qasm:
                return "[spec must contain 'qasm' or 'circuit']"
            qc = QuantumCircuit.from_qasm_str(qasm) if isinstance(qasm, str) else QuantumCircuit.from_qasm_str(str(qasm))
        else:
            qc = QuantumCircuit.from_qasm_str(str(spec))
        sim = Aer.get_backend(backend)
        from qiskit import transpile
        job = sim.run(transpile(qc, sim), shots=shots)
        result = job.result()
        counts = result.get_counts()
        return json.dumps(dict(counts))
    except ImportError:
        return "[qiskit/qiskit-aer not installed]"
    except Exception as e:
        return f"[run_quantum_circuit error: {e}]"


# --- Self-created models and training ---

def create_ollama_model(name: str, base_model: str, system_prompt: str = "") -> tuple[int, str, str]:
    """
    Create a new Ollama model from a base model with a custom system prompt.
    Writes a Modelfile and runs `ollama create`. Registers the model in the engine.
    Returns (returncode, stdout, stderr).
    """
    import os
    try:
        import db
    except ImportError:
        return (-1, "", "db not available")
    models_dir = getattr(config, "MODELS_DIR", "state/models")
    dir_path = Path(models_dir) / name.replace("/", "_").replace("\\", "_")
    dir_path.mkdir(parents=True, exist_ok=True)
    modelfile = dir_path / "Modelfile"
    lines = [f"FROM {base_model.strip()}"]
    if (system_prompt or "").strip():
        lines.append('SYSTEM """')
        lines.append(system_prompt.strip())
        lines.append('"""')
    modelfile.write_text("\n".join(lines), encoding="utf-8")
    cwd = str(dir_path.resolve())
    cmd = f'ollama create {name} -f "{cwd}"'
    code, out, err = run_shell_command(cmd, cwd=cwd, timeout_s=getattr(config, "OLLAMA_CREATE_TIMEOUT_S", 600))
    if code == 0:
        db.insert_model(name, "ollama", None, base_model.strip())
    return (code, out, err)


def register_external_model(name: str, endpoint_url: str) -> int:
    """
    Register an external model (Ollama-compatible API at endpoint_url).
    The engine will use this endpoint when a bot uses MODEL: name.
    Returns DB row id.
    """
    try:
        import db
        return db.insert_model(name.strip(), "external", endpoint_url.strip().rstrip("/"), None)
    except Exception:
        return -1


def collect_training_example(
    prompt: str,
    response: str,
    score: float | None = None,
    run_id: int | None = None,
    bot_id: int | None = None,
) -> int:
    """
    Record a (prompt, response, score) for training. Used to build datasets for fine-tuning.
    Returns inserted row id.
    """
    try:
        import db
        return db.insert_training_example(prompt, response, score, run_id, bot_id)
    except Exception:
        return -1


# --- Git (full repo access: branch, add, commit, merge, optional push) ---

_GIT_REPO_ROOT: str | None = None


def git_repo_root() -> str | None:
    """Return the repo root (directory containing .git). Uses config.GIT_REPO_ROOT if set, else infers from this file."""
    global _GIT_REPO_ROOT
    if _GIT_REPO_ROOT is not None:
        return _GIT_REPO_ROOT
    root = (getattr(config, "GIT_REPO_ROOT", "") or "").strip()
    if root and Path(root).joinpath(".git").exists():
        _GIT_REPO_ROOT = str(Path(root).resolve())
        return _GIT_REPO_ROOT
    start = Path(__file__).resolve().parent
    for p in [start] + list(start.parents):
        if p.joinpath(".git").exists():
            _GIT_REPO_ROOT = str(p)
            return _GIT_REPO_ROOT
    return None


def _git_cwd(cwd: str | Path | None) -> str | None:
    """Resolve cwd for git commands. Returns repo root or None if git disabled or no repo."""
    if not getattr(config, "GIT_ENGINE_ENABLED", False):
        return None
    root = git_repo_root()
    if not root:
        return None
    if cwd is not None:
        return str(Path(cwd).resolve()) if Path(cwd).exists() else root
    return root


def _git_audit(op: str, **payload: object) -> None:
    if not getattr(config, "GIT_AUDIT", False):
        return
    try:
        import audit_log
        audit_log.emit(f"git_{op}", payload)
    except Exception:
        pass


def _git_run(args: list[str], cwd: str | None, timeout_s: int | None = None) -> tuple[int, str, str]:
    c = _git_cwd(cwd)
    if c is None:
        return (-1, "", "[git disabled or no repo root]")
    timeout_s = timeout_s or getattr(config, "GIT_TIMEOUT_S", 30)
    def _quote(a: str) -> str:
        if not a or " " in a or '"' in a or "\\" in a:
            return '"' + str(a).replace("\\", "\\\\").replace('"', '\\"') + '"'
        return a
    cmd = "git " + " ".join(_quote(str(a)) for a in args)
    return run_shell_command(cmd, cwd=c, timeout_s=timeout_s)


def git_status(cwd: str | None = None) -> tuple[int, str, str]:
    """Short status. Returns (returncode, stdout, stderr)."""
    return _git_run(["status", "-sb"], cwd)


def git_branch_list(cwd: str | None = None) -> tuple[int, str, str]:
    """List all branches (local and remote). Returns (returncode, stdout, stderr)."""
    return _git_run(["branch", "-a"], cwd)


def git_branch_create(branch_name: str, cwd: str | None = None) -> tuple[int, str, str]:
    """Create and checkout a new branch. Returns (returncode, stdout, stderr)."""
    name = (branch_name or "").strip()
    if not name:
        return (-1, "", "branch name required")
    r, o, e = _git_run(["checkout", "-b", name], cwd)
    if r == 0:
        _git_audit("branch_create", branch=name, returncode=r, out=(o or e)[:300])
    return (r, o, e)


def git_checkout(ref: str, cwd: str | None = None) -> tuple[int, str, str]:
    """Checkout a branch or commit. Returns (returncode, stdout, stderr)."""
    ref = (ref or "").strip()
    if not ref:
        return (-1, "", "ref (branch or commit) required")
    r, o, e = _git_run(["checkout", ref], cwd)
    if r == 0:
        _git_audit("checkout", ref=ref, returncode=r, out=(o or e)[:300])
    return (r, o, e)


def git_add(paths: str | list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Stage paths (e.g. 'generated/' or '.' or ['file1.py', 'generated/']). Returns (returncode, stdout, stderr)."""
    c = _git_cwd(cwd)
    if c is None:
        return (-1, "", "[git disabled or no repo root]")
    if isinstance(paths, str):
        paths = [paths]
    paths = [p.strip() for p in paths if p and str(p).strip()]
    if not paths:
        return (-1, "", "paths required")
    args = ["add"] + paths
    r, o, e = _git_run(args, cwd)
    if r == 0:
        _git_audit("add", paths=paths, returncode=r, out=(o or e)[:300])
    return (r, o, e)


def git_commit(message: str, cwd: str | None = None) -> tuple[int, str, str]:
    """Commit staged changes. message must be non-empty. Returns (returncode, stdout, stderr)."""
    msg = (message or "").strip()
    if not msg:
        return (-1, "", "commit message required")
    r, o, e = _git_run(["commit", "-m", msg], cwd)
    if r == 0:
        _git_audit("commit", message=msg[:200], returncode=r, out=(o or e)[:300])
        if getattr(config, "GIT_AUTO_PUSH_AFTER_COMMIT", False):
            try:
                branch = _git_current_branch(cwd)
                if branch:
                    rp, op, ep = git_push("origin", branch, cwd)
                    if rp != 0 and (op or ep):
                        _git_audit("push_after_commit", returncode=rp, out=(op or ep)[:300])
            except Exception:
                pass
    return (r, o, e)


def _git_current_branch(cwd: str | None) -> str | None:
    r, o, e = _git_run(["branch", "--show-current"], cwd)
    if r != 0 or not (o or "").strip():
        return None
    return (o or e).strip().split("\n")[0].strip()


def git_current_branch(cwd: str | None = None) -> str | None:
    """Return current branch name. None if not a repo or error."""
    return _git_current_branch(cwd)


def git_merge(branch_or_ref: str, cwd: str | None = None) -> tuple[int, str, str]:
    """Merge branch_or_ref into current branch. Refused if current branch is in GIT_PROTECTED_BRANCHES. Returns (returncode, stdout, stderr)."""
    ref = (branch_or_ref or "").strip()
    if not ref:
        return (-1, "", "branch or ref required")
    current = _git_current_branch(cwd)
    protected = getattr(config, "GIT_PROTECTED_BRANCHES", ()) or ()
    if current and current in protected:
        return (-1, "", f"refused: current branch {current!r} is protected (no merge into protected via automation)")
    r, o, e = _git_run(["merge", ref], cwd)
    if r == 0:
        _git_audit("merge", ref=ref, into=current, returncode=r, out=(o or e)[:300])
    return (r, o, e)


def git_push(remote: str | None = None, branch: str | None = None, cwd: str | None = None) -> tuple[int, str, str]:
    """Push to remote. Disabled unless config.GIT_ALLOW_PUSH is True. Returns (returncode, stdout, stderr)."""
    if not getattr(config, "GIT_ALLOW_PUSH", False):
        return (-1, "", "[git push disabled by config]")
    args = ["push"]
    if remote:
        args.append(remote)
    if branch:
        args.append(branch)
    r, o, e = _git_run(args, cwd)
    if r == 0:
        _git_audit("push", remote=remote, branch=branch, returncode=r, out=(o or e)[:300])
    return (r, o, e)


def git_head_rev(cwd: str | None = None) -> str | None:
    """Return current HEAD commit hash (short). None if not a repo or error."""
    r, o, e = _git_run(["rev-parse", "HEAD"], cwd)
    if r != 0 or not (o or "").strip():
        return None
    return (o or e).strip().split("\n")[0].strip()


def git_pull(remote: str | None = None, branch: str | None = None, cwd: str | None = None) -> tuple[int, str, str]:
    """Pull from remote. Disabled unless config.GIT_ALLOW_PULL is True. Returns (returncode, stdout, stderr)."""
    if not getattr(config, "GIT_ALLOW_PULL", True):
        return (-1, "", "[git pull disabled by config]")
    args = ["pull"]
    if remote:
        args.append(remote)
    if branch:
        args.append(branch)
    r, o, e = _git_run(args, cwd)
    if r == 0:
        _git_audit("pull", remote=remote, branch=branch, returncode=r, out=(o or e)[:300])
    return (r, o, e)
