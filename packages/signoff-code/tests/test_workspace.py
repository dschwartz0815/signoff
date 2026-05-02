"""Unit tests for :class:`Workspace`."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from signoff.testing import FakeHttpClient
from signoff_code import (
    BaseReference,
    CodeChangeDeliverable,
    Workspace,
    WorkspaceError,
)


def _write_base(tmp_path: Path) -> Path:
    """Create a fake base-revision directory with one file."""
    base = tmp_path / "base"
    base.mkdir()
    (base / "x.py").write_text("x = 1\n")
    return base


async def test_materialize_files_without_base(tmp_path: Path) -> None:
    d = CodeChangeDeliverable(intent="new module", files={"hello.py": "print('hi')\n"})
    async with await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path) as ws:
        assert (ws.root / "hello.py").read_text() == "print('hi')\n"
        assert d.changed_paths == ["hello.py"]


async def test_materialize_files_nested_path(tmp_path: Path) -> None:
    d = CodeChangeDeliverable(intent="nested", files={"pkg/mod.py": "Y = 2\n"})
    async with await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path) as ws:
        assert (ws.root / "pkg" / "mod.py").read_text() == "Y = 2\n"


async def test_materialize_diff_applies_against_local_base(tmp_path: Path) -> None:
    base = _write_base(tmp_path)
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    d = CodeChangeDeliverable(
        intent="bump x",
        base=BaseReference(kind="local_path", value=str(base)),
        diff=diff,
    )
    async with await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path) as ws:
        assert (ws.root / "x.py").read_text() == "x = 2\n"
        assert d.changed_paths == ["x.py"]


async def test_materialize_bad_diff_raises_workspace_error(tmp_path: Path) -> None:
    base = _write_base(tmp_path)
    bogus = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-does-not-match\n+replacement\n"
    d = CodeChangeDeliverable(
        intent="nope",
        base=BaseReference(kind="local_path", value=str(base)),
        diff=bogus,
    )
    with pytest.raises(WorkspaceError, match="Failed to apply diff"):
        await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path)


async def test_diff_without_base_rejected(tmp_path: Path) -> None:
    d = CodeChangeDeliverable(intent="bad", diff="--- a\n+++ b\n@@\n")
    with pytest.raises(WorkspaceError, match="requires a `base`"):
        await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path)


async def test_size_cap_enforced(tmp_path: Path) -> None:
    huge = "x" * 200
    d = CodeChangeDeliverable(intent="big", files={"big.txt": huge})
    with pytest.raises(WorkspaceError, match="bytes; max is"):
        await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path, max_bytes=100)


async def test_local_path_base_missing_raises(tmp_path: Path) -> None:
    d = CodeChangeDeliverable(
        intent="x",
        base=BaseReference(kind="local_path", value=str(tmp_path / "nope")),
        diff="--- a\n+++ b\n@@\n",
    )
    with pytest.raises(WorkspaceError, match="does not exist"):
        await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path)


async def test_cleanup_removes_temp_dir(tmp_path: Path) -> None:
    d = CodeChangeDeliverable(intent="x", files={"a": "a"})
    ws = await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path)
    root = ws.root
    assert root.exists()
    await ws.cleanup()
    assert not root.exists()
    # Idempotent: second cleanup is a no-op.
    await ws.cleanup()


async def test_cleanup_skips_git_dirs_when_copying_base(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / ".git").mkdir()
    (base / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (base / "keep.py").write_text("keep = 1\n")
    d = CodeChangeDeliverable(
        intent="copy",
        base=BaseReference(kind="local_path", value=str(base)),
        files={"added.py": "added = 1\n"},
    )
    async with await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path) as ws:
        assert (ws.root / "keep.py").exists()
        assert (ws.root / "added.py").exists()
        assert not (ws.root / ".git").exists()


async def test_git_sha_base_raises_clear_error(tmp_path: Path) -> None:
    d = CodeChangeDeliverable(
        intent="x",
        base=BaseReference(kind="git_sha", value="deadbeef"),
        diff="--- a\n+++ b\n@@\n",
    )
    with pytest.raises(WorkspaceError, match="git_sha base"):
        await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path)


# ---------------------------------------------------------------------------
# F9 regression: workspace must be readable from the sandbox's non-root UID.
# DockerRuntime bind-mounts ``ws.root`` into a container running as
# UID 10001 (signoff:signoff per the published code-sandbox image). The
# old materialisation path left ``mkdtemp``-created dirs at 0o700 and
# ``shutil.copy2``-copied files at whatever mode the source carried,
# which made the tree opaque to the sandbox UID on Linux. Docker Desktop
# on macOS hides the issue with VM UID translation, so we explicitly
# skip these tests on platforms where POSIX mode bits don't apply
# (Windows) and assert against the host filesystem's view directly.
# ---------------------------------------------------------------------------


pytestmark_posix = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits are not enforced on Windows",
)


@pytestmark_posix
async def test_materialize_root_is_world_traversable(tmp_path: Path) -> None:
    """Root tempdir must have the ``o+x`` bit set so a non-root
    container UID can ``cd`` into it. Without this, every verifier
    in the pack fails with ``Permission denied`` on Linux."""
    d = CodeChangeDeliverable(intent="x", files={"a.py": "a = 1\n"})
    async with await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path) as ws:
        mode = stat.S_IMODE(os.stat(ws.root).st_mode)
        assert mode & 0o001, f"root mode {oct(mode)} missing world-traverse"
        assert mode & 0o004, f"root mode {oct(mode)} missing world-read"


@pytestmark_posix
async def test_materialize_files_are_world_readable(tmp_path: Path) -> None:
    """Every file written into the workspace — by the ``files`` map,
    by ``_copy_tree`` from a base, or by ``patch`` applying a diff —
    must end up with ``o+r`` so the sandbox UID can read it."""
    base = tmp_path / "base"
    base.mkdir()
    (base / "seed.py").write_text("seed = 1\n")
    # Belt-and-braces: lock the seed down to owner-only the way a
    # carefully-permissioned checkout might. The fix has to override.
    os.chmod(base / "seed.py", 0o600)

    d = CodeChangeDeliverable(
        intent="copy + add",
        base=BaseReference(kind="local_path", value=str(base)),
        files={"added.py": "added = 1\n", "pkg/nested.py": "n = 1\n"},
    )
    async with await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path) as ws:
        for rel in ("seed.py", "added.py", "pkg/nested.py"):
            mode = stat.S_IMODE(os.stat(ws.root / rel).st_mode)
            assert mode & 0o004, f"{rel} mode {oct(mode)} missing world-read"


@pytestmark_posix
async def test_materialize_subdirs_are_world_traversable(tmp_path: Path) -> None:
    """Subdirectories created by ``files`` writes (or copied from a
    base) must also be traversable from any UID; a 0o700 ``pkg/``
    in the middle of the path is just as fatal as a 0o700 root.

    Force a strict umask for the duration of the test so the
    assertion isn't accidentally satisfied by the default 0o022
    umask making ``mkdir(parents=True)`` already-world-readable
    — a CI container or security-hardened system can ship with
    umask 0o077, which is the failure mode this regression
    actually catches."""
    d = CodeChangeDeliverable(
        intent="nested",
        files={
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/mod.py": "v = 1\n",
        },
    )
    saved_umask = os.umask(0o077)
    try:
        async with await Workspace.materialize(d, http=FakeHttpClient(), tmp_root=tmp_path) as ws:
            for rel in ("pkg", "pkg/sub"):
                mode = stat.S_IMODE(os.stat(ws.root / rel).st_mode)
                assert mode & 0o001, f"{rel} mode {oct(mode)} missing world-traverse"
                assert mode & 0o004, f"{rel} mode {oct(mode)} missing world-read"
    finally:
        os.umask(saved_umask)
