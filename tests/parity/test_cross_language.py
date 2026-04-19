"""Cross-language parity: byte-for-byte agreement between Python and TS.

For every valid fixture, we round-trip it through the Pydantic model and
the Zod schema (via a tiny Node helper) and assert the canonicalised
outputs match. This catches serialization drift that field-level unit
tests on each side would miss — e.g. one side stripping a nullable
default that the other preserves.

The test is skipped (not failed) if ``node`` or the built SDK are
unavailable, so Python-only contributors can still run ``just test-py``.
CI has both toolchains and executes the test for real.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel
from signoff import (
    BlockerEntry,
    Claim,
    Deliverable,
    FeedbackPacket,
    Verdict,
    VerifierResult,
    WarningEntry,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "parity" / "fixtures"
NODE_HELPER = REPO_ROOT / "tests" / "parity" / "roundtrip_node.mjs"
SDK_DIST = REPO_ROOT / "packages" / "signoff-sdk-ts" / "dist" / "index.js"
MANIFEST = json.loads((FIXTURES_DIR / "_manifest.json").read_text())

MODELS: dict[str, type[BaseModel]] = {
    "Deliverable": Deliverable,
    "Claim": Claim,
    "VerifierResult": VerifierResult,
    "Verdict": Verdict,
    "FeedbackPacket": FeedbackPacket,
    "BlockerEntry": BlockerEntry,
    "WarningEntry": WarningEntry,
}


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _skip_if_no_node() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not installed; skipping cross-language parity")
    if not SDK_DIST.exists():
        pytest.skip("@signoff/sdk dist not built. Run `pnpm --filter @signoff/sdk build` first.")


@pytest.mark.parametrize(("stem", "model_name"), sorted(MANIFEST["valid"].items()))
def test_cross_language_round_trip_matches(stem: str, model_name: str) -> None:
    _skip_if_no_node()
    fixture = FIXTURES_DIR / f"{stem}.json"
    python_out = _canonical(
        MODELS[model_name].model_validate_json(fixture.read_text()).model_dump(mode="json")
    )
    proc = subprocess.run(
        ["node", str(NODE_HELPER), model_name, str(fixture)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, f"node helper failed for {stem}: stderr={proc.stderr}"
    ts_out = proc.stdout

    # Byte-for-byte where encoding conventions agree; value-equal where
    # they don't. The known legal divergence is JSON number repr: Python
    # emits 0.0 for a float whose value is integer, JavaScript emits 0.
    # Both encode the same IEEE-754 value, so we compare parsed forms.
    if python_out == ts_out:
        return
    assert json.loads(python_out) == json.loads(ts_out), (
        f"cross-language divergence on {stem}:\n  py: {python_out}\n  ts: {ts_out}"
    )
