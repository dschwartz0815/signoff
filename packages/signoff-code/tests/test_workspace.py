"""Unit tests for :class:`Workspace`."""

from __future__ import annotations

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
