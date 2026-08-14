"""Recall Engine — FTS5+BM25 keyword search over Knowledge Library for prompt injection.

Pure keyword recall (the sqlite-vec vector leg was removed 2026-08-14 — see PRI11:
FTS5-only, zero-embedding is the intended architecture). Retrieves relevant knowledge
chunks from the indexed Library, formatted with provenance (source file + heading) for
injection into the system prompt as "## Recalled Knowledge".

Standing principle: **Power over token budget.** Inject everything relevant.
Only apply budget pressure at >95% context usage.

Public symbols:

- ``RecallEngine``       — FTS5+BM25 search + formatting over KnowledgeStore
- ``RECALL_THRESHOLD``   — Minimum score to include a result (0.05)
"""

import hashlib
import logging

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

RECALL_THRESHOLD = 0.05  # Low — power first
# Floor for the min-max-normalized FTS5/BM25 score. Without it the worst keyword
# match in a multi-hit set normalizes to 0.0 < RECALL_THRESHOLD → silently dropped.
# 0.3 keeps the weakest real keyword hit above threshold.
FTS_SCORE_FLOOR = 0.3
DEFAULT_MAX_TOKENS = 15_000
_CHARS_PER_TOKEN = 4  # rough estimate; code-heavy content may be ~2-3 chars/token


# ── RecallEngine ──────────────────────────────────────────────────────


class RecallEngine:
    """Keyword search engine over the Knowledge Library + optional stores.

    Pure FTS5+BM25 keyword search (the sqlite-vec vector leg was removed
    2026-08-14 — see the module docstring). Formats output with provenance
    for system prompt injection.

    Supports additional stores (e.g. TranscriptStore) for unified search.
    """

    def __init__(self, store: "KnowledgeStore", additional_stores: list = None):
        """
        Args:
            store: KnowledgeStore instance with tables already ensured.
            additional_stores: Optional list of additional stores (e.g.
                TranscriptStore) that implement fts5_search() with the same
                return format (recall is FTS5-only; no vector_search).
        """
        self._store = store
        self._additional_stores = additional_stores or []

    def search(
        self,
        query: str,
        embed_fn=None,  # DEAD param kept for signature compat — recall is FTS5-only
        top_k: int = 20,
        threshold: float = RECALL_THRESHOLD,
    ) -> list[dict]:
        """Pure FTS5+BM25 keyword search across all stores, merged and ranked.

        Recall is keyword-only (the vector leg was removed 2026-08-14 — see PRI11:
        FTS5-only, zero-embedding is the intended architecture). ``embed_fn`` is an
        inert parameter retained only so existing call sites don't break; it is
        ignored.

        Args:
            query: Natural language query string.
            embed_fn: IGNORED (dead — see above).
            top_k: Max results per store.
            threshold: Minimum recall score to include.

        Returns:
            List of result dicts with keys: id, source_file, heading,
            content, recall_score, fts_score.
        """
        if not query or not query.strip():
            return []

        all_stores = [self._store] + self._additional_stores
        fts_scored: dict[str, dict] = {}  # keyed by "storeidx:rowid"

        # Per-leg failure tracking (run_4d06640b Gate-2 HIGH-2): RecallEngine used
        # to swallow per-leg errors to [] and return an empty list that was
        # INDISTINGUISHABLE from a genuine no-match → silent dead recall. We count
        # leg failures + expose them via ``last_search_errors`` so the caller can
        # tell "degraded" (errored) apart from "no match" (clean empty).
        self.last_search_errors: list[str] = []

        for store_idx, store in enumerate(all_stores):
            prefix = f"s{store_idx}:"
            try:
                fts_results = store.fts5_search(query, limit=top_k)
            except Exception as exc:  # noqa: BLE001 — surfaced via last_search_errors
                fts_results = []
                self.last_search_errors.append(f"fts[{store_idx}]:{type(exc).__name__}")

            if fts_results:
                min_rank = min(r["fts_rank"] for r in fts_results)
                max_rank = max(r["fts_rank"] for r in fts_results)
                rank_range = max_rank - min_rank if max_rank != min_rank else 1.0

                for r in fts_results:
                    norm = 1.0 - (r["fts_rank"] - min_rank) / rank_range if rank_range else 1.0
                    # Floor the normalized score so the WORST keyword match is not
                    # zeroed. With pure min-max the lowest-ranked hit gets 0.0 <
                    # RECALL_THRESHOLD → silently DROPPED. Map [0,1] →
                    # [FTS_SCORE_FLOOR,1] so a real match always clears the
                    # threshold. (run_bbd79e84 Gate-2)
                    score = FTS_SCORE_FLOOR + (1.0 - FTS_SCORE_FLOOR) * norm
                    key = f"{prefix}{r['id']}"
                    fts_scored[key] = {**r, "fts_score": score}

        # Merge + dedup across all stores (keyword-only → recall_score == fts_score).
        merged: list[dict] = []
        seen_content: set[str] = set()
        for key, entry in fts_scored.items():
            recall_score = entry.get("fts_score", 0.0)
            if recall_score < threshold:
                continue
            content = entry.get("content", "")
            content_key = hashlib.sha256(content.encode()).hexdigest()
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            merged.append({
                "id": entry.get("id", 0),
                "source_file": entry.get("source_file", ""),
                "heading": entry.get("heading", ""),
                "content": content,
                "recall_score": recall_score,
                "fts_score": recall_score,
            })

        merged.sort(key=lambda r: r["recall_score"], reverse=True)
        return merged

    def recall_knowledge(
        self,
        query: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        """Search and format results for system prompt injection.

        Returns formatted markdown text with provenance, or empty string
        if no relevant results found.

        Args:
            query: Search query (typically focus keywords from proactive briefing).
            max_tokens: Token budget for the recalled content.

        Returns:
            Formatted string ready for prompt injection, or "".
        """
        results = self.search(query)

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
