"""Image lifecycle management for :class:`DockerRuntime`.

:class:`ImageManager` handles pulls under the configured policy and
(by default) verifies cosign signatures on every image before first
use. Verification results are cached for the harness lifetime.

Cosign is invoked via subprocess. When ``verify_signatures=True`` and
``cosign`` is missing on ``PATH``, :meth:`ensure` raises
:class:`ImageVerificationNotConfiguredError` at prepare time — fail
fast rather than mid-run. When ``verify_signatures=False`` the
constructor logs a WARNING naming the config that opted in.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from signoff_runtime_docker.config import DockerRuntimeConfig
from signoff_runtime_docker.errors import (
    ImageNotFoundError,
    ImageNotTrustedError,
    ImageVerificationNotConfiguredError,
)

__all__ = ["ImageManager"]


_logger = logging.getLogger("signoff_runtime_docker.images")


class ImageManager:
    """Pulls + verifies images on behalf of :class:`DockerRuntime`.

    Constructed once per runtime; the cache of verified tags lives on
    the instance and is cleared on :meth:`invalidate`.
    """

    def __init__(
        self,
        client: Any,
        config: DockerRuntimeConfig,
        *,
        cosign_cmd: str = "cosign",
    ) -> None:
        self._client = client
        self._config = config
        self._cosign_cmd = cosign_cmd
        self._verified: set[str] = set()
        if not config.verify_signatures:
            _logger.warning(
                "ImageManager initialised with verify_signatures=False — "
                "images will be used without cosign verification. This is "
                "unsafe for any image you did not build yourself; set "
                "SIGNOFF_DOCKER_VERIFY_SIGNATURES=true in production."
            )

    async def ensure(self, image: str) -> None:
        """Ensure ``image`` is present locally, pull if policy allows,
        then verify its signature once per session.

        Idempotent: a second call for the same tag short-circuits on
        the verified-cache.
        """
        present = await self._present_locally(image)
        policy = self._config.pull_policy
        if policy == "always":
            await self._pull(image)
        elif policy == "if_not_present":
            if not present:
                await self._pull(image)
        elif policy == "never" and not present:
            raise ImageNotFoundError(
                f"Image {image!r} is not present locally and "
                "pull_policy='never'. Pre-pull the image or "
                "change pull_policy."
            )
        if self._config.verify_signatures:
            await self._verify_once(image)

    def invalidate(self, image: str | None = None) -> None:
        """Drop the verified-cache entry for ``image`` (or all entries)."""
        if image is None:
            self._verified.clear()
        else:
            self._verified.discard(image)

    # -- internals ---------------------------------------------------------

    async def _present_locally(self, image: str) -> bool:
        def _check() -> bool:
            try:
                self._client.images.get(image)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_check)

    async def _pull(self, image: str) -> None:
        def _do_pull() -> None:
            self._client.images.pull(image)

        try:
            await asyncio.to_thread(_do_pull)
        except Exception as exc:
            raise ImageNotFoundError(
                f"Failed to pull image {image!r}: {type(exc).__name__}: {exc}"
            ) from exc
        # A fresh pull invalidates any prior verification.
        self._verified.discard(image)

    async def _verify_once(self, image: str) -> None:
        if image in self._verified:
            return
        await self._verify_signature(image)
        self._verified.add(image)

    async def _verify_signature(self, image: str) -> None:
        """Invoke ``cosign verify`` with the configured identity
        regexp + OIDC issuer. Raises :class:`ImageNotTrustedError`
        on any non-zero exit or parse failure."""
        if shutil.which(self._cosign_cmd) is None:
            raise ImageVerificationNotConfiguredError(
                f"{self._cosign_cmd!r} is not on PATH but "
                "verify_signatures=True. Install cosign "
                "(https://github.com/sigstore/cosign) or set "
                "SIGNOFF_DOCKER_VERIFY_SIGNATURES=false for "
                "locally-built images only."
            )
        cmd = [
            self._cosign_cmd,
            "verify",
            f"--certificate-identity-regexp={self._config.signature_cert_identity_regexp}",
            f"--certificate-oidc-issuer={self._config.signature_cert_oidc_issuer}",
            image,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            raise ImageNotTrustedError(
                f"cosign verify failed for image {image!r} "
                f"(exit {proc.returncode}): "
                f"{stderr_bytes.decode('utf-8', errors='replace').strip()[:400]}"
            )
        _logger.debug(
            "cosign verify OK for %s: %s",
            image,
            stdout_bytes.decode("utf-8", errors="replace").strip()[:200],
        )
