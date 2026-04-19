"""Harness configuration per ``docs/protocol.md`` §6.

Loads YAML, merges layer-by-layer in the order defined in §6.2, and
validates per §6.3. Model classes are Pydantic v2 so the config
document round-trips through JSON schemas like the data models.

Resolution order (later wins):

    1. Built-in defaults (the model class defaults)
    2. Pack-declared defaults (entry-point group ``signoff.pack_defaults``)
    3. User-supplied YAML at ``path``
    4. Environment variables prefixed ``SIGNOFF_CORE_`` (double-underscore
       nesting). Sibling packages own parallel namespaces
       (``SIGNOFF_MCP_``, ``SIGNOFF_HTTP_``, ``SIGNOFF_JUDGE_``); they're
       ignored here.
    5. Per-request overrides passed to the harness

Deep-merge semantics:

- Dicts merge recursively, key by key.
- Lists are **replaced**, not concatenated. This avoids surprising
  behaviour where pack defaults pollute user choices.
- Scalars replace. Explicit ``None`` unsets the earlier value.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from signoff.models import Severity
from signoff.registry import Registry
from signoff.runtime.base import RuntimePolicy

__all__ = [
    "ENV_PREFIX",
    "PACK_DEFAULTS_ENTRY_POINT_GROUP",
    "BudgetConfig",
    "ConfigurationError",
    "DeliverableConfig",
    "HarnessConfig",
    "HttpConfig",
    "JudgeConfig",
    "RetryConfig",
    "RuntimeConfig",
    "RuntimePolicyConfig",
    "VerifierConfig",
    "deep_merge",
    "load_config",
    "validate_config",
]


_logger = logging.getLogger("signoff.config")


#: Entry-point group packs use to publish their ``default_config.yaml``.
#: A pack declares one entry pointing at a callable ``() -> dict`` (or
#: at a module attribute that is already a dict).
PACK_DEFAULTS_ENTRY_POINT_GROUP = "signoff.pack_defaults"


class ConfigurationError(Exception):
    """Raised when config merging or validation fails."""


# ---------------------------------------------------------------------------
# Model classes (§6.1)
# ---------------------------------------------------------------------------


class VerifierConfig(BaseModel):
    """Per-verifier overrides inside a deliverable config block."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    severity_override: Severity | None = None
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    timeout_seconds_override: int | None = Field(default=None, ge=1)


class DeliverableConfig(BaseModel):
    """Config block scoped to a single deliverable ``kind``."""

    model_config = ConfigDict(extra="forbid")

    packs: list[str] | None = None
    verifiers: dict[str, VerifierConfig] = Field(default_factory=dict)


class BudgetConfig(BaseModel):
    """§5.3 budget knobs."""

    model_config = ConfigDict(extra="forbid")

    max_cost_usd: float = Field(default=0.50, ge=0.0)
    max_duration_seconds: int = Field(default=120, ge=1)
    global_concurrency: int = Field(default=16, ge=1)
    early_termination: bool = False


class RuntimeConfig(BaseModel):
    """Runtime selection (§8.3)."""

    model_config = ConfigDict(extra="forbid")

    default: str = "local"
    per_verifier: dict[str, str] = Field(default_factory=dict)


class RuntimePolicyConfig(BaseModel):
    """Per-runtime policy blocks.

    ``local`` is typed; other runtime keys are tolerated via
    ``extra="allow"`` so ``signoff-runtime-docker`` can contribute a
    ``docker: ...`` block when installed.
    """

    model_config = ConfigDict(extra="allow")

    local: RuntimePolicy = Field(default_factory=RuntimePolicy)


