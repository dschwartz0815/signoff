"""Export JSON schemas for every ``signoff.models`` type.

The committed schemas under
``packages/signoff-core/src/signoff/schemas/`` are the source of truth
for cross-language parity: the TypeScript SDK copies them at build time
and asserts agreement between its Zod schemas and these JSON schemas.

Usage:

    python scripts/export_schemas.py              # rewrite schemas on disk
    python scripts/export_schemas.py --check      # fail if they drifted

The ``--check`` mode is wired into CI so that any change to
``signoff.models`` that doesn't re-export schemas fails the PR.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from signoff.models import (
    BlockerEntry,
    Claim,
    Deliverable,
    FeedbackPacket,
    Verdict,
    VerifierResult,
    WarningEntry,
)

# Ordered so the files on disk are easy to read top-down in §3 order.
MODELS: list[type[BaseModel]] = [
    Deliverable,
    Claim,
    VerifierResult,
    Verdict,
    FeedbackPacket,
    BlockerEntry,
    WarningEntry,
]

SCHEMAS_DIR = (
    Path(__file__).resolve().parent.parent
    / "packages"
    / "signoff-core"
    / "src"
    / "signoff"
    / "schemas"
)


def _file_for(model: type[BaseModel]) -> Path:
    return SCHEMAS_DIR / f"{_snake(model.__name__)}.json"


def _snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _schema_bytes(model: type[BaseModel]) -> bytes:
    schema = model.model_json_schema()
    return (json.dumps(schema, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_all() -> None:
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        _file_for(model).write_bytes(_schema_bytes(model))


def check_all() -> int:
    """Return 0 if committed schemas match what the models would produce now."""
    drift: list[str] = []
    for model in MODELS:
        path = _file_for(model)
        expected = _schema_bytes(model)
        if not path.exists():
            drift.append(f"missing: {path.relative_to(SCHEMAS_DIR.parent.parent.parent.parent)}")
            continue
        actual = path.read_bytes()
        if actual != expected:
            drift.append(
                f"drift: {path.relative_to(SCHEMAS_DIR.parent.parent.parent.parent)}"
            )
    if drift:
        sys.stderr.write(
            "JSON schema drift detected. Re-run `python scripts/export_schemas.py` "
            "after editing models.py and commit the result.\n"
        )
        for line in drift:
            sys.stderr.write(f"  - {line}\n")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the committed schemas don't match the current models.",
    )
    args = parser.parse_args(argv)
    if args.check:
        return check_all()
    write_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
