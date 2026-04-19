"""Unit tests for :class:`ImageManager`.

The Docker SDK is mocked (``client.images.get`` / ``client.images.pull``)
and :meth:`ImageManager._verify_signature` is patched per-test so the
tests exercise the pull-policy matrix and the cosign-gate behaviour
without a daemon or the real ``cosign`` binary on PATH.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from signoff_runtime_docker import (
    DockerRuntimeConfig,
    ImageManager,
    ImageNotFoundError,
    ImageNotTrustedError,
    ImageVerificationNotConfiguredError,
)


def _client(*, present: bool, pull_raises: Exception | None = None) -> Any:
    client = MagicMock()
    client.images = MagicMock()
    if present:
        client.images.get.return_value = MagicMock()
    else:
        client.images.get.side_effect = Exception("ImageNotFound")
    if pull_raises is not None:
        client.images.pull.side_effect = pull_raises
    else:
        client.images.pull.return_value = MagicMock()
    return client


def _cfg(**overrides: Any) -> DockerRuntimeConfig:
    base: dict[str, Any] = {"verify_signatures": False}
    base.update(overrides)
    return DockerRuntimeConfig(**base)


# ---------------------------------------------------------------------------
# Pull policy
# ---------------------------------------------------------------------------


async def test_if_not_present_pulls_when_missing() -> None:
    client = _client(present=False)
    mgr = ImageManager(client, _cfg(pull_policy="if_not_present"))
    await mgr.ensure("sig/x:1")
    assert client.images.pull.call_count == 1


async def test_if_not_present_skips_when_present() -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(pull_policy="if_not_present"))
    await mgr.ensure("sig/x:1")
    assert client.images.pull.call_count == 0


async def test_always_pulls_even_when_present() -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(pull_policy="always"))
    await mgr.ensure("sig/x:1")
    assert client.images.pull.call_count == 1


async def test_never_raises_when_missing() -> None:
    client = _client(present=False)
    mgr = ImageManager(client, _cfg(pull_policy="never"))
    with pytest.raises(ImageNotFoundError, match="pull_policy='never'"):
        await mgr.ensure("sig/x:1")


async def test_never_succeeds_when_present() -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(pull_policy="never"))
    await mgr.ensure("sig/x:1")
    assert client.images.pull.call_count == 0


async def test_pull_failure_wraps_to_image_not_found() -> None:
    client = _client(present=False, pull_raises=RuntimeError("registry 500"))
    mgr = ImageManager(client, _cfg(pull_policy="if_not_present"))
    with pytest.raises(ImageNotFoundError, match="registry 500"):
        await mgr.ensure("sig/x:1")


# ---------------------------------------------------------------------------
# Signature verification gate
# ---------------------------------------------------------------------------


async def test_verify_signature_invoked_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(verify_signatures=True, pull_policy="if_not_present"))
    called_with: list[str] = []

    async def fake_verify(image: str) -> None:
        called_with.append(image)

    monkeypatch.setattr(mgr, "_verify_signature", fake_verify)
    await mgr.ensure("sig/x:1")
    assert called_with == ["sig/x:1"]


async def test_verify_once_per_image_in_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(verify_signatures=True, pull_policy="if_not_present"))
    verify = AsyncMock()
    monkeypatch.setattr(mgr, "_verify_signature", verify)
    await mgr.ensure("sig/x:1")
    await mgr.ensure("sig/x:1")
    assert verify.await_count == 1


async def test_invalidate_forces_reverify(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(verify_signatures=True, pull_policy="if_not_present"))
    verify = AsyncMock()
    monkeypatch.setattr(mgr, "_verify_signature", verify)
    await mgr.ensure("sig/x:1")
    mgr.invalidate("sig/x:1")
    await mgr.ensure("sig/x:1")
    assert verify.await_count == 2


async def test_fresh_pull_invalidates_prior_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(verify_signatures=True, pull_policy="always"))
    verify = AsyncMock()
    monkeypatch.setattr(mgr, "_verify_signature", verify)
    await mgr.ensure("sig/x:1")
    # pull_policy=always → another pull → re-verify is required.
    await mgr.ensure("sig/x:1")
    assert verify.await_count == 2


async def test_skip_verify_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(verify_signatures=False))
    verify = AsyncMock()
    monkeypatch.setattr(mgr, "_verify_signature", verify)
    await mgr.ensure("sig/x:1")
    assert verify.await_count == 0


async def test_missing_cosign_raises_at_verify_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(present=True)
    mgr = ImageManager(
        client,
        _cfg(verify_signatures=True, pull_policy="if_not_present"),
        cosign_cmd="cosign-does-not-exist-xyz",
    )
    # shutil.which("cosign-does-not-exist-xyz") returns None on the real
    # system — no monkeypatch needed, but ensure we don't accidentally
    # pick up something in PATH.
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _c: None)
    with pytest.raises(ImageVerificationNotConfiguredError, match="cosign"):
        await mgr.ensure("sig/x:1")


async def test_cosign_verify_nonzero_exit_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(verify_signatures=True, pull_policy="if_not_present"))
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _c: "/usr/local/bin/cosign")

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"error: identity mismatch"

    async def fake_create_subprocess_exec(*_cmd: str, **_kw: Any) -> _FakeProc:
        return _FakeProc()

    import asyncio

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    with pytest.raises(ImageNotTrustedError, match="identity mismatch"):
        await mgr.ensure("sig/x:1")


async def test_cosign_verify_success_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(present=True)
    mgr = ImageManager(client, _cfg(verify_signatures=True, pull_policy="if_not_present"))
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _c: "/usr/local/bin/cosign")

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"verified", b""

    invocations = 0

    async def fake_create_subprocess_exec(*_cmd: str, **_kw: Any) -> _FakeProc:
        nonlocal invocations
        invocations += 1
        return _FakeProc()

    import asyncio

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    await mgr.ensure("sig/x:1")
    await mgr.ensure("sig/x:1")
    assert invocations == 1  # cached


# ---------------------------------------------------------------------------
# Startup warning for verify_signatures=False
# ---------------------------------------------------------------------------


def test_verify_disabled_logs_warning_at_construction(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="signoff_runtime_docker.images"):
        ImageManager(MagicMock(), _cfg(verify_signatures=False))
    assert any("verify_signatures=False" in rec.message for rec in caplog.records)
