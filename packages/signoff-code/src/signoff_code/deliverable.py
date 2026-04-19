"""``CodeChangeDeliverable`` — structured shape of ``Deliverable.content``
for ``kind="code_change"``.

Users build one of these and wrap it in a :class:`signoff.Deliverable` the
harness consumes. The pack's verifiers
(:mod:`signoff_code.verifiers.tests_pass` etc.) read the materialised
workspace — they never touch the raw ``CodeChangeDeliverable`` directly.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["BaseReference", "CodeChangeDeliverable"]


_DIFF_FILE_RE = re.compile(r"^(?:diff --git a/|\+\+\+ b/|--- a/)(?P<path>[^\s]+)", re.MULTILINE)


class BaseReference(BaseModel):
    """How to obtain the pre-change state of the codebase.

    - ``git_sha``: a SHA in the project's git repo. The materialiser
      issues ``git clone`` + ``git checkout`` and expects the repo to
      be reachable from the harness host (SSH key, public HTTPS, etc.).
    - ``tarball_url``: a URL that returns a ``.tar.gz`` archive of the
      project at the base revision. Fetched via ``ctx.http``.
    - ``local_path``: a filesystem path on the harness host. Copied
      (not symlinked) so verifier mutations can't leak back. Primarily
      for local dogfooding; the hosted service will reject this kind.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["git_sha", "tarball_url", "local_path"]
    value: str


class CodeChangeDeliverable(BaseModel):
    """A proposed change to a codebase.

    Exactly one of :attr:`diff` or :attr:`files` must be set. When
    :attr:`base` is omitted, the materialiser treats the workspace
    root (specified at runtime) as already in the desired state —
    useful for dogfooding against a local checkout without going
    through git.
    """

    model_config = ConfigDict(extra="forbid")

    #: The stated intent — what the agent says this change does.
    #: Consumed by :mod:`~signoff_code.verifiers.semantic_diff`;
    #: ignored by the deterministic verifiers.
    intent: str = ""

    base: BaseReference | None = None

    #: Unified-diff text (``git diff`` format). Applied with
    #: ``git apply --index`` if a base repo is available, else
    #: ``patch -p1`` from the workspace root.
    diff: str | None = None

    #: Map of project-relative path → full file content. Overwrites
    #: whatever is at those paths in the base revision. Mutually
    #: exclusive with :attr:`diff`.
    files: dict[str, str] | None = None

    #: Paths touched by the change. Derived from ``diff`` or
    #: ``files.keys()`` during :class:`~signoff_code.Workspace`
    #: materialisation when left empty.
    changed_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_change_form(self) -> Self:
        if self.diff is None and self.files is None:
            raise ValueError("CodeChangeDeliverable requires exactly one of `diff` or `files`.")
        if self.diff is not None and self.files is not None:
            raise ValueError("CodeChangeDeliverable accepts `diff` XOR `files`, not both.")
        if self.files is not None:
            for path in self.files:
                _reject_unsafe_path(path)
        return self

    def derive_changed_paths(self) -> list[str]:
        """Return a best-effort list of paths touched by this change.

        Prefers the explicit :attr:`changed_paths`; otherwise parses
        the diff headers or reads :attr:`files`. Deduplicates while
        preserving order.
        """
        if self.changed_paths:
            return list(dict.fromkeys(self.changed_paths))
        paths: list[str] = []
        if self.files is not None:
            paths.extend(self.files)
        if self.diff is not None:
            seen: set[str] = set()
            for match in _DIFF_FILE_RE.finditer(self.diff):
                path = match.group("path")
                if path == "/dev/null" or path in seen:
                    continue
                seen.add(path)
                paths.append(path)
        return list(dict.fromkeys(paths))


def _reject_unsafe_path(path: str) -> None:
    """Raise ``ValueError`` if ``path`` tries to escape the workspace.

    Applied to every ``files`` key at validation so a compromised
    caller can't write outside the materialised workspace even if
    the verifiers later run sandboxed. Belt-and-braces defence.
    """
    if not path:
        raise ValueError("CodeChangeDeliverable files: empty path not allowed.")
    if path.startswith("/"):
        raise ValueError(f"CodeChangeDeliverable files: absolute path {path!r} not allowed.")
    parts = path.replace("\\", "/").split("/")
    if any(p == ".." for p in parts):
        raise ValueError(f"CodeChangeDeliverable files: {path!r} contains '..' segment.")
