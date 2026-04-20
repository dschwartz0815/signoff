"""Deliverable preparer registered via ``signoff.deliverable_preparers``.

Runs once per :meth:`signoff.Harness.verify` call for a
``code_change`` deliverable, before any verifier executes. This is
the one and only place :class:`Workspace` gets materialised per
verification — the old per-verifier materialisation pattern caused
nested temp trees when ``ctx.workspace`` was itself the seed
directory for the next verifier's copy.

Signature expected by the harness::

    async def prepare(deliverable, http) -> (Path, cleanup)

where ``cleanup`` is an async callable the harness invokes after
building the verdict.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from signoff_code.deliverable import CodeChangeDeliverable
from signoff_code.workspace import Workspace

if TYPE_CHECKING:
    from signoff import Deliverable
    from signoff.context import HttpClient

__all__ = ["prepare_code_change"]


_logger = logging.getLogger("signoff_code.prepare")


async def prepare_code_change(
    deliverable: Deliverable, http: HttpClient
) -> tuple[Path, Callable[[], Awaitable[None]]] | None:
    """Materialise the code_change workspace once per verify().

    Returns ``None`` when the deliverable's content is not a
    :class:`CodeChangeDeliverable` — the harness treats that as
    "no preparation needed" and falls back to ``Path.cwd()`` so a
    mis-typed payload doesn't wedge the pipeline.
    """
    payload = deliverable.content
    if isinstance(payload, CodeChangeDeliverable):
        change = payload
    elif isinstance(payload, dict):
        try:
            change = CodeChangeDeliverable.model_validate(payload)
        except Exception as exc:
            _logger.warning(
                "prepare_code_change: content validation failed (%s); no workspace materialised.",
                exc,
            )
            return None
    else:
        _logger.warning(
            "prepare_code_change: unexpected content type %s; no workspace materialised.",
            type(payload).__name__,
        )
        return None

    # ``tmp_root=None`` lands the workspace under tempfile.gettempdir()
    # (/tmp on Linux, $TMPDIR on macOS — both OS-reaped). Keeping it
    # outside the caller's cwd is the fix for the nested-temp-tree
    # bug: when ``ctx.workspace`` was the cwd, Workspace._copy_tree
    # included prior runs' in-flight materialisations in the seed.
    workspace = await Workspace.materialize(change, http=http, tmp_root=None)

    async def _cleanup() -> None:
        await workspace.cleanup()

    return workspace.root, _cleanup
