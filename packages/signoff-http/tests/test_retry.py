"""Unit tests for the retry classifier and backoff calculator."""

from __future__ import annotations

import httpx
import pytest
from signoff_http.retry import (
    RETRYABLE_STATUS_CODES,
    backoff_seconds,
    classify,
    parse_retry_after,
)


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {})


# -- classify --------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS_CODES))
def test_classify_retryable_statuses(status: int) -> None:
    d = classify(method="GET", response=_response(status), exception=None)
    assert d.should_retry is True
    assert d.reason == f"http_{status}"


@pytest.mark.parametrize("status", [200, 301, 400, 401, 403, 404, 418])
def test_classify_terminal_statuses(status: int) -> None:
    d = classify(method="GET", response=_response(status), exception=None)
    assert d.should_retry is False
    assert d.reason == f"http_{status}"


def test_classify_429_picks_up_retry_after() -> None:
    d = classify(
        method="GET",
        response=_response(429, {"Retry-After": "3"}),
        exception=None,
    )
    assert d.should_retry is True
    assert d.server_backoff == 3.0


def test_classify_connect_timeout_is_retryable() -> None:
    d = classify(method="GET", response=None, exception=httpx.ConnectTimeout("x"))
    assert d.should_retry is True
    assert d.reason == "connect_timeout"


def test_classify_read_timeout_is_retryable() -> None:
    d = classify(method="GET", response=None, exception=httpx.ReadTimeout("x"))
    assert d.should_retry is True
    assert d.reason == "read_timeout"


def test_classify_connect_error_is_retryable() -> None:
    d = classify(method="GET", response=None, exception=httpx.ConnectError("x"))
    assert d.should_retry is True
    assert d.reason == "connect_error"


def test_classify_other_httpx_error_not_retryable() -> None:
    d = classify(method="GET", response=None, exception=httpx.TooManyRedirects("x"))
    assert d.should_retry is False
    assert "TooManyRedirects" in d.reason


# -- parse_retry_after -----------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("5", 5.0),
        ("0", 0.0),
        ("  12  ", 12.0),
        (None, None),
        ("", None),
        ("not-a-number", None),
        ("-3", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", None),  # HTTP-date: unsupported.
    ],
)
def test_parse_retry_after(header: str | None, expected: float | None) -> None:
    assert parse_retry_after(header) == expected


# -- backoff_seconds -------------------------------------------------------


def test_backoff_exponential_growth() -> None:
    assert backoff_seconds(attempt=1, base=0.5, factor=2.0, max_backoff=10.0) == 0.5
    assert backoff_seconds(attempt=2, base=0.5, factor=2.0, max_backoff=10.0) == 1.0
    assert backoff_seconds(attempt=3, base=0.5, factor=2.0, max_backoff=10.0) == 2.0
    # Clamped at max_backoff.
    assert backoff_seconds(attempt=20, base=0.5, factor=2.0, max_backoff=10.0) == 10.0


def test_backoff_server_hint_wins() -> None:
    assert (
        backoff_seconds(attempt=5, base=0.5, factor=2.0, max_backoff=10.0, server_hint=2.5) == 2.5
    )


def test_backoff_server_hint_clamped() -> None:
    assert (
        backoff_seconds(attempt=1, base=0.5, factor=2.0, max_backoff=4.0, server_hint=60.0) == 4.0
    )
