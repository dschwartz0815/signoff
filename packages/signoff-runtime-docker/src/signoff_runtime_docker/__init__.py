"""Docker-backed sandbox Runtime for Signoff verifiers.

Implements :class:`signoff.runtime.Runtime` per ``CLAUDE.md`` §8.2.
Drop-in replacement for :class:`signoff.runtime.LocalRuntime` when
verifiers need to execute untrusted content.

Import path convention (mirrors ``signoff_http`` / ``signoff_judge``):
pip ``signoff-runtime-docker`` → module ``signoff_runtime_docker``.
"""

from __future__ import annotations

__version__ = "0.0.1"

from signoff_runtime_docker.config import DockerRuntimeConfig
from signoff_runtime_docker.errors import (
    ContainerStartError,
    DockerRuntimeNotAvailableError,
    ExecCwdOutsideWorkspaceError,
    ImageNotFoundError,
    ImageNotTrustedError,
    ImageVerificationNotConfiguredError,
    WorkspaceNotMountableError,
)

__all__ = [
    "ContainerStartError",
    "DockerRuntimeConfig",
    "DockerRuntimeNotAvailableError",
    "ExecCwdOutsideWorkspaceError",
    "ImageNotFoundError",
    "ImageNotTrustedError",
    "ImageVerificationNotConfiguredError",
    "WorkspaceNotMountableError",
    "__version__",
]
