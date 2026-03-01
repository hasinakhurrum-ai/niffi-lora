"""
Quantum realm: on_cycle() runs a small circuit and writes state/quantum_state.json.
Use the result for tie-breaking or weighting in scheduling. Requires QUANTUM_ENABLED and qiskit.
"""

import json
import os
from datetime import datetime

def on_cycle() -> None:
    try:
        import config
    except ImportError:
        return
    if not getattr(config, "QUANTUM_ENABLED", False):
        return
    state_dir = getattr(config, "STATE_DIR", "state")
    path = os.path.join(state_dir, "quantum_state.json")
    try:
        from tools import run_quantum_circuit
        # Minimal 2-qubit circuit: H on 0, CX 0->1, measure both
        qasm = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0],q[1];
measure q -> c;
"""
        out = run_quantum_circuit(qasm, shots=256)
        if out.startswith("["):
            state = {"available": False, "error": out, "ts": datetime.utcnow().isoformat() + "Z"}
        else:
            counts = json.loads(out)
            # Single number for tie-breaker: sum of (key as int) * count
            tie = 0
            for k, v in counts.items():
                try:
                    tie = (tie + int(k, 2) * v) % 1000
                except Exception:
                    pass
            state = {"available": True, "counts": counts, "tie_breaker": tie, "ts": datetime.utcnow().isoformat() + "Z"}
        os.makedirs(state_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        try:
            os.makedirs(state_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"available": False, "error": str(e)[:200], "ts": datetime.utcnow().isoformat() + "Z"}, f)
        except Exception:
            pass
