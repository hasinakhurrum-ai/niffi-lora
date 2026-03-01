"""Extract numeric score from stdout; apply penalties for errors/time."""

# Penalty when returncode != 0 or timed_out
FAIL_SCORE = -1e9
# Penalty per non-empty stderr (subtracted from raw score)
STDERR_PENALTY = 0.1
# Penalty per second over 1s (optional)
DURATION_PENALTY_PER_SEC = 0.01


def extract_numeric(stdout: str) -> float | None:
    """Try to get a single float from stdout (last non-empty line preferred)."""
    lines = [s.strip() for s in (stdout or "").strip().splitlines() if s.strip()]
    if not lines:
        return None
    for line in reversed(lines):
        try:
            return float(line)
        except ValueError:
            continue
    return None


def compute_score(result: dict, task_type: str = "code") -> tuple[float, bool]:
    """
    Compute final score from sandbox result.
    code: numeric from stdout, with penalties. server/website/tool: valid if rc==0 and not timed_out, score 1.0.
    """
    rc = result.get("returncode", -1)
    timed_out = result.get("timed_out", False)
    stderr = (result.get("stderr") or "").strip()
    stdout = result.get("stdout") or ""
    duration = result.get("duration") or 0.0

    if timed_out or rc != 0:
        return (FAIL_SCORE, False)

    if task_type in ("server", "website", "tool", "simulation", "graphics", "video"):
        return (1.0, True)

    raw = extract_numeric(stdout)
    if raw is None:
        return (FAIL_SCORE, False)

    score = raw
    if stderr:
        score -= STDERR_PENALTY
    if duration > 1.0:
        score -= (duration - 1.0) * DURATION_PENALTY_PER_SEC

    return (score, True)
