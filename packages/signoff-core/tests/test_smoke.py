from __future__ import annotations

import signoff


def test_package_imports() -> None:
    assert signoff.__version__ == "0.0.1"
