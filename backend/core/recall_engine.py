"""Recall Engine — hybrid search over Knowledge Library for prompt injection.

Combines FTS5 keyword search + sqlite-vec vector search (0.6/0.4 weights)
to retrieve relevant knowledge chunks from the indexed Library. Results are
formatted with provenance (source file + heading) for injection into the
system prompt as "## Recalled Knowledge".

Standing principle: **Power over token budget.** Inject everything relevant.
Only apply budget pressure at >95% context usage.

Public symbols:

- ``RecallEngine``       — Hybrid search + formatting over KnowledgeStore
- ``VECTOR_WEIGHT``      — Weight for vector score (0.6)
- ``KEYWORD_WEIGHT``     — Weight for keyword/FTS5 score (0.4)
- ``RECALL_THRESHOLD``   — Minimum score to include a result (0.05)
"""

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

VECTOR_WEIGHT = 0.6
KEYWORD_WEIGHT = 0.4
RECALL_THRESHOLD = 0.05  # Low — power first
DEFAULT_MAX_TOKENS = 15_000
_CHARS_PER_TOKEN = 4  # rough estimate; code-heavy content may be ~2-3 chars/token


# ── RecallEngine ──────────────────────────────────────────────────────


class RecallEngine:
    """Hybrid search engine over the Knowledge Library.

    Combines FTS5 (keyword) and sqlite-vec (vector) search results
    with configurable weights. Formats output with provenance for
    system prompt injection.
    """

    def __init__(self, store: "KnowledgeStore"):
        """
        Args:
            store: KnowledgeStore instance with tables already ensured.
        """
        self._store = store

    def search(
        self,
        query: str,
        embed_fn: Optional[Callable[[str], Optional[list[float]]]] = None,
        top_k: int = 20,
        threshold: float = RECALL_THRESHOLD,
    ) -> list[dict]:
        """Hybrid search: FTS5 + vector, merged and ranked.

        Args:
            query: Natural language query string.
            embed_fn: Optional embedding function (Bedrock Titan).
                     If None or returns None, falls back to FTS5-only.
            top_k: Max results per search type.
            threshold: Minimum hybrid score to include.

        Returns:
            List of result dicts with keys: id, source_file, heading,
            content, hybrid_score, fts_score, vector_score.
        """
        if not query or not query.strip():
            return []

        # 1. FTS5 keyword search
        fts_results = self._store.fts5_search(query, limit=top_k)

        # Normalize FTS5 ranks to 0-1 scores
        # FTS5 rank is negative (more negative = better match)
        fts_scored: dict[int, dict] = {}
        if fts_results:
            min_rank = min(r["fts_rank"] for r in fts_results)
            max_rank = max(r["fts_rank"] for r in fts_results)
            rank_range = max_rank - min_rank if max_rank != min_rank else 1.0

            for r in fts_results:
                # Invert: best rank (most negative) → highest score
                score = 1.0 - (r["fts_rank"] - min_rank) / rank_range if rank_range else 1.0
                fts_scored[r["id"]] = {**r, "fts_score": score}

        # 2. Vector search (graceful fallback)
        vec_scored: dict[int, dict] = {}
        if embed_fn is not None:
            query_embedding = embed_fn(query)
            if query_embedding is not None:
                vec_results = self._store.vector_search(query_embedding, top_k=top_k)
                for r in vec_results:
                    vec_scored[r["id"]] = {**r}

        # 3. Hybrid merge
        all_ids = set(fts_scored.keys()) | set(vec_scored.keys())
        merged: list[dict] = []

        for chunk_id in all_ids:
            fts_entry = fts_scored.get(chunk_id, {})
            vec_entry = vec_scored.get(chunk_id, {})

            fts_score = fts_entry.get("fts_score", 0.0)
            vec_score = vec_entry.get("vector_score", 0.0)
            hybrid = VECTOR_WEIGHT * vec_score + KEYWORD_WEIGHT * fts_score

            if hybrid < threshold:
                continue

            # Take metadata from whichever source has it
            base = fts_entry or vec_entry
            merged.append({
                "id": chunk_id,
                "source_file": base.get("source_file", ""),
                "heading": base.get("heading", ""),
                "content": base.get("content", ""),
                "hybrid_score": hybrid,
                "fts_score": fts_score,
                "vector_score": vec_score,
            })

        # Sort by hybrid score descending
        merged.sort(key=lambda r: r["hybrid_score"], reverse=True)
        return merged

    def recall_knowledge(
        self,
        query: str,
        embed_fn: Optional[Callable[[str], Optional[list[float]]]] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        """Search and format results for system prompt injection.

        Returns formatted markdown text with provenance, or empty string
        if no relevant results found.

        Args:
            query: Search query (typically focus keywords from proactive briefing).
            embed_fn: Optional embedding function.
            max_tokens: Token budget for the recalled content.

        Returns:
            Formatted string ready for prompt injection, or "".
        """
        results = self.search(query, embed_fn=embed_fn)

        if not results:
            return ""

        chunks: list[str] = []
        used_tokens = 0

        for r in results:
            formatted = self._format_chunk(r)
            chunk_tokens = len(formatted) // _CHARS_PER_TOKEN

            if used_tokens + chunk_tokens > max_tokens:
                break

            chunks.append(formatted)
            used_tokens += chunk_tokens

        return "\n\n".join(chunks)

    @staticmethod
    def _format_chunk(result: dict) -> str:
        """Format a single recall result with provenance."""
        source = result.get("source_file", "unknown")
        heading = result.get("heading", "")
        content = result.get("content", "")

        # Extract date from source file if possible (e.g. "DailyActivity/2026-04-01.md")
        date = ""
        parts = source.split("/")
        if len(parts) >= 2:
            name = parts[-1].replace(".md", "")
            # Guard: need at least 10 chars and a dash at position 4 (YYYY-MM-DD)
            if len(name) >= 10 and name[4:5] == "-":
                date = name[:10]

        header = f"**[{source}]**"
        if date:
            header += f" ({date})"
        if heading:
            header += f" — {heading}"

        return f"{header}\n{content}"