class JudgeConfig(BaseModel):
    """LLM judge configuration."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["anthropic", "openai", "fake"] = "fake"
    model: str = "claude-haiku-4-5"
    max_tokens: int = Field(default=1024, ge=1)


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_budget: int = Field(default=3, ge=0)


class HttpConfig(BaseModel):
    """HTTP client selection for :meth:`Harness.from_config_path`.

    ``provider="httpx"`` (the default) wires up the real
    :class:`signoff_http.HttpxClient`. ``provider="fake"`` stays on
    :class:`signoff.testing.FakeHttpClient` — useful for offline
    unit runs and deterministic regression suites.

    Fine-grained httpx tuning (timeouts, retries, size caps, robots)
    lives in ``signoff-http``'s own ``SIGNOFF_HTTP_*`` namespace per
    ``docs/configuration.md``; we do not duplicate it here to avoid
    two places of truth.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["httpx", "fake"] = "httpx"


class HarnessConfig(BaseModel):
    """Top-level harness configuration. Implements protocol §6.1."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = Field(default="0.1")
    packs: list[str] = Field(default_factory=list)
    deliverables: dict[str, DeliverableConfig] = Field(default_factory=dict)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    runtime_policy: RuntimePolicyConfig = Field(default_factory=RuntimePolicyConfig)
    judge: JudgeConfig | None = None
    http: HttpConfig = Field(default_factory=HttpConfig)
    retries: RetryConfig = Field(default_factory=RetryConfig)


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``override`` into a copy of ``base``.

    - Dicts merge recursively.
    - Lists are replaced (not concatenated).
    - ``None`` in ``override`` unsets the corresponding key.
    - Scalars replace.
    """
    out: dict[str, Any] = {k: v for k, v in base.items()}
    for key, val in override.items():
        if val is None and key in out:
            # Explicit unset.
            del out[key]
            continue
        if isinstance(val, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _pack_defaults() -> dict[str, Any]:
    """Collect every installed pack's default config. Drift-tolerant:
    a broken pack logs a WARNING and is skipped rather than failing the
    whole load.
    """
    merged: dict[str, Any] = {}
    for ep in entry_points(group=PACK_DEFAULTS_ENTRY_POINT_GROUP):
        try:
            target = ep.load()
        except Exception as exc:
            _logger.warning(
                "Failed to load pack default %s=%s: %s: %s",
                ep.name,
                ep.value,
                type(exc).__name__,
                exc,
            )
            continue
        payload = target() if callable(target) else target
        if not isinstance(payload, Mapping):
            _logger.warning(
                "Pack default %s=%s did not resolve to a mapping; got %s. Skipping.",
                ep.name,
                ep.value,
                type(payload).__name__,
            )
            continue
        merged = deep_merge(merged, dict(payload))
    return merged


#: Env-var prefix for harness (signoff-core) config overrides.
#: Sibling packages own parallel namespaces — ``SIGNOFF_MCP_*`` for the
#: MCP server, and ``SIGNOFF_HTTP_*`` / ``SIGNOFF_JUDGE_*`` reserved for
#: the HTTP and judge client packages (PR 7 / PR 8). Anything outside
#: ``SIGNOFF_CORE_*`` is ignored by this loader.
ENV_PREFIX = "SIGNOFF_CORE_"


def _env_overrides() -> dict[str, Any]:
    """Translate :data:`ENV_PREFIX`-scoped env vars into a nested dict.

    ``SIGNOFF_CORE_BUDGET__MAX_COST_USD=1.0`` →
    ``{"budget": {"max_cost_usd": "1.0"}}``. Values stay strings;
    Pydantic coerces them when the merged dict is validated.

    Env vars that don't carry the ``SIGNOFF_CORE_`` prefix are ignored,
    even if they're prefixed ``SIGNOFF_`` — that bare prefix belongs to
    sibling packages (``SIGNOFF_MCP_*``, etc.) and must not be
    consumed here.
    """
    import os

    out: dict[str, Any] = {}
    for raw_key, raw_val in os.environ.items():
        if not raw_key.startswith(ENV_PREFIX):
            continue
        path = raw_key[len(ENV_PREFIX) :].lower().split("__")
        if not path or not path[0]:
            continue
        # Navigate / build the nested dict.
        cursor = out
        for part in path[:-1]:
            existing = cursor.setdefault(part, {})
            if not isinstance(existing, dict):
                # Collision between a leaf and a nested path — skip with warning.
                _logger.warning("Env var %s collides with an existing scalar; ignoring.", raw_key)
                cursor = {}
                break
            cursor = existing
        if path[-1]:
            cursor[path[-1]] = raw_val
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigurationError(f"failed to read config file {path}: {exc}") from exc
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {path}: {exc}") from exc
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ConfigurationError(
            f"config at {path} must be a mapping at the top level; got {type(payload).__name__}"
        )
    return dict(payload)


def load_config(
    *,
    path: Path | str | None = None,
    pack_defaults: bool = True,
    env_overrides: bool = True,
    request_overrides: Mapping[str, Any] | None = None,
) -> HarnessConfig:
    """Load, merge, and validate a harness config.

    Any layer can be suppressed via the ``pack_defaults`` /
    ``env_overrides`` flags (useful in tests). ``request_overrides``
    applies last.

    Raises :class:`ConfigurationError` on YAML/IO errors, shape errors
    (non-mapping top level), and Pydantic validation failures. The
    :class:`ConfigurationError` message includes the source path when
    the error can be attributed to a specific layer.
    """
    # Layer 1 — built-in defaults.
    merged: dict[str, Any] = HarnessConfig().model_dump(mode="python")

    # Layer 2 — pack defaults.
    if pack_defaults:
        merged = deep_merge(merged, _pack_defaults())

    # Layer 3 — user YAML.
    if path is not None:
        merged = deep_merge(merged, _load_yaml(Path(path)))

    # Layer 4 — env overrides.
    if env_overrides:
        merged = deep_merge(merged, _env_overrides())

    # Layer 5 — per-request overrides.
    if request_overrides is not None:
        merged = deep_merge(merged, dict(request_overrides))

    try:
        return HarnessConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigurationError(f"config validation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Validator (§6.3)
# ---------------------------------------------------------------------------


def validate_config(config: HarnessConfig, registry: Registry) -> None:
    """Raise :class:`ConfigurationError` if the config refers to verifiers
    or packs not present in ``registry``, or uses an incompatible
    protocol major version.

    Does NOT raise for:

    - Unknown deliverable kinds (harmless — they stay idle).
    - Unknown runtime keys in ``runtime_policy`` (``extra='allow'``).
    - Unknown environment variables.
    """
    # Protocol version major must match this implementation (0.x).
    try:
        major = int(config.protocol_version.split(".", 1)[0])
    except (ValueError, IndexError) as exc:
        raise ConfigurationError(
            f"protocol_version {config.protocol_version!r} is not a dotted number."
        ) from exc
    if major != 0:
        raise ConfigurationError(
            f"protocol_version major={major} is incompatible with this implementation (0.x)."
        )

    # Every verifier referenced in deliverables must be registered.
    registered_fqns = {m.fully_qualified_name for m in registry.list_all()}
    registered_packs = {m.pack for m in registry.list_all()}
    unknown_verifiers: list[str] = []
    for kind, block in config.deliverables.items():
        for fqn in block.verifiers:
            if fqn not in registered_fqns:
                unknown_verifiers.append(f"{kind}.{fqn}")
    if unknown_verifiers:
        raise ConfigurationError(
            "config references verifiers not present in the registry: "
            + ", ".join(sorted(unknown_verifiers))
        )

    # Every pack listed at the top level (or per-deliverable) must be
    # represented by at least one registered verifier.
    declared_packs = set(config.packs)
    for block in config.deliverables.values():
        if block.packs is not None:
            declared_packs.update(block.packs)
    missing_packs = sorted(declared_packs - registered_packs)
    if missing_packs:
        raise ConfigurationError(
            "config declares packs not represented by any registered verifier: "
            + ", ".join(missing_packs)
        )
