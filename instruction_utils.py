"""Helpers for user instructions: infer task type and normalize for prompts."""

import re


# Keywords that suggest a task type (order matters: more specific first).
TASK_TYPE_HINTS = [
    ("server", ["server", "http server", "api server", "flask", "fastapi", "listen on port", "bind port", "endpoint"]),
    ("website", ["website", "web page", "html page", "landing page", "index.html", "frontend"]),
    ("tool", ["cli", "command line", "tool", "script that reads", "script that takes"]),
    ("simulation", ["simulation", "simulate", "random walk", "physics simulation", "n steps"]),
    ("graphics", ["image", "png", "matplotlib", "pillow", "pil", "plot", "draw"]),
    ("video", ["video", "animation", "gif", "frames", "movie"]),
    ("test", ["test", "pytest", "unit test", "add tests"]),
    ("deploy", ["deploy", "docker", "build script", "production"]),
]


def infer_task_type_from_instruction(instruction: str) -> str:
    """
    Infer task_type from user instruction text so the right contract and prompt suffix are used.
    Returns one of: code, server, website, tool, simulation, graphics, video, test, deploy.
    """
    if not (instruction or "").strip():
        return "code"
    text = instruction.strip().lower()
    for task_type, keywords in TASK_TYPE_HINTS:
        if any(kw in text for kw in keywords):
            return task_type
    return "code"
