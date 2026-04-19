"""The :func:`verifier` decorator.

Turns an ``async def`` function into a Signoff verifier by validating
its metadata at import time and attaching a
:class:`~signoff.runtime.base.VerifierMeta` to it. See
``docs/writing-a-verifier.md`` for the authoring guide.

Implements ``docs/protocol.md`` §4.1 (registration metadata) and §4.3
(invocation signature).
"""

from __future__ import annotations

import inspect
import re
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any, Literal, Protocol, cast

from signoff.models import RESERVED_CLAIM_KINDS
from signoff.runtime.base import VerifierMeta

__all__ = [
    "RegisteredVerifier",
    "VerifierFn",
    "_testing_pack",
    "verifier",
]


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Local name of a verifier: lowercase snake_case, ≤ 64 chars.
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Fully-qualified ``<pack>.<name>`` per protocol §4.1.
_FQN_PATTERN = re.compile(r"^[a-z][a-z0-9_\-]*\.[a-z][a-z0-9_]*$")

# Pack-namespaced claim kind per §3.3.1.
_PACK_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_\-]*\.[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# A verifier is any ``async def`` callable with two positional parameters.
# We type-hint it as Any here to keep the decorator caller-friendly; the
# runtime signature check is exact.
VerifierFn = Callable[..., Awaitable[Any]]


class RegisteredVerifier(Protocol):
    """Structural protocol — the decorator returns the original function
    with a ``signoff_meta`` attribute attached. At runtime any callable
    with a ``.signoff_meta: VerifierMeta`` satisfies this protocol.
    """

    signoff_meta: VerifierMeta

    def __call__(self, *args: Any, **kwargs: Any) -> Awaitable[Any]: ...


# ---------------------------------------------------------------------------
# Pack-name detection + testing override
# ---------------------------------------------------------------------------

_testing_pack_stack = threading.local()


def _current_testing_pack() -> str | None:
    stack: list[str] = getattr(_testing_pack_stack, "stack", [])
    return stack[-1] if stack else None


@contextmanager
def _testing_pack(pack_name: str) -> Iterator[None]:
    """Override pack-name detection inside the ``with`` block.

    Test-only. Verifier authors should NEVER use this. The decorator
    prefers this override when set so tests can construct verifiers
    without the wrapping function actually living inside a ``signoff_``
    module.
    """
    stack: list[str] = getattr(_testing_pack_stack, "stack", [])
    stack.append(pack_name)
    _testing_pack_stack.stack = stack
    try:
        yield
    finally:
        stack.pop()


def _infer_pack_name(module_name: str) -> str:
    """Derive the pack name from a module's dotted path.

    ``signoff_research.verifiers.citation`` → ``signoff-research``.
    ``signoff-foo`` → ``signoff-foo`` (already hyphenated; unusual but
    tolerated for packages written that way).
    """
    override = _current_testing_pack()
    if override is not None:
        return override
    if not module_name:
        raise ValueError(
            "Cannot determine pack name: fn.__module__ is empty. "
            "Move the verifier into a module under a signoff_* package, "
            "or (tests only) use signoff.verifier._testing_pack()."
        )
    top = module_name.split(".", 1)[0]
    hyphenated = top.replace("_", "-")
    if not (hyphenated.startswith("signoff-") or hyphenated == "signoff"):
        raise ValueError(
            f"@verifier must live under a signoff_* pack; got module {module_name!r}. "
            "Examples: signoff_research.verifiers.foo (→ pack 'signoff-research'), "
            "signoff_code.verifiers.bar (→ pack 'signoff-code'). "
            "For ad-hoc tests, wrap decoration in signoff.verifier._testing_pack('my-pack')."
        )
    return hyphenated


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def verifier(
    *,
    name: str,
    claim_kinds: list[str] | tuple[str, ...] | Literal["*"],
    cost_tier: Literal["cheap", "medium", "expensive"],
    concurrency: int = 1,
    timeout_seconds: int = 30,
    version: str | None = None,
    requires: list[str] | tuple[str, ...] = (),
    runtime_required: Literal["local", "docker"] | None = None,
) -> Callable[[VerifierFn], RegisteredVerifier]:
    """Declare a Signoff verifier. See ``docs/writing-a-verifier.md``.

    The decorated function MUST be ``async def`` and MUST accept exactly
    two positional arguments: ``claim`` and ``ctx``. Metadata is
    validated eagerly so authoring mistakes surface at import time.
    """
    _validate_name(name)
    kinds_tuple = _validate_claim_kinds(claim_kinds)
    _validate_cost_tier(cost_tier)
    _validate_positive_int("concurrency", concurrency)
    _validate_positive_int("timeout_seconds", timeout_seconds)
    requires_tuple = _validate_requires(requires)
    _validate_runtime_required(runtime_required)

    def decorate(fn: VerifierFn) -> RegisteredVerifier:
        _validate_signature(fn)
        pack = _infer_pack_name(fn.__module__ or "")
        meta = VerifierMeta(
            name=name,
            pack=pack,
            claim_kinds=kinds_tuple,
            cost_tier=cost_tier,
            concurrency=concurrency,
            timeout_seconds=timeout_seconds,
            version=version,
            requires=requires_tuple,
            runtime_required=runtime_required,
        )
        setattr(fn, "signoff_meta", meta)  # noqa: B010 — dynamic attr is the contract
        return cast("RegisteredVerifier", fn)

    return decorate


# ---------------------------------------------------------------------------
# Validators — all raise ValueError with messages that cite the spec.
# ---------------------------------------------------------------------------


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_PATTERN.match(name):
        raise ValueError(
            f"@verifier(name={name!r}) must match {_NAME_PATTERN.pattern} "
            "(lowercase snake_case, ≤ 64 chars)."
        )


def _validate_claim_kinds(
    claim_kinds: list[str] | tuple[str, ...] | Literal["*"],
) -> tuple[str, ...]:
    # Canonicalise to tuple[str, ...].
    if claim_kinds == "*":
        return ("*",)
    if not isinstance(claim_kinds, (list, tuple)):
        raise ValueError(
            "@verifier(claim_kinds=...) must be a list/tuple of strings or the literal '*'; "
            f"got {type(claim_kinds).__name__}."
        )
    if not claim_kinds:
        raise ValueError("@verifier(claim_kinds=...) must be non-empty.")
    out: list[str] = []
    for k in claim_kinds:
        if not isinstance(k, str):
            raise ValueError(f"@verifier claim_kind must be a string; got {type(k).__name__}.")
        if k == "*":
            # Whole-deliverable declared alongside specific kinds is an error —
            # ambiguous routing. If you want *, pass exactly ["*"].
            if len(claim_kinds) > 1:
                raise ValueError(
                    "@verifier(claim_kinds=...) cannot mix '*' with specific kinds. "
                    "Pass exactly ['*'] for whole-deliverable verifiers."
                )
            out.append(k)
            continue
        if k in RESERVED_CLAIM_KINDS:
            out.append(k)
            continue
        if _PACK_KIND_PATTERN.match(k):
            out.append(k)
            continue
        raise ValueError(
            f"@verifier claim_kind {k!r} is not reserved (§3.3.1) and lacks a pack namespace. "
            f"Use one of {sorted(RESERVED_CLAIM_KINDS)} or namespace as '<pack>.<kind>'."
        )
    return tuple(out)


def _validate_cost_tier(cost_tier: str) -> None:
    if cost_tier not in ("cheap", "medium", "expensive"):
        raise ValueError(
            f"@verifier(cost_tier={cost_tier!r}) must be 'cheap', 'medium', or 'expensive'."
        )


def _validate_positive_int(field: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"@verifier({field}={value!r}) must be an integer >= 1.")


def _validate_requires(
    requires: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(requires, (list, tuple)):
        raise ValueError(
            "@verifier(requires=...) must be a list/tuple of fully-qualified "
            f"'<pack>.<name>' strings; got {type(requires).__name__}."
        )
    out: list[str] = []
    for r in requires:
        if not isinstance(r, str) or not _FQN_PATTERN.match(r):
            raise ValueError(
                f"@verifier requires entry {r!r} must be fully-qualified '<pack>.<name>' "
                "per protocol §4.1 (lowercase)."
            )
        out.append(r)
    return tuple(out)


def _validate_runtime_required(runtime_required: str | None) -> None:
    if runtime_required is None:
        return
    if runtime_required not in ("local", "docker"):
        raise ValueError(
            f"@verifier(runtime_required={runtime_required!r}) must be 'local', 'docker', or None."
        )


def _validate_signature(fn: Callable[..., Any]) -> None:
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(
            f"@verifier expects an async function; {fn.__qualname__} is not 'async def'. "
            "Protocol §4.3 requires verifiers to be async callables."
        )
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if len(params) != 2:
        raise TypeError(
            f"@verifier expects exactly two parameters (claim, ctx); "
            f"{fn.__qualname__} takes {len(params)}."
        )
    for p in params:
        if p.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        ):
            raise TypeError(
                f"@verifier parameter {p.name!r} must be positional; "
                "*args / **kwargs forms are not allowed."
            )
