"""Pluggable, per-application domain knowledge for the NL->SQL agent.

The agent harness is deliberately domain-neutral: it knows how to explore a
schema, resolve dates, stay read-only and hold a conversation, but it knows
nothing about any particular product. A *domain pack* injects the business
knowledge for one application (glossary, rules, example queries) so the same
plugin can serve Warrants today and another application tomorrow — by swapping
the pack, not the code.

Packs live as JSON files under ``domains/`` (override with ``DOMAIN_PACK_DIR``)
and are loaded by name, e.g. ``load_domain("warrants")``.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "domains"


class DomainExample(BaseModel):
    """One illustrative NL->SQL example used as few-shot guidance.

    ``sql`` is a pattern to learn structure from (quarters, de-duplication,
    exclusions). The agent must still confirm real table/column names via the
    schema tools; identifiers here are illustrative only.
    """

    question: str
    sql: str = ""
    notes: str = ""


class DomainPack(BaseModel):
    """Business knowledge for a single application, adaptable per product."""

    name: str
    description: str = ""
    # Business term -> what it means / how it maps to the model (a hint, not a
    # hard-coded identifier). Helps the agent bridge user language to the schema.
    glossary: dict[str, str] = Field(default_factory=dict)
    # Domain-specific guidance (aggregation levels, exclusion flags, etc.).
    rules: list[str] = Field(default_factory=list)
    examples: list[DomainExample] = Field(default_factory=list)

    def to_prompt(self) -> str:
        """Render the pack into a system-prompt fragment (empty-safe)."""
        parts: list[str] = [
            f"Application domain: {self.name}."
            + (f" {self.description}" if self.description else "")
        ]

        if self.glossary:
            parts.append(
                "\nBusiness glossary (map the user's words to the model; still "
                "confirm the exact identifiers with the schema tools):"
            )
            parts += [f"- {term}: {meaning}" for term, meaning in self.glossary.items()]

        if self.rules:
            parts.append("\nDomain rules:")
            parts += [f"- {rule}" for rule in self.rules]

        if self.examples:
            parts.append(
                "\nExample requests (patterns to adapt — the table/column names are "
                "illustrative; use the real ones returned by the tools):"
            )
            for ex in self.examples:
                parts.append(f'\nRequest: "{ex.question}"')
                if ex.sql:
                    parts.append(f"Pattern SQL:\n{ex.sql}")
                if ex.notes:
                    parts.append(f"Note: {ex.notes}")

        return "\n".join(parts)


def _pack_dir() -> Path:
    override = os.getenv("DOMAIN_PACK_DIR")
    return Path(override) if override else _DEFAULT_DIR


def load_domain(name_or_path: str | None) -> DomainPack | None:
    """Load a domain pack by name (``warrants``) or explicit file path.

    Returns ``None`` when ``name_or_path`` is falsy, so the agent runs fully
    generic. Raises ``FileNotFoundError`` if a name/path was given but missing.
    """
    if not name_or_path:
        return None

    candidate = Path(name_or_path)
    if not candidate.exists():
        candidate = _pack_dir() / f"{name_or_path}.json"
    if not candidate.exists():
        raise FileNotFoundError(
            f"Domain pack '{name_or_path}' not found (looked in {candidate})."
        )

    data = json.loads(candidate.read_text(encoding="utf-8"))
    pack = DomainPack.model_validate(data)
    logger.info("Loaded domain pack '%s' from %s", pack.name, candidate)
    return pack


def available_domains() -> list[str]:
    """Names of the domain packs discoverable in the pack directory."""
    directory = _pack_dir()
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))
