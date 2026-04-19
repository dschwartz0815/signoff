"""``Workspace`` — materialise a :class:`CodeChangeDeliverable` into a
filesystem tree verifiers can run against.

Under :class:`DockerRuntime`, the resulting path is bind-mounted into
the sandbox container. Under :class:`LocalRuntime`, verifiers execute
directly against the host path. That's safe because every verifier
in this pack declares ``runtime_required="docker"``; running under
LocalRuntime logs a loud warning but stays supported for development.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from signoff_code.deliverable import BaseReference, CodeChangeDeliverable

if TYPE_CHECKING:
    from signoff.context import HttpClient

__all__ = ["MAX_CHANGE_BYTES", "Workspace", "WorkspaceError"]


_logger = logging.getLogger("signoff_code.workspace")


#: Hard cap on how big a CodeChangeDeliverable's diff + files body may
#: be. 10 MiB keeps a runaway content payload from filling the temp
#: dir. Overridable by passing ``max_bytes=`` to
#: :meth:`Workspace.materialize` (for genuinely large legitimate
#: changes — but think twice before raising it).
MAX_CHANGE_BYTES = 10 * 1024 * 1024


class WorkspaceError(Exception):
    """Raised when a deliverable can't be materialised into a workspace.

    Verifier call sites turn this into a BLOCKER-severity
    :class:`VerifierResult` — a code change the harness can't even
    unpack shouldn't get the deliverable a ``passed`` verdict.
    """


class Workspace:
    """A temp directory hosting a materialised :class:`CodeChangeDeliverable`.

    Construct via :meth:`materialize`; always call :meth:`cleanup`
    (the async context manager form does this for you). Holding the
    workspace longer than a verify() call is a lifetime bug.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cleaned = False

    # -- lifecycle ----------------------------------------------------------

    @classmethod
    async def materialize(
        cls,
        deliverable: CodeChangeDeliverable,
        *,
        http: HttpClient,
        tmp_root: Path | None = None,
        max_bytes: int = MAX_CHANGE_BYTES,
    ) -> Workspace:
        """Turn ``deliverable`` into a writable working tree.

        Steps:

        1. Enforce the size cap against ``deliverable.diff +
           sum(files.values())``.
        2. Create a tempdir under ``tmp_root`` (``tempfile.gettempdir()``
           by default).
        3. If ``base`` is set, materialise the pre-change state.
           Unsupported kinds raise :class:`WorkspaceError` with a
           clear message.
        4. If ``diff`` is set, apply it via ``git apply --index``
           when base is a git SHA, otherwise ``patch -p1``.
        5. If ``files`` is set, write each path (creating parent
           dirs). Path traversal was already rejected at model
           validation — re-checked here as belt-and-braces.
        6. Record the derived :attr:`CodeChangeDeliverable.changed_paths`
           back onto the deliverable if it was empty.
        """
        _enforce_size_cap(deliverable, max_bytes=max_bytes)

        root = Path(tempfile.mkdtemp(prefix="signoff-code-", dir=_as_str(tmp_root)))
        ws = cls(root)
        try:
            if deliverable.base is not None:
                await ws._seed_from_base(deliverable.base, http=http)
            if deliverable.diff is not None:
                await ws._apply_diff(deliverable.diff, has_base=deliverable.base is not None)
            if deliverable.files is not None:
                ws._write_files(deliverable.files)
            if not deliverable.changed_paths:
                deliverable.changed_paths = deliverable.derive_changed_paths()
        except WorkspaceError:
            await ws.cleanup()
            raise
        except Exception as exc:
            await ws.cleanup()
            raise WorkspaceError(
                f"Unexpected failure while materialising workspace: {type(exc).__name__}: {exc}"
            ) from exc
        return ws

    async def __aenter__(self) -> Workspace:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.cleanup()

    async def cleanup(self) -> None:
        """Remove the workspace. Idempotent."""
        if self._cleaned:
            return
        self._cleaned = True
        if self._root.exists():
            await asyncio.to_thread(shutil.rmtree, self._root, True)

    @property
    def root(self) -> Path:
        return self._root

    # -- internals ----------------------------------------------------------

    async def _seed_from_base(self, base: BaseReference, *, http: HttpClient) -> None:
        if base.kind == "local_path":
            src = Path(base.value).resolve()
            if not src.is_dir():
                raise WorkspaceError(
                    f"local_path base {src!s} does not exist or is not a directory."
                )
            await asyncio.to_thread(_copy_tree, src, self._root)
            return
        if base.kind == "tarball_url":
            result = await http.get(base.value)
            if not result.ok:
                raise WorkspaceError(
                    f"Failed to fetch base tarball {base.value!r}: "
                    f"status={result.status_code} error={result.error!r}"
                )
            # FetchResult.text carries decoded bytes as a string; for
            # a gzip we need raw bytes. HttpxClient decodes utf-8 with
            # errors=replace, which loses binary fidelity. Reject this
            # path with a clear error until we add a bytes-returning
            # HttpClient method in a later PR.
            raise WorkspaceError(
                "tarball_url base is not yet supported end-to-end — "
                "HttpClient.get returns text. Track: see "
                "docs/http-client.md for the follow-up bytes API."
            )
        if base.kind == "git_sha":
            # git_sha requires an out-of-band repo URL (we don't have
            # one in BaseReference yet). Phase 1 supports git bases
            # via a pre-seeded local_path; dedicated git materialiser
            # lands with the hosted service.
            raise WorkspaceError(
                "git_sha base requires a repo URL (not yet in "
                "BaseReference). Use kind='local_path' pointing at a "
                "checkout of the base revision for now."
            )
        raise WorkspaceError(f"Unknown BaseReference kind={base.kind!r}.")

    async def _apply_diff(self, diff: str, *, has_base: bool) -> None:
        if not has_base:
            # Without a base, the `diff` has no "pre" state to apply
            # against; treat this as a user error rather than
            # silently dropping hunks.
            raise WorkspaceError(
                "CodeChangeDeliverable.diff requires a `base` reference. "
                "Provide `files` instead if you don't have a pre-change "
                "state to patch against."
            )
        patch_path = self._root / ".signoff.diff"
        patch_path.write_text(diff)
        proc = await asyncio.create_subprocess_exec(
            "patch",
            "-p1",
            "--force",
            "--no-backup-if-mismatch",
            "-i",
            str(patch_path),
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        patch_path.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise WorkspaceError(
                f"Failed to apply diff (patch exit {proc.returncode}): "
                f"{stderr_bytes.decode('utf-8', errors='replace')[:400] or stdout_bytes.decode('utf-8', errors='replace')[:400]}"
            )

    def _write_files(self, files: dict[str, str]) -> None:
        for rel, content in files.items():
            dest = (self._root / rel).resolve()
            # Re-enforce: dest must be under root (belt-and-braces).
            try:
                dest.relative_to(self._root.resolve())
            except ValueError as exc:
                raise WorkspaceError(f"File path {rel!r} resolved outside workspace root.") from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _enforce_size_cap(deliverable: CodeChangeDeliverable, *, max_bytes: int) -> None:
    total = 0
    if deliverable.diff is not None:
        total += len(deliverable.diff.encode("utf-8"))
    if deliverable.files is not None:
        total += sum(len(c.encode("utf-8")) for c in deliverable.files.values())
    if total > max_bytes:
        raise WorkspaceError(
            f"CodeChangeDeliverable body is {total} bytes; max is {max_bytes}. "
            "Raise `max_bytes=` on Workspace.materialize if this is legitimate."
        )


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy ``src`` contents into the existing ``dst`` directory.

    Skips version-control directories (``.git`` etc.) that aren't
    useful for the verifier and are often the bulk of a repo copy.
    """

    def _ignore(
        _directory: str, entries: list[str]
    ) -> Iterable[str]:  # signature match for shutil.copytree
        skip = {".git", ".hg", ".svn", "__pycache__", ".venv", "node_modules"}
        return [e for e in entries if e in skip]

    for entry in src.iterdir():
        if entry.name in {".git", ".hg", ".svn"}:
            continue
        if entry.is_dir():
            shutil.copytree(entry, dst / entry.name, ignore=_ignore)
        else:
            shutil.copy2(entry, dst / entry.name)


def _as_str(path: Path | None) -> str | None:
    return str(path) if path is not None else None
