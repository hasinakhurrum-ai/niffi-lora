"""
OS and platform detection. Use these for dynamic paths and shell behavior.
"""

import os
import sys
import subprocess
from pathlib import Path

# --- OS ---
IS_WINDOWS = os.name == "nt" or sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_DARWIN = sys.platform == "darwin"
OS_NAME = "windows" if IS_WINDOWS else ("darwin" if IS_DARWIN else "linux")

# --- Venv: Windows uses Scripts/python, Unix uses bin/python ---
VENV_BIN_DIR = "Scripts" if IS_WINDOWS else "bin"
VENV_PYTHON_NAME = "python.exe" if IS_WINDOWS else "python"


def venv_python_path(venv_root: str | Path) -> Path:
    """Path to python inside a venv. Works on Windows and Unix."""
    root = Path(venv_root)
    return root / VENV_BIN_DIR / VENV_PYTHON_NAME


# --- Shell: for running OS commands ---
SHELL = "cmd" if IS_WINDOWS else "sh"
SHELL_FLAG = "/c" if IS_WINDOWS else "-c"


def run_shell_command(
    cmd: str,
    cwd: str | Path | None = None,
    timeout_s: int = 60,
    env_overlay: dict | None = None,
) -> tuple[int, str, str]:
    """
    Run a shell command. Returns (returncode, stdout, stderr).
    Uses cmd /c on Windows, default shell on Unix. cwd is the working directory.
    env_overlay: optional dict merged over os.environ (e.g. {"DOCKER_HOST": "tcp://host:2375"}).
    """
    cwd = str(Path(cwd).resolve()) if cwd else None
    env = {**os.environ, **env_overlay} if env_overlay else None
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["cmd", "/c", cmd],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                shell=False,
                env=env,
            )
        else:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                shell=True,
                env=env,
            )
        return (result.returncode or 0, result.stdout or "", result.stderr or "")
    except subprocess.TimeoutExpired:
        return (-1, "", f"Command timed out after {timeout_s}s")
    except Exception as e:
        return (-1, "", str(e))
