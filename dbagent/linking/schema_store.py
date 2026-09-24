"""Loads an enriched schema.json and performs schema linking.

"Schema linking" = selecting only the tables relevant to a question so the
agent's context stays small on databases with hundreds of tables. Two
strategies are supported and chosen automatically:

- keyword  : fast, dependency-free lexical scoring (default, always available)
- embedding: semantic ranking via Azure embeddings (enable with an LLM instance)
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from dbagent.llm import LLM
from dbagent.introspection.schema_model import SchemaModel, TableModel

logger = logging.getLogger(__name__)

# Unicode-aware: matches word tokens in any script (Latin, Cyrillic, Greek, ...).
_WORD = re.compile(r"\w+", re.UNICODE)


def _strip_accents(text: str) -> str:
    """Fold accents so 'Empfänger' and 'empfanger' or 'clé' and 'cle' match."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def _tokens(text: str) -> set[str]:
    return {
        _strip_accents(w.casefold())
        for w in _WORD.findall(text or "")
        if not w.isdigit()
    }


class SchemaStore:
    def __init__(self, model: SchemaModel, llm: LLM | None = None):
        self.model = model
        self.llm = llm
        self._by_name: dict[str, TableModel] = {
            t.qualified_name.lower(): t for t in model.tables
        }
        self._embeddings: dict[str, list[float]] | None = None
        self._fk_adj: dict[str, list[str]] | None = None

    # ---- construction -----------------------------------------------------
    @classmethod
    def from_json(cls, path: str | Path, llm: LLM | None = None) -> "SchemaStore":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(SchemaModel.model_validate(data), llm=llm)

    # ---- catalog views ----------------------------------------------------
    @property
    def dialect(self) -> str:
        return self.model.dialect

    def compact_catalog(self) -> str:
        """One line per table (name + description) for the agent's first look."""
        return self._catalog_lines(self.model.tables)

    def compact_catalog_for(self, table_names: list[str]) -> str:
        """Same one-line view, restricted to specific tables (used by search results)."""
        tables = [self._resolve(n) for n in table_names]
        return self._catalog_lines([t for t in tables if t is not None])

    def _catalog_lines(self, tables: list[TableModel]) -> str:
        lines = []
        for t in tables:
            desc = f" — {t.description}" if t.description else ""
            lines.append(f"{t.qualified_name}{desc}")
        return "\n".join(lines)

    def describe(self, table_names: list[str]) -> str:
        """Detailed, LLM-friendly schema for the named tables (columns, keys, samples)."""
        blocks = []
        for name in table_names:
            table = self._resolve(name)
            if table is None:
                blocks.append(f"[unknown table: {name}]")
                continue
            blocks.append(self._render_table(table))
        return "\n\n".join(blocks)

    # ---- schema linking ---------------------------------------------------
    def search(self, keywords: list[str], limit: int = 12) -> list[str]:
        """Return the most relevant qualified table names for the keywords.

        Fuses lexical and (when available) semantic rankings with reciprocal-rank
        fusion, then pulls in the foreign-key neighbours of the top hits so the
        agent always sees the tables it needs to join — the main accuracy risk on
        a large schema is a missing join partner, not a missing SELECT target.
        """
        pool = max(limit * 2, limit + 6)
        rankings = [self._search_keyword(keywords, pool)]
        if self.llm is not None:
            try:
                rankings.append(self._search_embedding(keywords, pool))
            except Exception as exc:  # fall back to lexical on any embedding issue
                logger.warning("Embedding search failed (%s); using keyword search only", exc)
        fused = _reciprocal_rank_fusion(rankings)[:limit]
        return self._with_fk_neighbors(fused, extra=min(6, limit))

    def _search_keyword(self, keywords: list[str], limit: int) -> list[str]:
        wanted = {_strip_accents(k.casefold()) for k in keywords}
        scored: list[tuple[int, str]] = []
        for t in self.model.tables:
            haystack = _tokens(t.qualified_name) | _tokens(t.description or "")
            for col in t.columns:
                haystack |= _tokens(col.name) | _tokens(col.description or "")
            score = sum(
                1
                for w in wanted
                if any(w in token or token in w for token in haystack)
            )
            if score:
                scored.append((score, t.qualified_name))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [name for _, name in scored[:limit]]

    def ensure_embeddings(self) -> None:
        """Build the table embedding index now if it isn't built yet.

        Lets callers pay the one-off embedding cost up front (e.g. at startup or
        on first cache load) so individual requests don't incur it. Safe to call
        repeatedly — it is a no-op once the index exists.
        """
        if self.llm is not None and self._embeddings is None:
            self._build_embeddings()

    def _search_embedding(self, keywords: list[str], limit: int) -> list[str]:
        assert self.llm is not None
        if self._embeddings is None:
            self._build_embeddings()
        assert self._embeddings is not None
        query_vec = _normalize(self.llm.embed([" ".join(keywords)])[0])
        # Stored vectors are pre-normalized, so cosine similarity reduces to a
        # dot product — no per-table norm recomputation on every search.
        ranked = sorted(
            self._embeddings.items(),
            key=lambda kv: _dot(query_vec, kv[1]),
            reverse=True,
        )
        return [name for name, _ in ranked[:limit]]

    def _build_embeddings(self) -> None:
        assert self.llm is not None
        docs, names = [], []
        for t in self.model.tables:
            docs.append(self._embedding_doc(t))
            names.append(t.qualified_name)
        vectors = self.llm.embed(docs)
        self._embeddings = {name: _normalize(v) for name, v in zip(names, vectors)}

    def _embedding_doc(self, t: TableModel) -> str:
        """Rich per-table document: descriptions and FK targets sharpen retrieval."""
        parts = [t.qualified_name]
        if t.description:
            parts.append(t.description)
        col_bits = []
        for c in t.columns:
            col_bits.append(f"{c.name} ({c.description})" if c.description else c.name)
        parts.append("columns: " + ", ".join(col_bits))
        related = [self._fk_target_name(fk) for fk in t.foreign_keys]
        related = [r for r in related if r]
        if related:
            parts.append("related: " + ", ".join(related))
        return " | ".join(parts)

    # ---- foreign-key neighbourhood ---------------------------------------
    def _with_fk_neighbors(self, names: list[str], extra: int) -> list[str]:
        """Append FK-linked tables of the top hits (both directions), capped."""
        if extra <= 0 or not names:
            return names
        adjacency = self._fk_adjacency()
        result = list(names)
        seen = {n.lower() for n in result}
        added = 0
        for name in names:
            for neighbor in adjacency.get(name.lower(), ()):
                if neighbor.lower() in seen:
                    continue
                result.append(neighbor)
                seen.add(neighbor.lower())
                added += 1
                if added >= extra:
                    return result
        return result

    def _fk_adjacency(self) -> dict[str, list[str]]:
        if self._fk_adj is None:
            adj: dict[str, set[str]] = defaultdict(set)
            for t in self.model.tables:
                src = t.qualified_name
                for fk in t.foreign_keys:
                    target = self._resolve(self._fk_target_name(fk) or "")
                    if target is not None:
                        adj[src.lower()].add(target.qualified_name)
                        adj[target.qualified_name.lower()].add(src)
            self._fk_adj = {k: sorted(v) for k, v in adj.items()}
        return self._fk_adj

    @staticmethod
    def _fk_target_name(fk) -> str:
        if not fk.referred_table:
            return ""
        return f"{fk.referred_schema}.{fk.referred_table}" if fk.referred_schema else fk.referred_table

    # ---- helpers ----------------------------------------------------------
    def _resolve(self, name: str) -> TableModel | None:
        key = name.lower()
        if key in self._by_name:
            return self._by_name[key]
        # Accept unqualified names when unambiguous.
        matches = [t for t in self.model.tables if t.name.lower() == key]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _render_table(t: TableModel) -> str:
        lines = [f"TABLE {t.qualified_name}"]
        if t.description:
            lines.append(f"  purpose: {t.description}")
        if t.row_count is not None:
            lines.append(f"  approx_rows: {t.row_count}")
        lines.append("  columns:")
        for c in t.columns:
            flags = []
            if c.primary_key:
                flags.append("PK")
            if not c.nullable:
                flags.append("NOT NULL")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            desc = f" — {c.description}" if c.description else ""
            samples = f" e.g. {c.sample_values}" if c.sample_values else ""
            lines.append(f"    - {c.name} {c.data_type}{flag_str}{desc}{samples}")
        if t.foreign_keys:
            lines.append("  foreign_keys:")
            for fk in t.foreign_keys:
                tgt_schema = f"{fk.referred_schema}." if fk.referred_schema else ""
                lines.append(
                    f"    - ({', '.join(fk.columns)}) -> "
                    f"{tgt_schema}{fk.referred_table}({', '.join(fk.referred_columns)})"
                )
        return "\n".join(lines)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Merge several ranked lists into one, rewarding items ranked high in many.

    RRF avoids having to normalise heterogeneous scores (cosine vs lexical hit
    counts): each list contributes 1/(k + rank) per item, and the sums are sorted.
    """
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, name in enumerate(ranking):
            scores[name] += 1.0 / (k + rank + 1)
    return [name for name, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if not norm:
        return vec
    return [x / norm for x in vec]
