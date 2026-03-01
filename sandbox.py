"""
Persistent sandbox per bot: each project lives under projects/<bot_name>/ (venv, workspace).
Single workspace per project: all runs use projects/<bot_name>/workspace/ for current code (one version per agent).
OS-aware: Windows uses venv/Scripts/python.exe, Unix uses venv/bin/python.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import config

# Project root per bot: projects/<bot_name>/ (venv, runs live here)
def get_project_root(bot_name: str) -> Path:
    """Root directory for this project. All project files (venv, runs, creations) live under it."""
    projects_dir = Path(getattr(config, "PROJECTS_DIR", "projects"))
    return projects_dir / _safe_dir_name(bot_name)


def _safe_dir_name(bot_name: str) -> str:
    """Safe directory name (no path separators, no leading dot)."""
    name = (bot_name or "default").strip().replace(" ", "_")
    return "".join(c for c in name if c.isalnum() or c in "_-") or "default"


try:
    import env
    def _venv_python(bot_name: str) -> Path:
        venv_root = get_project_root(bot_name) / "venv"
        return env.venv_python_path(venv_root)
except ImportError:
    def _venv_python(bot_name: str) -> Path:
        return get_project_root(bot_name) / "venv" / "Scripts" / "python.exe"


def ensure_bot_venv(bot_name: str) -> Path:
    """Create venv for bot if missing. Return path to python.exe. Lives under projects/<bot_name>/venv."""
    venv_dir = get_project_root(bot_name) / "venv"
    py = _venv_python(bot_name)
    if py.exists():
        return py
    venv_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )
    return py


def get_project_workspace(bot_name: str) -> Path:
    """Single canonical workspace for this project. All runs write and test here (one version per project)."""
    root = get_project_root(bot_name)
    d = root / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_run_workspace(bot_name: str, run_id: int) -> Path:
    """Return the project workspace (single dir). Kept for API compat; prefer get_project_workspace."""
    return get_project_workspace(bot_name)


def run_candidate(
    code: str,
    workspace: Path,
    python_exe: Path,
    *,
    timeout_s: int | None = None,
    max_stdout_chars: int | None = None,
    max_stderr_chars: int | None = None,
    stream_stdout: bool = True,
    run_mode: str = "normal",
) -> dict:
    """
    Write candidate.py to workspace, run with python_exe. Capture or stream stdout.
    Returns: stdout, stderr, returncode, timed_out, duration (seconds).
    Uses absolute paths so subprocess does not duplicate workspace path (Windows).
    """
    if run_mode == "server":
        timeout_s = timeout_s or getattr(config, "SERVER_TIMEOUT_S", 30)
    else:
        timeout_s = timeout_s or config.TIMEOUT_S
    max_stdout_chars = max_stdout_chars or config.MAX_STDOUT_CHARS
    max_stderr_chars = max_stderr_chars or config.MAX_STDERR_CHARS

    workspace = Path(workspace).resolve()
    script = workspace / "candidate.py"
    script.write_text(code, encoding="utf-8")
    log_path = workspace / "logs.txt"

    def trunc(s: str, n: int) -> str:
        return s if len(s) <= n else s[:n] + "\n... [truncated]"

    # Phase 2: optional Docker sandbox for generated code
    if getattr(config, "RUN_GENERATED_IN_DOCKER", False):
        try:
            from tools import docker_available, run_shell
            if docker_available():
                image = getattr(config, "RUN_GENERATED_DOCKER_IMAGE", "python:3.11-slim")
                t0 = time.perf_counter()
                # Mount workspace as /app, run candidate.py (no network by default for safety)
                ws_str = str(workspace).replace("\\", "/")
                cmd = f'docker run --rm -v "{ws_str}:/app" -w /app {image} python candidate.py'
                code_out, stdout_str, stderr_str = run_shell(cmd, timeout_s=timeout_s or config.TIMEOUT_S)
                duration = time.perf_counter() - t0
                max_so = max_stdout_chars or config.MAX_STDOUT_CHARS
                max_se = max_stderr_chars or config.MAX_STDERR_CHARS
                return {
                    "stdout": trunc(stdout_str or "", max_so),
                    "stderr": trunc(stderr_str or "", max_se),
                    "returncode": code_out if code_out is not None else -1,
                    "timed_out": False,
                    "duration": round(duration, 4),
                    "log_path": str(log_path),
                }
        except Exception as e:
            pass  # fall through to host execution

    # So candidate code can import generated modules (e.g. from generated.core.error_handler import ErrorHandler)
    project_root = Path(getattr(config, "GENERATED_MODULES_DIR", "generated")).resolve().parent
    py_path = str(project_root)

    t0 = time.perf_counter()
    try:
        proc = subprocess.Popen(
            [str(python_exe), str(script)],
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": py_path},
            text=True,
        )
        stdout_lines = []
        stderr_str = ""
        if stream_stdout and config.STREAM_PROGRAM_OUTPUT:
            with open(log_path, "w", encoding="utf-8") as logf:
                for line in proc.stdout:
                    line = line if line.endswith("\n") else line + "\n"
                    stdout_lines.append(line)
                    print(line, end="", flush=True)
                    logf.write(line)
            try:
                _, stderr_str = proc.communicate(timeout=max(0.1, timeout_s - (time.perf_counter() - t0)))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                stderr_str = "Process timed out"
            stderr_str = stderr_str or ""
        else:
            try:
                stdout_bytes, stderr_str = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                duration = time.perf_counter() - t0
                return {
                    "stdout": "",
                    "stderr": f"Execution timed out after {timeout_s}s",
                    "returncode": -1,
                    "timed_out": True,
                    "duration": duration,
                    "log_path": str(log_path),
                }
            stdout_lines = [stdout_bytes] if stdout_bytes else []
            stderr_str = stderr_str or ""

        duration = time.perf_counter() - t0
        stdout_str = "".join(stdout_lines)
        return {
            "stdout": trunc(stdout_str, max_stdout_chars),
            "stderr": trunc(stderr_str, max_stderr_chars),
            "returncode": proc.returncode or 0,
            "timed_out": False,
            "duration": round(duration, 4),
            "log_path": str(log_path),
        }
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        duration = time.perf_counter() - t0
        return {
            "stdout": "",
            "stderr": f"Execution timed out after {timeout_s}s",
            "returncode": -1,
            "timed_out": True,
            "duration": float(timeout_s),
            "log_path": str(log_path),
        }
    except Exception as e:
        duration = time.perf_counter() - t0
        return {
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "timed_out": False,
            "duration": round(duration, 4),
            "log_path": str(log_path),
        }
