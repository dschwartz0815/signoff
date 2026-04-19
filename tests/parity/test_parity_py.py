"""Python side of the cross-language parity test.

Every valid fixture must:
- Round-trip cleanly through the relevant model.
- Produce byte-identical canonical JSON on the way out.

Every invalid fixture must raise a ValidationError that points at the
field named in the fixture's ``.meta.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from signoff import (
    BlockerEntry,
    Claim,
    Deliverable,
    FeedbackPacket,
    Verdict,
    VerifierResult,
    WarningEntry,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
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


def _canonical(obj: Any) -> str:
    """Canonical JSON: sorted keys, minimal separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@pytest.mark.parametrize(("stem", "model_name"), sorted(MANIFEST["valid"].items()))
def test_valid_fixture_round_trips(stem: str, model_name: str) -> None:
    raw = (FIXTURES_DIR / f"{stem}.json").read_text()
    loaded = json.loads(raw)
    model = MODELS[model_name]
    parsed = model.model_validate(loaded)
    # Round-trip: dump -> load -> dump should produce identical canonical JSON.
    dumped_once = _canonical(parsed.model_dump(mode="json"))
    re_parsed = model.model_validate_json(dumped_once)
    dumped_twice = _canonical(re_parsed.model_dump(mode="json"))
    assert dumped_once == dumped_twice
    # And the canonicalised fixture matches the model's canonicalised output.
    assert _canonical(loaded) == dumped_once, (
        f"{stem}.json does not canonicalise the same as the model's output. "
        "Edit the fixture so its fields match the model output exactly."
    )


@pytest.mark.parametrize("stem", MANIFEST["invalid"])
def test_invalid_fixture_raises_validation_error(stem: str) -> None:
    payload = json.loads((FIXTURES_DIR / f"{stem}.json").read_text())
    meta = json.loads((FIXTURES_DIR / f"{stem}.meta.json").read_text())
    model = MODELS[meta["model"]]
    with pytest.raises(ValidationError) as exc_info:
        model.model_validate(payload)
    errors = exc_info.value.errors()
    fields = [".".join(str(p) for p in err["loc"]) for err in errors]
    messages = [err.get("msg", "") for err in errors]
    expected = meta["expect_error_on"]
    # Accept either a field-scoped error on the expected field, or a
    # model-wide invariant error whose message names the field. This lets
    # fixtures point at invariants (§3.5, §3.6) that Pydantic surfaces at
    # model level rather than per-field.
    assert any(expected in f for f in fields) or any(expected in m for m in messages), (
        f"{stem}: expected an error on/about {expected!r}; got fields={fields}, messages={messages}"
    )
