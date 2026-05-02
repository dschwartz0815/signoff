"""``DockerRuntimeConfig`` — loaded from ``SIGNOFF_DOCKER_*`` env vars.

The ``SIGNOFF_DOCKER_`` namespace is reserved in
``docs/configuration.md`` for this package. Every field carries a
safe-by-default value: network off, workspace read-only, rootfs
read-only, non-root UID, strict PID/memory/CPU defaults, cosign
signature verification on. A verifier that needs looser constraints
asks explicitly via :class:`signoff.runtime.RuntimePolicy`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["DockerRuntimeConfig"]


class DockerRuntimeConfig(BaseSettings):
    """Configuration for :class:`DockerRuntime`.

    Loaded from ``SIGNOFF_DOCKER_*`` env vars by default; pass an
    explicit instance to :class:`DockerRuntime` to override.
    """

    model_config = SettingsConfigDict(
        env_prefix="SIGNOFF_DOCKER_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # -------------------------- Connection -----------------------------------

    #: Override the Docker socket URL. ``None`` uses the SDK default
    #: (``/var/run/docker.sock`` on Linux, ``DOCKER_HOST`` when set).
    docker_host: str | None = None
    client_timeout_seconds: float = Field(default=30.0, gt=0.0)

    # -------------------------- Image policy ---------------------------------

    default_image: str = "ghcr.io/dschwartz0815/signoff/generic-sandbox:latest"
    pull_policy: Literal["always", "if_not_present", "never"] = "if_not_present"
    verify_signatures: bool = True
    #: Must match :literal:`cosign verify --certificate-identity-regexp`
    #: exactly — narrow this down in production.
    signature_cert_identity_regexp: str = r"^https://github\.com/signoff/"
    signature_cert_oidc_issuer: str = "https://token.actions.githubusercontent.com"

    # -------------------------- Resource defaults ----------------------------

    default_cpu_limit: float = Field(default=2.0, gt=0.0)
    default_memory_limit_mb: int = Field(default=1024, ge=64)
    default_pids_limit: int = Field(default=256, ge=16)
    default_timeout_seconds: int = Field(default=60, ge=1)

    # -------------------------- Network --------------------------------------

    default_network: Literal["none", "bridge", "allowlist"] = "none"
    network_allowlist_hosts: list[str] = Field(default_factory=list)

    # -------------------------- Filesystem -----------------------------------

    workspace_mount_mode: Literal["ro", "rw"] = "ro"
    tmpfs_size_mb: int = Field(default=256, ge=16)
    read_only_rootfs: bool = True

    # -------------------------- User / cleanup -------------------------------

    run_as_uid: int = Field(default=10001, ge=1)
    run_as_gid: int = Field(default=10001, ge=1)

    auto_remove: bool = True
    #: Leave failing containers around for postmortem. Operators should
    #: keep this ``False`` in any long-running deployment — the orphans
    #: pile up fast.
    keep_on_failure: bool = False

    # -------------------------- Concurrency ----------------------------------

    max_concurrent_containers: int = Field(default=8, ge=1)

    # -------------------------- Exec output caps -----------------------------

    #: Per-stream byte cap for ``ctx.exec`` stdout/stderr. Anything
    #: beyond this is truncated with a marker in the returned
    #: :class:`ExecResult`.
    exec_stdout_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    exec_stderr_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
