"""Prompt registry for :mod:`signoff_judge`.

Prompts are first-class, versioned artifacts stored as Markdown files
with YAML frontmatter. The registry loads them lazily on first access
and caches by ``(name, version)``.

Layout on disk::

    prompts/
      <name>.md                           ← template
      schemas/<name>.schema.json          ← JSON Schema for the
                                            structured output

A user directory (see :attr:`JudgeClientConfig.prompt_root`) may
override built-ins by placing files with the same ``name``/``version``
in the same layout. Overrides are opt-in; the harness never picks up
prompts from an ambient CWD.

Template variables are validated against the frontmatter's
``required_variables`` / ``optional_variables`` list, so a stray
kwarg fails loudly instead of silently expanding to an empty string.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined
from jinja2.exceptions import UndefinedError

__all__ = ["PromptNotFoundError", "PromptRegistry", "PromptTemplate"]


_BUILTIN_ROOT = Path(__file__).parent
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


class PromptNotFoundError(LookupError):
    """Raised when the registry can't resolve ``(name, version)``."""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A loaded prompt template with its output schema.

    ``render`` returns ``(system_prompt, user_prompt)`` so the judge
    can pass each to the provider separately — providers like
    Anthropic treat the two as different inputs, not a concatenation.
    """

    name: str
    version: str
    description: str
    system: str
    user_template: str
    output_schema: dict[str, Any]
    required_variables: tuple[str, ...]
    optional_variables: tuple[str, ...]

    def render(self, **kwargs: Any) -> tuple[str, str]:
        """Render the user prompt against ``kwargs``.

        Raises :class:`ValueError` when:

        - A required variable is missing.
        - An unexpected variable (not in required + optional) is passed.

        Undefined variables inside the template body also raise via
        Jinja's ``StrictUndefined``, so typos in the template surface
        immediately instead of silently rendering empty.
        """
        declared = set(self.required_variables) | set(self.optional_variables)
        supplied = set(kwargs)
        missing = set(self.required_variables) - supplied
        if missing:
            raise ValueError(
                f"Prompt {self.name!r}@{self.version} missing required "
                f"variable(s): {sorted(missing)}"
            )
        extra = supplied - declared
        if extra:
            raise ValueError(
                f"Prompt {self.name!r}@{self.version} received unexpected "
                f"variable(s): {sorted(extra)}. Declared: "
                f"required={list(self.required_variables)}, "
                f"optional={list(self.optional_variables)}."
            )
        # Populate optional vars that the caller omitted with ``None``
        # so ``{% if optional %}`` blocks render falsy rather than
        # tripping StrictUndefined. Required vars were already verified
        # present above.
        render_kwargs = {name: None for name in self.optional_variables}
        render_kwargs.update(kwargs)
        env = Environment(undefined=StrictUndefined, autoescape=False)
        template = env.from_string(self.user_template)
        try:
            user = template.render(**render_kwargs)
        except UndefinedError as exc:
            raise ValueError(
                f"Prompt {self.name!r}@{self.version} references an "
                f"undefined variable: {exc.message}"
            ) from exc
        return self.system, user.strip() + "\n"


class PromptRegistry:
    """Look up prompt templates by name (and optionally version).

    A registry has a built-in root (the ``prompts/`` directory shipped
    inside this package) and an optional user root. User files shadow
    built-ins when both exist with the same ``(name, version)``.
    """

    def __init__(self, user_root: Path | None = None) -> None:
        self._builtin_root = _BUILTIN_ROOT
        self._user_root = user_root
        self._cache: dict[tuple[str, str | None], PromptTemplate] = {}

    def get(self, name: str, version: str | None = None) -> PromptTemplate:
        """Return the template for ``name``.

        When ``version`` is ``None``, the registry returns whatever
        version it found first (user-root wins over built-in). Passing
        an explicit version makes the lookup strict — a mismatch
        raises :class:`PromptNotFoundError`.
        """
        cache_key = (name, version)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        template = self._load(name, version)
        self._cache[cache_key] = template
        return template

    def list_available(self) -> list[tuple[str, str]]:
        """Return sorted ``(name, version)`` tuples for every prompt
        the registry can see. User overrides take precedence."""
        found: dict[str, PromptTemplate] = {}
        for root in self._search_roots():
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*.md")):
                try:
                    template = self._load_from_file(path)
                except (ValueError, FileNotFoundError):
                    continue
                found.setdefault(template.name, template)
        return sorted((t.name, t.version) for t in found.values())

    # -- internals ---------------------------------------------------------

    def _search_roots(self) -> list[Path]:
        roots: list[Path] = []
        if self._user_root is not None:
            roots.append(self._user_root)
        roots.append(self._builtin_root)
        return roots

    def _load(self, name: str, version: str | None) -> PromptTemplate:
        for root in self._search_roots():
            candidate = root / f"{name}.md"
            if not candidate.is_file():
                continue
            template = self._load_from_file(candidate)
            if version is None or template.version == version:
                return template
        where = ", ".join(str(r) for r in self._search_roots())
        raise PromptNotFoundError(
            f"No prompt found for name={name!r} version={version!r} under roots [{where}]."
        )

    @staticmethod
    def _load_from_file(path: Path) -> PromptTemplate:
        raw = path.read_text()
        match = _FRONTMATTER_RE.match(raw)
        if match is None:
            raise ValueError(
                f"Prompt at {path} is missing YAML frontmatter (expected `---\\n...\\n---\\n`)."
            )
        meta = yaml.safe_load(match.group(1)) or {}
        body = match.group(2)
        system, user_template = _split_body(body, source=path)

        schema_rel = meta.get("output_schema")
        if not schema_rel:
            raise ValueError(f"Prompt at {path} missing `output_schema` in frontmatter.")
        schema_path = (path.parent / schema_rel).resolve()
        if not schema_path.is_file():
            raise FileNotFoundError(
                f"Prompt at {path} references output_schema={schema_rel!r}, "
                f"but {schema_path} does not exist."
            )
        output_schema = json.loads(schema_path.read_text())

        return PromptTemplate(
            name=str(meta["name"]),
            version=str(meta["version"]),
            description=str(meta.get("description", "")),
            system=system.strip(),
            user_template=user_template.strip(),
            output_schema=output_schema,
            required_variables=tuple(meta.get("required_variables") or ()),
            optional_variables=tuple(meta.get("optional_variables") or ()),
        )


_SYSTEM_HEADING = re.compile(r"^#\s*System prompt\s*$", re.IGNORECASE | re.MULTILINE)
_USER_HEADING = re.compile(r"^#\s*User prompt template\s*$", re.IGNORECASE | re.MULTILINE)


def _split_body(body: str, *, source: Path) -> tuple[str, str]:
    """Split a prompt body into ``(system, user_template)`` sections."""
    sys_match = _SYSTEM_HEADING.search(body)
    user_match = _USER_HEADING.search(body)
    if sys_match is None or user_match is None:
        raise ValueError(
            f"Prompt at {source} must include both "
            "'# System prompt' and '# User prompt template' headings."
        )
    if sys_match.start() > user_match.start():
        raise ValueError(
            f"Prompt at {source}: 'System prompt' must appear before 'User prompt template'."
        )
    system_text = body[sys_match.end() : user_match.start()]
    user_text = body[user_match.end() :]
    return system_text, user_text
