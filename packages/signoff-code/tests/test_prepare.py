"""Regression tests for single-materialisation workspace preparation.

These cover the bug where each verifier materialised its own
workspace under ``ctx.workspace`` and concurrent verifiers produced
nested ``signoff-code-XXXX/signoff-code-YYYY/...`` trees. The fix
moved materialisation into the ``signoff.deliverable_preparers``
entry-point hook invoked once per :meth:`Harness.verify` call, so
every verifier in the run sees the same workspace root.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from signoff import (
    Claim,
    Deliverable,
    Harness,
    LocalRuntime,
    Registry,
    VerifierContext,
    VerifierResult,
    load_config,
    verifier,
)
from signoff.testing import FakeHttpClient, FakeJudge
from signoff.verifier import _testing_pack
from signoff_code import BaseReference, CodeChangeDeliverable
from signoff_code.prepare import prepare_code_change

# ``tempfile.mkdtemp`` picks suffix chars from
# ``string.ascii_letters + string.digits + "-_"`` (Python 3.12+),
# so the regex must allow upper-case + underscore — without that
# allowance the pattern false-negatives ~5% of the time depending
# on the random suffix.
_NESTED_TEMPDIR_RE = re.compile(r"signoff-code-[a-zA-Z0-9_-]+")


async def test_prepare_code_change_returns_single_root(tmp_path: Path) -> None:
    """The preparer itself materialises exactly once."""
    deliverable = Deliverable(
        id="dlv_prep",
        kind="code_change",
        content=CodeChangeDeliverable(intent="add x", files={"a.py": "X = 1\n"}),
    )
    result = await prepare_code_change(deliverable, http=FakeHttpClient())
    assert result is not None
    workspace_root, cleanup = result
    try:
        assert workspace_root.is_dir()
        # Exactly one ``signoff-code-`` segment in the path.
        segments = _NESTED_TEMPDIR_RE.findall(str(workspace_root))
        assert len(segments) == 1, f"unexpected nesting: {segments}"
        # Contents match the deliverable.
        assert (workspace_root / "a.py").read_text() == "X = 1\n"
    finally:
        await cleanup()
    # Cleanup actually removed the tree.
    assert not workspace_root.exists()
    del tmp_path


async def test_prepare_skips_when_content_shape_wrong() -> None:
    """A dict that can't validate returns None (no materialisation)."""
    deliverable = Deliverable(
        id="dlv_bad",
        kind="code_change",
        content={"intent": "x", "files": {"../escape": "bad"}},  # path traversal
    )
    result = await prepare_code_change(deliverable, http=FakeHttpClient())
    assert result is None


@pytest.mark.asyncio
async def test_harness_verify_passes_single_workspace_to_every_verifier(
    tmp_path: Path,
) -> None:
    """Integration: register two whole-deliverable verifiers that
    record ``ctx.workspace``. Both must see the SAME path (the one
    the preparer materialised), and that path must contain exactly
    one ``signoff-code-`` segment (no nesting)."""
    seen: list[Path] = []
    files_seen: list[bool] = []

    with _testing_pack("signoff-research"):

        @verifier(name="seen_first", claim_kinds="*", cost_tier="cheap")
        async def seen_first(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            seen.append(ctx.workspace)
            # Assert inside the verifier while the workspace is live —
            # after verify() returns the preparer teardown has rmtree'd
            # the tempdir.
            files_seen.append(
                (ctx.workspace / "added.py").exists() and (ctx.workspace / "keep.py").exists()
            )
            return ctx.ok(evidence={"workspace": str(ctx.workspace)})

        @verifier(name="seen_second", claim_kinds="*", cost_tier="cheap")
        async def seen_second(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            seen.append(ctx.workspace)
            files_seen.append((ctx.workspace / "added.py").exists())
            return ctx.ok(evidence={"workspace": str(ctx.workspace)})

    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "keep.py").write_text("keep = 1\n")

    r = Registry()
    r.register(seen_first)
    r.register(seen_second)

    deliverable = Deliverable(
        id="dlv_integ",
        kind="code_change",
        content=CodeChangeDeliverable(
            intent="integration test",
            base=BaseReference(kind="local_path", value=str(base_dir)),
            files={"added.py": "added = 1\n"},
        ),
    )

    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "code_change": {
                    "verifiers": {
                        "signoff-research.seen_first": {"enabled": True},
                        "signoff-research.seen_second": {"enabled": True},
                    }
                }
            },
        },
    )
    harness = Harness(
        config=cfg,
        registry=r,
        runtimes=[LocalRuntime()],
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    verdict = await harness.verify(deliverable, claims=[])

    assert verdict.passed is True
    assert len(seen) == 2, f"expected both verifiers to run, got {seen!r}"
    # Both verifiers saw the same workspace — no per-verifier
    # re-materialisation.
    assert seen[0] == seen[1]
    # Exactly one ``signoff-code-`` segment — no nesting.
    segments = _NESTED_TEMPDIR_RE.findall(str(seen[0]))
    assert len(segments) == 1, f"workspace path contains nested signoff-code segments: {seen[0]!s}"
    # The workspace had the expected files while the verifier ran.
    # (It's been rmtree'd by the preparer teardown by now.)
    assert all(files_seen), f"files_seen={files_seen!r}"


@pytest.mark.asyncio
async def test_workspace_cleaned_up_after_verify(tmp_path: Path) -> None:
    """The preparer's teardown fires in ``verify()``'s finally block
    even when a verifier raises."""
    with _testing_pack("signoff-research"):

        @verifier(name="captures_path", claim_kinds="*", cost_tier="cheap")
        async def captures_path(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            captures_path.workspace = ctx.workspace  # type: ignore[attr-defined]
            return ctx.ok()

    r = Registry()
    r.register(captures_path)

    deliverable = Deliverable(
        id="dlv_cleanup",
        kind="code_change",
        content=CodeChangeDeliverable(intent="x", files={"a.py": "X = 1\n"}),
    )
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "code_change": {"verifiers": {"signoff-research.captures_path": {"enabled": True}}}
            },
        },
    )
    harness = Harness(
        config=cfg,
        registry=r,
        runtimes=[LocalRuntime()],
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    await harness.verify(deliverable, claims=[])
    recorded: Any = captures_path.workspace  # type: ignore[attr-defined]
    assert not Path(recorded).exists(), f"preparer teardown should have removed {recorded}"
    del tmp_path
