"""Unit tests for :class:`CodeChangeDeliverable` and :class:`BaseReference`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from signoff_code import BaseReference, CodeChangeDeliverable


def test_diff_only_parses() -> None:
    d = CodeChangeDeliverable(
        intent="add x",
        base=BaseReference(kind="local_path", value="/tmp/repo"),
        diff="--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+x\n",
    )
    assert d.diff is not None
    assert d.files is None


def test_files_only_parses() -> None:
    d = CodeChangeDeliverable(intent="rewrite", files={"foo.py": "x = 1\n"})
    assert d.files == {"foo.py": "x = 1\n"}
    assert d.diff is None


def test_both_diff_and_files_rejected() -> None:
    with pytest.raises(ValidationError, match="XOR"):
        CodeChangeDeliverable(
            intent="both",
            diff="--- a\n+++ b\n",
            files={"a": ""},
        )


def test_neither_diff_nor_files_rejected() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        CodeChangeDeliverable(intent="none")


@pytest.mark.parametrize(
    "kind",
    ["git_sha", "tarball_url", "local_path"],
)
def test_base_reference_kinds(kind: str) -> None:
    ref = BaseReference(kind=kind, value="whatever")  # type: ignore[arg-type]
    assert ref.kind == kind


def test_base_reference_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        BaseReference(kind="sftp", value="x")  # type: ignore[arg-type]


def test_files_path_absolute_rejected() -> None:
    with pytest.raises(ValidationError, match="absolute path"):
        CodeChangeDeliverable(intent="x", files={"/etc/passwd": ""})


def test_files_path_dotdot_rejected() -> None:
    with pytest.raises(ValidationError, match=r"'\.\.' segment"):
        CodeChangeDeliverable(intent="x", files={"../secret": ""})


def test_files_empty_path_rejected() -> None:
    with pytest.raises(ValidationError, match="empty path"):
        CodeChangeDeliverable(intent="x", files={"": ""})


def test_derive_changed_paths_from_files() -> None:
    d = CodeChangeDeliverable(intent="x", files={"a.py": "", "b/c.py": ""})
    assert d.derive_changed_paths() == ["a.py", "b/c.py"]


def test_derive_changed_paths_from_diff() -> None:
    diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -0,0 +1 @@\n+hi\n"
    )
    d = CodeChangeDeliverable(
        intent="x",
        base=BaseReference(kind="local_path", value="/tmp"),
        diff=diff,
    )
    assert d.derive_changed_paths() == ["src/foo.py", "README.md"]


def test_derive_changed_paths_respects_explicit_override() -> None:
    d = CodeChangeDeliverable(
        intent="x",
        files={"a.py": ""},
        changed_paths=["pinned.py"],
    )
    assert d.derive_changed_paths() == ["pinned.py"]
