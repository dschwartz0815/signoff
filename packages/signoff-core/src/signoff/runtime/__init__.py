"""Signoff runtime abstraction.

See :mod:`signoff.runtime.base` for the :class:`Runtime` protocol, the
:class:`RuntimePolicy` model, and :class:`VerifierMeta`.
See :mod:`signoff.runtime.local` for the default
:class:`LocalRuntime`.
"""

from __future__ import annotations

from signoff.runtime.base import (
    Runtime,
    RuntimeError,
    RuntimeInfrastructureError,
    RuntimePolicy,
    RuntimePolicyViolationError,
    RuntimeResourceLimitError,
    RuntimeTimeoutError,
    VerifierMeta,
)
from signoff.runtime.local import LocalRuntime

__all__ = [
    "LocalRuntime",
    "Runtime",
    "RuntimeError",
    "RuntimeInfrastructureError",
    "RuntimePolicy",
    "RuntimePolicyViolationError",
    "RuntimeResourceLimitError",
    "RuntimeTimeoutError",
    "VerifierMeta",
]
