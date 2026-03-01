"""
Fleet mode (Phase 6): start multiple Niffi kernels with different config profiles.
Usage: python fleet.py [--profiles dev,quantum,security] [--daemon]
Each kernel runs in a subprocess with NIFFI_PROFILE set. Optional --daemon runs each with --daemon.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

PROFILES = os.environ.get("NIFFI_FLEET_PROFILES", "dev,quantum,security").strip().split(",")
MAIN_PY = Path(__file__).resolve().parent / "main.py"


def main():
    daemon = "--daemon" in sys.argv
    if "--profiles" in sys.argv:
        i = sys.argv.index("--profiles")
        if i + 1 < len(sys.argv):
            PROFILES[:] = sys.argv[i + 1].strip().split(",")
    procs = []
    for profile in PROFILES:
        profile = profile.strip()
        if not profile:
            continue
        env = {**os.environ, "NIFFI_PROFILE": profile}
        cmd = [sys.executable, str(MAIN_PY)]
        if daemon:
            cmd.append("--daemon")
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        procs.append((profile, p))
        print(f"[FLEET] Started {profile} (PID {p.pid})")
    try:
        while procs:
            time.sleep(5)
            alive = []
            for profile, p in procs:
                if p.poll() is None:
                    alive.append((profile, p))
                else:
                    print(f"[FLEET] {profile} exited with {p.returncode}")
            procs = alive
    except KeyboardInterrupt:
        for profile, p in procs:
            p.terminate()
        print("[FLEET] Stopped.")


if __name__ == "__main__":
    main()
