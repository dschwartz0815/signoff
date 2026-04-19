"""Pack-defaults loader for ``signoff_code``.

Published via the ``signoff.pack_defaults`` entry-point group
(see ``pyproject.toml``). :func:`signoff.config.load_config` calls
this, merges the result into layer 2 of the config resolution, and
validates the combined document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

__all__ = ["DEFAULT_CONFIG_PATH", "load"]


DEFAULT_CONFIG_PATH = Path(__file__).with_name("default_config.yaml")


def load() -> dict[str, Any]:
    """Return the parsed ``default_config.yaml`` as a plain dict.

    Called by the config loader's pack-defaults layer. Raised
    exceptions are turned into a WARNING in the loader (per
    ``signoff.config._pack_defaults``) so a broken pack doesn't
    poison the whole harness — but a broken ``default_config.yaml``
    should be caught in this pack's own test suite before it ships.
    """
    with DEFAULT_CONFIG_PATH.open() as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(
            f"{DEFAULT_CONFIG_PATH} did not parse as a mapping; got {type(payload).__name__}."
        )
    return payload
