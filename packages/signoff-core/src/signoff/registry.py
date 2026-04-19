"""Verifier registry and entry-point discovery.

Implements ``docs/protocol.md`` §4.1 (metadata index) and §4.2
(entry-point discovery via the ``signoff.verifiers`` group).
"""

from __future__ import annotations

import logging
import threading
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from signoff.runtime.base import VerifierMeta
    from signoff.verifier import RegisteredVerifier


__all__ = ["ENTRY_POINT_GROUP", "Registry", "default_registry"]


#: Protocol §4.2 — the entry-point group every Python pack uses to
#: publish verifiers.
ENTRY_POINT_GROUP = "signoff.verifiers"


_logger = logging.getLogger("signoff.registry")


def _meta(v: Any) -> VerifierMeta | None:
    """Return ``v.signoff_meta`` if present; else ``None``. Type-narrow helper."""
    return getattr(v, "signoff_meta", None)


class Registry:
    """Verifier registry. Discovers and indexes verifiers from installed packs.

    Implements protocol §4.1 (metadata index) and §4.2 (entry-point
    discovery). Thread-safe for concurrent reads; writes
    (``register`` / ``discover``) hold an internal lock.
    """

    def __init__(self) -> None:
        self._by_fqn: dict[str, RegisteredVerifier] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ factory

    @classmethod
    def discovered(cls) -> Registry:
        """Return a registry with every installed pack's verifiers
        already loaded via :meth:`discover`.

        Equivalent to::

            r = Registry()
            r.discover()
            return r

        Prefer this factory when you have no programmatic verifiers to
        add. Use the two-step form when you need to register verifiers
        before discovery, or re-scan after installing a new pack at
        runtime.
        """
        r = cls()
        r.discover()
        return r

    # ------------------------------------------------------------------ public

    def discover(self) -> int:
        """Load all verifiers published under the ``signoff.verifiers``
        entry-point group.

        Returns the number of verifiers actually registered (not counted
        are skipped entry points — see log output). Idempotent: calling
        twice reconciles against the current environment and re-warns on
        duplicates.
        """
        count = 0
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            if self._load_entry_point(ep):
                count += 1
        return count

    def register(self, fn: RegisteredVerifier) -> None:
        """Add a verifier to the registry. Last-write-wins for
        duplicates; logs a WARNING so silent overrides stand out.
        """
        meta = _meta(fn)
        if meta is None:
            raise TypeError(
                f"register() expected a @verifier-decorated callable; got {fn!r} with no "
                "`signoff_meta` attribute."
            )
        fqn = meta.fully_qualified_name
        with self._lock:
            if fqn in self._by_fqn:
                _logger.warning("Duplicate verifier registration for %r; last write wins.", fqn)
            self._by_fqn[fqn] = fn

    def get(self, fully_qualified_name: str) -> RegisteredVerifier:
        """Look up a verifier by ``<pack>.<name>``. Raises ``KeyError``
        if not present."""
        try:
            return self._by_fqn[fully_qualified_name]
        except KeyError as exc:
            raise KeyError(
                f"no verifier registered with fully-qualified name {fully_qualified_name!r}"
            ) from exc

    def list_all(self) -> list[VerifierMeta]:
        """Every registered verifier's metadata, sorted by FQN."""
        # Read-only snapshot so callers iterating don't see mutation.
        items = list(self._by_fqn.values())
        metas = [_require_meta(v) for v in items]
        return sorted(metas, key=lambda m: m.fully_qualified_name)

    def for_claim_kind(self, kind: str) -> list[RegisteredVerifier]:
        """Verifiers whose ``claim_kinds`` include ``kind`` or ``'*'``."""
        out: list[RegisteredVerifier] = []
        for v in self._by_fqn.values():
            meta = _require_meta(v)
            if "*" in meta.claim_kinds or kind in meta.claim_kinds:
                out.append(v)
        return sorted(out, key=lambda v: _require_meta(v).fully_qualified_name)

    def whole_deliverable(self) -> list[RegisteredVerifier]:
        """Verifiers registered with ``claim_kinds = ['*']``."""
        return sorted(
            (v for v in self._by_fqn.values() if _require_meta(v).claim_kinds == ("*",)),
            key=lambda v: _require_meta(v).fully_qualified_name,
        )

    def clear(self) -> None:
        """Drop every registered verifier. Primarily for tests."""
        with self._lock:
            self._by_fqn.clear()

    # ------------------------------------------------------------------ dunder

    def __contains__(self, fully_qualified_name: object) -> bool:
        return isinstance(fully_qualified_name, str) and fully_qualified_name in self._by_fqn

    def __len__(self) -> int:
        return len(self._by_fqn)

    # ------------------------------------------------------------------ helpers

    def _load_entry_point(self, ep: EntryPoint) -> bool:
        """Load one entry point. Returns ``True`` iff a verifier was
        registered from it. Any error is logged and swallowed so one bad
        pack doesn't break discovery.
        """
        try:
            target = ep.load()
        except Exception as exc:
            _logger.warning(
                "Failed to load entry point %s=%s: %s: %s",
                ep.name,
                ep.value,
                type(exc).__name__,
                exc,
            )
            return False
        if _meta(target) is None:
            _logger.warning(
                "Entry point %s=%s did not resolve to a @verifier-decorated callable; skipping.",
                ep.name,
                ep.value,
            )
            return False
        self.register(target)
        return True


def _require_meta(v: Any) -> VerifierMeta:
    meta = _meta(v)
    if meta is None:  # pragma: no cover — Registry only stores decorated fns
        raise TypeError(f"internal error: {v!r} has no signoff_meta")
    return meta


#: Module-level convenience singleton. Constructed lazily to keep import
#: costs low. Tests should construct fresh :class:`Registry` instances.
default_registry = Registry()
