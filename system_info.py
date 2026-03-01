"""
Gather hardware and software info for the host so the LLM knows the system it runs on.
Uses stdlib first; psutil if available for richer data.
"""

import os
import platform
import sys
import subprocess
from pathlib import Path


def _cpu_count() -> int:
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def _memory_info() -> tuple[str, str]:
    """Return (total_mb_str, available_mb_str) or ('?', '?')."""
    try:
        import psutil
        v = psutil.virtual_memory()
        total_mb = v.total // (1024 * 1024)
        avail_mb = v.available // (1024 * 1024)
        return (str(total_mb), str(avail_mb))
    except ImportError:
        pass
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["wmic", "OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/Value"],
                timeout=5,
                text=True,
            )
            total = free = 0
            for line in out.splitlines():
                if "TotalVisibleMemorySize" in line:
                    total = int(line.split("=")[-1].strip())
                elif "FreePhysicalMemory" in line:
                    free = int(line.split("=")[-1].strip())
            if total > 0:
                return (str(total // 1024), str(free // 1024))
        except Exception:
            pass
    return ("?", "?")


def _disk_info() -> str:
    try:
        import psutil
        d = psutil.disk_usage("/" if not os.name == "nt" else "C:\\")
        total_gb = d.total // (1024 ** 3)
        free_gb = d.free // (1024 ** 3)
        return f"total={total_gb}GB free={free_gb}GB"
    except Exception:
        pass
    return "?"


def _gpu_info() -> str:
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                timeout=5,
                text=True,
            )
            lines = [x.strip() for x in out.splitlines() if x.strip() and x.strip() != "Name"]
            if lines:
                return "; ".join(lines[:3])
    except Exception:
        pass
    return "?"


def get_system_info_dict() -> dict:
    """Return a dict with OS, hardware, and software info."""
    total_mb, avail_mb = _memory_info()
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "?",
        "python_version": sys.version.split()[0],
        "python_impl": platform.python_implementation(),
        "cpu_count": _cpu_count(),
        "memory_total_mb": total_mb,
        "memory_available_mb": avail_mb,
        "disk": _disk_info(),
        "gpu": _gpu_info(),
        "cwd": str(Path.cwd()),
        "hostname": platform.node(),
    }


def get_system_info_text() -> str:
    """Format system info for the LLM (injected into self_description)."""
    d = get_system_info_dict()
    lines = [
        "--- HOST (this machine) ---",
        f"OS: {d['os']} {d['os_release']} ({d['machine']})",
        f"Python: {d['python_version']} ({d['python_impl']})",
        f"CPU: {d['cpu_count']} cores, processor: {d['processor'][:60]}",
        f"Memory: total={d['memory_total_mb']} MB, available={d['memory_available_mb']} MB",
        f"Disk: {d['disk']}",
        f"GPU: {d['gpu']}",
        f"Hostname: {d['hostname']}",
        f"CWD: {d['cwd']}",
        "",
        "The engine can run OS commands via tools.run_shell(cmd, cwd, timeout). Use this to run scripts, list dirs, or call any system command available on this OS.",
    ]
    return "\n".join(lines)
