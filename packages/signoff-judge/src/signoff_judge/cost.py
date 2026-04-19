"""Model-specific cost tables and the single ``estimate_cost`` entry point.

Keep this module the *only* place judge-call rates live. When a
provider changes prices, exactly one file changes.

Updating the table:

1. Check the provider's pricing page (``source`` URL).
2. Update ``input_usd_per_million`` and/or ``output_usd_per_million``.
3. Change ``effective_date`` to the date you verified.
4. Leave older models in the table; deleting them means old audit
   logs can't be reconstructed.

Rates here are accurate as of the ``effective_date`` per entry and
are checked in to source so a regression in the audit trail is
visible in ``git log``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

__all__ = ["RATES", "ModelRates", "estimate_cost"]


_logger = logging.getLogger("signoff_judge.cost")


@dataclass(frozen=True, slots=True)
class ModelRates:
    """Per-million-token rates for a given model.

    ``effective_date`` + ``source`` together form the provenance
    stamp; the audit log stores the ``model`` string so cost can be
    re-derived from this table.
    """

    model: str
    input_usd_per_million: float
    output_usd_per_million: float
    effective_date: str
    source: str


#: Rate table. Keys are the exact ``model`` strings providers accept
#: on the wire — not friendly names. Unknown models are handled by
#: :func:`estimate_cost` with a WARNING.
RATES: dict[str, ModelRates] = {
    # --- Anthropic ---------------------------------------------------------
    "claude-haiku-4-5": ModelRates(
        model="claude-haiku-4-5",
        input_usd_per_million=1.00,
        output_usd_per_million=5.00,
        effective_date="2026-04-19",
        source="https://www.anthropic.com/pricing",
    ),
    "claude-sonnet-4-5": ModelRates(
        model="claude-sonnet-4-5",
        input_usd_per_million=3.00,
        output_usd_per_million=15.00,
        effective_date="2026-04-19",
        source="https://www.anthropic.com/pricing",
    ),
    "claude-opus-4-7": ModelRates(
        model="claude-opus-4-7",
        input_usd_per_million=15.00,
        output_usd_per_million=75.00,
        effective_date="2026-04-19",
        source="https://www.anthropic.com/pricing",
    ),
    # --- OpenAI ------------------------------------------------------------
    "gpt-4o-mini": ModelRates(
        model="gpt-4o-mini",
        input_usd_per_million=0.15,
        output_usd_per_million=0.60,
        effective_date="2026-04-19",
        source="https://openai.com/api/pricing/",
    ),
    "gpt-4o": ModelRates(
        model="gpt-4o",
        input_usd_per_million=2.50,
        output_usd_per_million=10.00,
        effective_date="2026-04-19",
        source="https://openai.com/api/pricing/",
    ),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for a single judge call.

    Unknown models log a WARNING and return ``0.0`` — the verdict
    still completes, but the operator sees a pointer to update
    :data:`RATES`. Negative token counts raise :class:`ValueError`
    (every provider we support reports non-negative usage).
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError(
            f"Token counts must be non-negative; got input={input_tokens}, output={output_tokens}."
        )
    if input_tokens == 0 and output_tokens == 0:
        return 0.0
    rates = RATES.get(model)
    if rates is None:
        _logger.warning(
            "estimate_cost: unknown model %r — reporting 0.0 USD. "
            "Add a ModelRates entry in signoff_judge.cost.RATES.",
            model,
        )
        return 0.0
    return (
        input_tokens * rates.input_usd_per_million / 1_000_000.0
        + output_tokens * rates.output_usd_per_million / 1_000_000.0
    )
