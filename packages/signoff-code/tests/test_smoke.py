from __future__ import annotations

import signoff_code


def test_package_imports() -> None:
    assert signoff_code.__version__ == "0.0.1"
