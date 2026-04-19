"""Signoff coding-verifier pack.

Five verifiers for Python code changes. Consumed via the harness
entry-point system (see :mod:`signoff.registry`); direct imports
exist for tests and ad-hoc callers.
"""

from __future__ import annotations

__version__ = "0.0.1"

from signoff_code.deliverable import BaseReference, CodeChangeDeliverable
from signoff_code.workspace import MAX_CHANGE_BYTES, Workspace, WorkspaceError

__all__ = [
    "MAX_CHANGE_BYTES",
    "BaseReference",
    "CodeChangeDeliverable",
    "Workspace",
    "WorkspaceError",
    "__version__",
]
