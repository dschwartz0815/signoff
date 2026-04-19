"""Translate :class:`RuntimePolicy` into Docker create-container kwargs.

One function, :func:`translate_policy`, so callers have a single
place to reason about "which policy field maps to which Docker
option". Keep this file pure: no ``docker`` SDK imports, no I/O —
that way the translation table is exhaustively testable.
"""

from __future__ import annotations

import logging
from typing import Any

from signoff.runtime.base import RuntimePolicy

from signoff_runtime_docker.config import DockerRuntimeConfig

__all__ = ["NETWORK_ALLOWLIST_DOWNGRADE_WARNING", "translate_policy"]


_logger = logging.getLogger("signoff_runtime_docker.policy")


#: Emitted once when a policy asks for ``network="allowlist"``. The
#: runtime downgrades to ``bridge`` (no egress filtering) in Phase 1
#: and logs this once so operators know the filter isn't on.
#: Tracked by the "allowlist network mode" follow-up.
NETWORK_ALLOWLIST_DOWNGRADE_WARNING = (
    "RuntimePolicy.network='allowlist' is not yet implemented; "
    "falling back to 'bridge' (unrestricted egress). Follow the "
    "allowlist-network-mode tracking issue for the DNS-filter work."
)


_ALLOWLIST_WARNED = False


def translate_policy(policy: RuntimePolicy, config: DockerRuntimeConfig) -> dict[str, Any]:
    """Return kwargs for ``docker.DockerClient.containers.create``.

    Policy values override config defaults where both are present.
    Produces the secure-by-default baseline: no capabilities, no
    new-privileges, read-only rootfs, tmpfs-backed /tmp, non-root
    user, strict PID + memory + CPU caps.
    """
    mem_bytes = policy.memory_limit_bytes or config.default_memory_limit_mb * 1024 * 1024
    cpu_cores = policy.cpu_limit if policy.cpu_limit is not None else config.default_cpu_limit
    network_mode = _network_mode(policy, config)
    return {
        "mem_limit": f"{mem_bytes}b",
        "nano_cpus": int(cpu_cores * 1e9),
        "pids_limit": config.default_pids_limit,
        "network_mode": network_mode,
        "cap_drop": ["ALL"],
        "cap_add": [],
        "security_opt": ["no-new-privileges"],
        "read_only": config.read_only_rootfs,
        "tmpfs": {"/tmp": f"rw,size={config.tmpfs_size_mb}m"},
        "user": f"{config.run_as_uid}:{config.run_as_gid}",
        "auto_remove": config.auto_remove,
    }


def _network_mode(policy: RuntimePolicy, config: DockerRuntimeConfig) -> str:
    """Resolve ``policy.network`` to a Docker ``network_mode`` string.

    ``RuntimePolicy.network`` uses the protocol vocabulary
    (``"none" | "allowlist" | "open"``); ``DockerRuntimeConfig.default_network``
    uses the runtime vocabulary (``"none" | "bridge" | "allowlist"``).
    The policy wins when set to anything other than the default
    ``"open"``.
    """
    global _ALLOWLIST_WARNED
    chosen: str
    if policy.network == "none":
        chosen = "none"
    elif policy.network == "allowlist":
        chosen = "allowlist"
    else:
        # policy.network == "open" → use the config default.
        chosen = config.default_network
    if chosen == "allowlist":
        if not _ALLOWLIST_WARNED:
            _logger.warning(NETWORK_ALLOWLIST_DOWNGRADE_WARNING)
            _ALLOWLIST_WARNED = True
        return "bridge"
    return chosen
