from __future__ import annotations

import signoff_mcp
from signoff_mcp.__main__ import main


def test_package_imports() -> None:
    assert signoff_mcp.__version__ == "0.0.1"


def test_health_flag_exits_zero(capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main(["--health"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "healthy" in captured.out
