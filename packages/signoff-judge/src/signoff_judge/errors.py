"""Exceptions raised by :class:`BaseJudge` subclasses.

All three are subclasses of :class:`JudgeError` so a verifier author
can catch the umbrella type once and map every class of judge failure
to ``ctx.fail(..., severity=INFO)`` per protocol §4.4 (transient LLM
failure MUST NOT be blamed on the deliverable).
"""

from __future__ import annotations

__all__ = [
    "JudgeError",
    "JudgeInfrastructureError",
    "JudgeMalformedResponseError",
    "JudgeRefusalError",
]


class JudgeError(Exception):
    """Base class for anything a :class:`BaseJudge` can raise."""


class JudgeInfrastructureError(JudgeError):
    """Rate limit exceeded, 5xx, timeout, or network failure.

    Retries have already been exhausted by the time this escapes; the
    caller should treat the attempt as inconclusive, not failed.
    """


class JudgeMalformedResponseError(JudgeError):
    """Provider returned something that doesn't match the output schema.

    Usually means the model ignored the structured-output instruction
    and returned prose, or the schema itself is broken. Either is a
    bug on our side, not a verdict about the deliverable.
    """


class JudgeRefusalError(JudgeError):
    """Provider refused the request on content-policy grounds.

    Same treatment as the other two: the harness records the refusal
    in the audit log but does not fail the deliverable on it.
    """
