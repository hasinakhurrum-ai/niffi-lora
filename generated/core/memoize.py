"""Small memoization helpers used by generated code.

IMPORTANT: This module must be safe to import during the engine hook load.
No example code should execute at import time.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Dict, Tuple, TypeVar


T = TypeVar("T")


def memoize(func: Callable[..., T]) -> Callable[..., T]:
    cache: Dict[Tuple[Any, ...], T] = {}

    @functools.wraps(func)
    def wrapper(*args: Any) -> T:
        if args not in cache:
            cache[args] = func(*args)
        return cache[args]

    return wrapper


class Memoizer:
    def __init__(self) -> None:
        self.cache: Dict[Tuple[Any, ...], Any] = {}

    def memoize(self, func: Callable[..., T]) -> Callable[..., T]:
        return memoize(func)


__all__ = ["memoize", "Memoizer"]
