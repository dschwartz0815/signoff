"""Calculator with a broken import that smoke_imports catches."""

from __future__ import annotations

# Module doesn't exist — top-level evaluation fails at import time.
import this_module_does_not_exist  # noqa: F401


def add(a: int, b: int) -> int:
    return a + b
