"""Code-only enforcement: clean output, AST check, enforce contract by task type."""

import ast
import re

MAIN_WRAPPER = """

if __name__ == "__main__":
    print(evaluate())
"""

def clean_output(raw_text: str) -> str:
    """Remove markdown fences and leading/trailing junk. Return code only."""
    raw = raw_text.strip()
    # Strip ```python ... ``` or ``` ... ``` (take first fenced block only)
    fence = re.compile(r"```(?:\w*)\s*\n?(.*?)```", re.DOTALL)
    m = fence.search(raw)
    if m:
        raw = m.group(1).strip()
    # Drop leading blank/comment lines
    lines = raw.splitlines()
    start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#") or not s:
            continue
        start = i
        break
    raw = "\n".join(lines[start:])
    # Drop trailing prose (lines that don't look like code: no indent, all lowercase words)
    lines = raw.splitlines()
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        s = line.strip()
        if not s:
            continue
        # Keep line if it looks like code: starts with space, or #, or keyword/import/def/class
        if line.startswith((" ", "\t")) or s.startswith(("#", "def ", "class ", "import ", "from ", "if ", "for ", "while ", "try:", "except", "with ", "return ", "print(", "assert ")):
            break
        if re.match(r"^[a-z].*\.$", s) or re.match(r"^[A-Za-z ]+:$", s):
            end = i
        else:
            break
    return "\n".join(lines[:end]).strip()


def get_python_parse_error(code: str) -> str | None:
    """Return None if code parses as valid Python; else return a short error message (e.g. 'line 3: unexpected indent')."""
    try:
        ast.parse(code)
        return None
    except SyntaxError as e:
        return f"line {e.lineno or '?'}: {e.msg or str(e)}"


def is_valid_python(code: str) -> bool:
    """Return True if code parses as valid Python."""
    return get_python_parse_error(code) is None


def enforce_contract(code: str, task_type: str = "code") -> str:
    """
    By task_type: code = require evaluate() + print(evaluate()); server/website/tool = runnable (main or __main__); self_improve not used for execution.
    """
    if task_type == "code":
        if "def evaluate" not in code:
            return code
        if 'if __name__' in code and "print(evaluate()" in code:
            return code
        if "print(evaluate()" not in code and "print(evaluate())" not in code:
            code = code.rstrip() + MAIN_WRAPPER
        return code
    # server, website, tool: no strict contract; script must be runnable as-is
    return code
