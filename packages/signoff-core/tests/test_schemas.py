"""Tests for the JSON schema export script and the committed schemas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "src" / "signoff" / "schemas"
EXPECTED_MODELS = {
    "deliverable",
    "claim",
    "verifier_result",
    "verdict",
    "feedback_packet",
    "blocker_entry",
    "warning_entry",
}


def test_every_model_has_a_committed_schema() -> None:
    on_disk = {p.stem for p in SCHEMAS_DIR.glob("*.json")}
    assert on_disk == EXPECTED_MODELS


@pytest.mark.parametrize("name", sorted(EXPECTED_MODELS))
def test_schemas_are_valid_json_with_expected_top_level_keys(name: str) -> None:
    data = json.loads((SCHEMAS_DIR / f"{name}.json").read_text())
    assert data.get("type") == "object"
    assert "properties" in data
    assert "required" in data
    assert data.get("title")


def test_schema_check_matches_live_models() -> None:
    # Import lazily to avoid a cycle if the script ever becomes importable
    # from the package.
    import sys

    script = Path(__file__).resolve().parents[3] / "scripts" / "export_schemas.py"
    sys.path.insert(0, str(script.parent))
    try:
        import export_schemas  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    assert export_schemas.check_all() == 0
