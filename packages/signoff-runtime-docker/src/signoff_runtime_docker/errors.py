"""Exceptions raised by :class:`DockerRuntime` and its helpers.

All subclass :class:`signoff.runtime.base.SignoffRuntimeError` so the
harness catches them uniformly and translates into synthetic
``severity=info`` :class:`VerifierResult` entries per protocol §4.4.
"""

from __future__ import annotations

from signoff.runtime.base import (
    RuntimeInfrastructureError,
    RuntimePolicyViolationError,
)

__all__ = [
    "ContainerStartError",
    "DockerRuntimeNotAvailableError",
    "ExecCwdOutsideWorkspaceError",
    "ImageNotFoundError",
    "ImageNotTrustedError",
    "ImageVerificationNotConfiguredError",
    "WorkspaceNotMountableError",
]


class DockerRuntimeNotAvailableError(RuntimeInfrastructureError):
    """Raised when the Docker daemon can't be reached."""


class ImageNotFoundError(RuntimeInfrastructureError):
    """pull_policy='never' and the image isn't present locally."""


class ImageNotTrustedError(RuntimeInfrastructureError):
    """cosign verify rejected the image's signature."""


class ImageVerificationNotConfiguredError(RuntimeInfrastructureError):
    """verify_signatures=True but cosign isn't installed on PATH."""


class ContainerStartError(RuntimeInfrastructureError):
    """Docker accepted the container spec but the container failed to start."""


class ExecCwdOutsideWorkspaceError(RuntimePolicyViolationError):
    """ctx.exec(cwd=...) pointed at a host path the container can't see."""


class WorkspaceNotMountableError(RuntimeInfrastructureError):
    """ctx.workspace doesn't exist or isn't a directory we can bind-mount."""
