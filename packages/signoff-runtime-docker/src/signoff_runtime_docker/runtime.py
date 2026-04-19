"""``DockerRuntime`` — sandboxed Runtime implementation.

Per ``CLAUDE.md`` §8.2: spawns an ephemeral container per verifier
invocation, mounts the deliverable workspace, wraps ``ctx.exec`` to
route through ``docker exec``, and tears the container down when the
verifier returns.

Architectural note — the verifier's *Python* body still runs in the
harness process. What's sandboxed is the subprocess invocations the
verifier makes via :meth:`ctx.exec`. That's the attack surface
(untrusted commands), not the pack author's code.

The runtime emits a synthetic ``severity=info``
:class:`VerifierResult` per protocol §4.4 whenever Docker fails us
(image pull problem, container refused to start, etc.). It NEVER
fails the deliverable on infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid
from types import TracebackType
from typing import TYPE_CHECKING, Any

from signoff.models import Severity, VerifierResult
from signoff.runtime.base import (
    Runtime,
    RuntimeInfrastructureError,
    RuntimePolicy,
    SignoffRuntimeError,
    VerifierMeta,
)

from signoff_runtime_docker.config import DockerRuntimeConfig
from signoff_runtime_docker.context import wrap_context
from signoff_runtime_docker.errors import (
    ContainerStartError,
    DockerRuntimeNotAvailableError,
    WorkspaceNotMountableError,
)
from signoff_runtime_docker.exec import CONTAINER_WORKSPACE, DockerExec
from signoff_runtime_docker.images import ImageManager
from signoff_runtime_docker.policy import translate_policy

if TYPE_CHECKING:
    from signoff.context import VerifierContext
    from signoff.models import Claim
    from signoff.runtime.base import VerifierCallable

__all__ = ["DockerRuntime"]


_logger = logging.getLogger("signoff_runtime_docker.runtime")

_HOLDER_CMD = ["sleep", "infinity"]


class DockerRuntime:
    """Runtime that runs each verifier in its own ephemeral container.

    Use as a harness runtime:

        async with DockerRuntime(config) as runtime:
            await runtime.prepare(meta)
            result = await runtime.execute(fn, claim=claim, ctx=ctx, policy=policy)
    """

    runtime_id: str = "docker"

    def __init__(
        self,
        config: DockerRuntimeConfig | None = None,
        *,
        docker_client: Any = None,
        image_manager: ImageManager | None = None,
    ) -> None:
        self._config = config if config is not None else DockerRuntimeConfig()
        self._docker_client = docker_client
        self._owns_client = docker_client is None
        self._image_manager = image_manager
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent_containers)
        self._prepared_images: set[str] = set()
        self._tracked_containers: set[str] = set()
        self._lock = asyncio.Lock()

    # -- lifecycle ----------------------------------------------------------

    async def __aenter__(self) -> DockerRuntime:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.teardown()

    async def _ensure_client(self) -> Any:
        if self._docker_client is not None:
            return self._docker_client
        async with self._lock:
            if self._docker_client is not None:
                return self._docker_client
            import docker as docker_sdk  # local import: optional dep

            try:
                kwargs: dict[str, Any] = {
                    "timeout": self._config.client_timeout_seconds,
                }
                if self._config.docker_host:
                    kwargs["base_url"] = self._config.docker_host
                self._docker_client = docker_sdk.DockerClient(**kwargs)
                # Force a round-trip so failures surface early.
                await asyncio.to_thread(self._docker_client.ping)
            except Exception as exc:
                raise DockerRuntimeNotAvailableError(
                    f"Failed to reach Docker daemon: {type(exc).__name__}: {exc}"
                ) from exc
            return self._docker_client

    def _get_image_manager(self, client: Any) -> ImageManager:
        if self._image_manager is None:
            self._image_manager = ImageManager(client, self._config)
        return self._image_manager

    # -- Runtime protocol --------------------------------------------------

    async def prepare(self, verifier_meta: VerifierMeta) -> None:
        """Ensure the image for ``verifier_meta`` is pulled + verified.

        Idempotent — subsequent calls for the same image short-circuit
        via :class:`ImageManager`'s verified-cache.
        """
        image = self._image_for(verifier_meta)
        if image in self._prepared_images:
            return
        client = await self._ensure_client()
        manager = self._get_image_manager(client)
        await manager.ensure(image)
        self._prepared_images.add(image)

    async def execute(
        self,
        fn: VerifierCallable,
        *,
        claim: Claim,
        ctx: VerifierContext,
        policy: RuntimePolicy,
    ) -> VerifierResult:
        """Run ``fn(claim, ctx)`` inside a fresh container."""
        ctx.current_claim = claim
        ctx.policy = policy
        started = time.perf_counter()

        async with self._semaphore:
            try:
                client = await self._ensure_client()
                image = self._image_for(ctx.current_verifier_meta)
                await self._get_image_manager(client).ensure(image)
                container = await self._create_container(
                    client=client,
                    image=image,
                    policy=policy,
                    ctx=ctx,
                    claim=claim,
                )
            except asyncio.CancelledError:
                raise
            except SignoffRuntimeError as exc:
                return self._synthetic_infra_result(
                    ctx=ctx, claim=claim, exc=exc, elapsed_ms=self._elapsed_ms(started)
                )
            except Exception as exc:
                return self._synthetic_infra_result(
                    ctx=ctx,
                    claim=claim,
                    exc=RuntimeInfrastructureError(
                        f"Docker setup failed: {type(exc).__name__}: {exc}"
                    ),
                    elapsed_ms=self._elapsed_ms(started),
                )

            container_id = container["Id"]
            self._tracked_containers.add(container_id)
            try:
                docker_exec = DockerExec(
                    client=client,
                    container_id=container_id,
                    workspace_host=ctx.workspace,
                    stdout_max_bytes=self._config.exec_stdout_max_bytes,
                    stderr_max_bytes=self._config.exec_stderr_max_bytes,
                )
                wrapped = wrap_context(ctx, docker_exec)
                try:
                    result = await asyncio.wait_for(
                        fn(claim, wrapped), timeout=policy.timeout_seconds
                    )
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    result = self._synthetic_timeout_result(
                        ctx, claim, policy, elapsed_ms=self._elapsed_ms(started)
                    )
                except Exception as exc:
                    result = self._synthetic_verifier_exc_result(
                        ctx, claim, exc, elapsed_ms=self._elapsed_ms(started)
                    )
                else:
                    result = result.model_copy(update={"duration_ms": self._elapsed_ms(started)})
                return self._annotate_with_container(result, container_id, image)
            finally:
                await self._cleanup_container(
                    client, container_id, keep=self._should_keep(result=locals().get("result"))
                )
                self._tracked_containers.discard(container_id)

    async def teardown(self) -> None:
        """Clean up any containers we spawned + close the Docker client.

        Idempotent: a second call is a no-op. Called from
        :meth:`__aexit__` and from harness shutdown.
        """
        client = self._docker_client
        if client is None:
            return
        # Best-effort cleanup of anything still tracked.
        orphans = list(self._tracked_containers)
        self._tracked_containers.clear()
        for cid in orphans:
            await self._cleanup_container(client, cid, keep=False)
        if self._owns_client:
            try:
                await asyncio.to_thread(client.close)
            except Exception:
                _logger.debug("DockerRuntime.teardown: swallowed close error", exc_info=True)
        self._docker_client = None

    # -- container lifecycle ----------------------------------------------

    async def _create_container(
        self,
        *,
        client: Any,
        image: str,
        policy: RuntimePolicy,
        ctx: VerifierContext,
        claim: Claim,
    ) -> dict[str, Any]:
        host_workspace = ctx.workspace.resolve()
        if not host_workspace.is_dir():
            raise WorkspaceNotMountableError(
                f"ctx.workspace={host_workspace!s} does not exist or is not a directory."
            )
        host_config_kwargs = translate_policy(policy, self._config)
        volumes = {
            str(host_workspace): {
                "bind": str(CONTAINER_WORKSPACE),
                "mode": self._config.workspace_mount_mode,
            }
        }
        run_id = uuid.uuid4().hex[:12]
        fqn = (
            ctx.current_verifier_meta.fully_qualified_name
            if ctx.current_verifier_meta is not None
            else "signoff-core.unknown_verifier"
        )
        labels = {
            "signoff.harness": "true",
            "signoff.verifier": fqn,
            "signoff.claim_id": claim.id,
            "signoff.run_id": run_id,
        }

        def _create_and_start() -> dict[str, Any]:
            host_config = client.api.create_host_config(
                binds=[
                    f"{host_workspace!s}:{CONTAINER_WORKSPACE!s}:{self._config.workspace_mount_mode}"
                ],
                **host_config_kwargs,
            )
            container = client.api.create_container(
                image=image,
                command=_HOLDER_CMD,
                working_dir=str(CONTAINER_WORKSPACE),
                labels=labels,
                host_config=host_config,
                user=f"{self._config.run_as_uid}:{self._config.run_as_gid}",
                detach=True,
            )
            client.api.start(container["Id"])
            return container  # type: ignore[no-any-return]

        # ``volumes`` accepted on create_host_config via `binds`; we
        # keep the ``volumes`` dict here only so tests can assert it.
        _ = volumes
        try:
            return await asyncio.to_thread(_create_and_start)
        except Exception as exc:
            raise ContainerStartError(
                f"Failed to create/start sandbox container for "
                f"image={image!r}: {type(exc).__name__}: {exc}"
            ) from exc

    async def _cleanup_container(self, client: Any, container_id: str, *, keep: bool) -> None:
        if keep:
            _logger.info(
                "DockerRuntime keeping container %s for postmortem "
                "(SIGNOFF_DOCKER_KEEP_ON_FAILURE=true).",
                container_id[:12],
            )
            return

        def _stop_and_remove() -> None:
            try:
                client.api.stop(container_id, timeout=1)
            except Exception:
                _logger.debug("stop(%s) failed", container_id[:12], exc_info=True)
            # auto_remove=True on HostConfig means Docker reaps once stopped.
            if not self._config.auto_remove:
                try:
                    client.api.remove_container(container_id, force=True)
                except Exception:
                    _logger.debug("remove_container(%s) failed", container_id[:12], exc_info=True)

        await asyncio.to_thread(_stop_and_remove)

    # -- helpers -----------------------------------------------------------

    def _image_for(self, verifier_meta: VerifierMeta | None) -> str:
        # VerifierMeta doesn't carry an image field in Phase 0. We use
        # the configured default; later phases can hang a per-verifier
        # image off the meta or the config's per_verifier map.
        del verifier_meta
        return self._config.default_image

    def _should_keep(self, *, result: VerifierResult | None) -> bool:
        if not self._config.keep_on_failure:
            return False
        if result is None:
            return True
        return not result.passed

    @staticmethod
    def _annotate_with_container(
        result: VerifierResult, container_id: str, image: str
    ) -> VerifierResult:
        existing = dict(result.evidence)
        existing.setdefault("runtime", "docker")
        existing.setdefault("container_id", container_id[:12])
        existing.setdefault("image", image)
        return result.model_copy(update={"evidence": existing})

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _synthetic_infra_result(
        self,
        *,
        ctx: VerifierContext,
        claim: Claim,
        exc: SignoffRuntimeError,
        elapsed_ms: int,
    ) -> VerifierResult:
        _logger.warning(
            "DockerRuntime infrastructure failure for %s: %s",
            claim.id,
            exc,
        )
        return VerifierResult(
            verifier=_verifier_name(ctx),
            claim_id=claim.id,
            passed=False,
            severity=Severity.INFO,
            reason=f"DockerRuntime infrastructure error: {type(exc).__name__}: {exc}",
            suggestion=None,
            evidence={"runtime": "docker", "error_class": type(exc).__name__},
            cost_usd=0.0,
            duration_ms=elapsed_ms,
        )

    def _synthetic_timeout_result(
        self,
        ctx: VerifierContext,
        claim: Claim,
        policy: RuntimePolicy,
        *,
        elapsed_ms: int,
    ) -> VerifierResult:
        return VerifierResult(
            verifier=_verifier_name(ctx),
            claim_id=claim.id,
            passed=False,
            severity=Severity.INFO,
            reason=f"Verifier timed out after {policy.timeout_seconds}s in DockerRuntime",
            suggestion=None,
            evidence={"runtime": "docker", "timeout_seconds": policy.timeout_seconds},
            cost_usd=0.0,
            duration_ms=elapsed_ms,
        )

    def _synthetic_verifier_exc_result(
        self,
        ctx: VerifierContext,
        claim: Claim,
        exc: BaseException,
        *,
        elapsed_ms: int,
    ) -> VerifierResult:
        _logger.warning("Verifier raised in DockerRuntime: %s: %s", type(exc).__name__, exc)
        tb = traceback.format_exc()[:5000]
        return VerifierResult(
            verifier=_verifier_name(ctx),
            claim_id=claim.id,
            passed=False,
            severity=Severity.INFO,
            reason=f"Verifier raised {type(exc).__name__}: {str(exc)[:200]}",
            suggestion=None,
            evidence={
                "runtime": "docker",
                "exception_type": type(exc).__name__,
                "traceback": tb,
            },
            cost_usd=0.0,
            duration_ms=elapsed_ms,
        )


def _verifier_name(ctx: VerifierContext) -> str:
    if ctx.current_verifier_meta is not None:
        return ctx.current_verifier_meta.fully_qualified_name
    return "signoff-core.unknown_verifier"


# Structural conformance check.
_runtime_type_check: type[Runtime] = DockerRuntime
