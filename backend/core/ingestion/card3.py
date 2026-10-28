"""Read-only CDF card 3 retrieval and relation aggregation.

- ``retrieve`` unions fingerprint, title/anchor, and BM25 top-eight hits.
- ``relation_for_hit`` implements the r15 per-hit precedence.
- ``aggregate_hits`` is deterministic and independent of hit order.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from core.memory_index import _bm25_scores

from .card0 import content_fingerprint
from .contracts import Destination, EVIDENCE_ORDER, HitJudgment, RetrievalHit, RetrievalResult
from .normalize import ClaimGroup
from .units import Unit, parse_units

CARD3_VERSION = "cdf-card3-r15.1-v1"
_ALWAYS = ("MEMORY.md", "EVOLUTION.md", "STEERING.md", "USER.md", "TOOLS.md", "SOUL.md", "AGENT.md")


@dataclass(frozen=True)
class HitRelation:
    """One computed relation, preserving target identity and score."""

    relation: str
    anchor: str
    score: float


@dataclass(frozen=True)
class AggregateAction:
    """The deterministic action selected from all hit relations."""

    action: str
    reason: str
    target_anchor: str | None = None


def corpus_paths(root: Path, destination: Destination) -> list[Path]:
    """Return the bounded read-only corpus paths for one destination."""
    paths: set[Path] = {root / ".context" / name for name in _ALWAYS}
    target = root / destination.relative_path
    if target.is_file():
        paths.add(target)
    elif target.is_dir():
        paths.update(p for p in target.rglob("*.md") if p.is_file())
    if destination.store == "DDD" and destination.project:
        from core.ddd_paths import ddd_path  # read resolver: new layout, old-layout fallback

        project = root / "Projects" / destination.project
        from core.project_registry import DDD_CANONICAL_DOCS  # the one DDD four-doc list
        paths.update(ddd_path(project, name) for name in DDD_CANONICAL_DOCS)
    if destination.store in {"DDD", "COMMON_DDD"}:
        learned = root / "Knowledge" / "Learned"
        for folder in (learned / "domains", learned / "repos"):
            if folder.is_dir():
                paths.update(item for item in folder.glob("*.md") if item.is_file())
    # A rerouted _common destination searches its original per-turn file.  Those files
    # are already in _ALWAYS.  Conversely a per-turn destination never adds _common.
    return sorted(path for path in paths if path.is_file())


def load_corpus(root: Path, destination: Destination, *, exclude_anchor: str | None = None) -> tuple[list[Unit], str]:
    """Parse the selected snapshot without writing or touching metadata."""
    units: list[Unit] = []
    digest = hashlib.sha256()
    for path in corpus_paths(root, destination):
        file_units = parse_units(path, root=root)
        for unit in file_units:
            digest.update(unit.anchor.encode("utf-8"))
            digest.update(unit.byte_hash.encode("ascii"))
            if unit.anchor != exclude_anchor:
                units.append(unit)
    return units, digest.hexdigest()


def retrieve(
    group: ClaimGroup,
    root: Path,
    destination: Destination,
    *,
    expected_digest: str | None = None,
    exclude_anchor: str | None = None,
    evidence_by_anchor: Mapping[str, tuple[str, str | None]] | None = None,
) -> RetrievalResult:
    """Retrieve the union of exact, title/anchor, and BM25 top-eight candidates."""
    try:
        units, digest = load_corpus(root, destination, exclude_anchor=exclude_anchor)
    except (OSError, UnicodeError, ValueError) as exc:
        return RetrievalResult(status="UNAVAILABLE", reason=type(exc).__name__)
    if expected_digest is not None and expected_digest != digest:
        return RetrievalResult(status="STALE", corpus_digest=digest, reason="corpus_digest_mismatch")
    if not units:
        return RetrievalResult(status="OK_EMPTY", corpus_digest=digest)

    docs = {unit.anchor: unit.text for unit in units}
    scores = _bm25_scores(group.text, docs)
    by_anchor = {unit.anchor: unit for unit in units}
    selected: set[str] = set()
    wanted_fingerprint = content_fingerprint(group.text)
    for unit in units:
        if content_fingerprint(unit.text) == wanted_fingerprint:
            selected.add(unit.anchor)
        if group.title and group.title.casefold() in {unit.title.casefold(), unit.anchor.casefold()}:
            selected.add(unit.anchor)
    selected.update(anchor for anchor, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:8])
    if not selected:
        return RetrievalResult(status="OK_EMPTY", corpus_digest=digest)
    metadata = evidence_by_anchor or {}
    hits = []
    for anchor in sorted(selected):
        unit = by_anchor[anchor]
        evidence, observed_at = metadata.get(anchor, ("inferred", None))
        hits.append(RetrievalHit(anchor=anchor, text=unit.text, score=scores.get(anchor, 0.0),
                                 identity=unit.identity, byte_hash=unit.byte_hash,
                                 evidence=evidence, observed_at=observed_at,
                                 baseline=not unit.identity.startswith("cand:")))
    hits.sort(key=lambda hit: (-hit.score, hit.anchor))
    return RetrievalResult(status="OK_HITS", hits=hits, corpus_digest=digest)


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def relation_for_hit(hit: HitJudgment) -> HitRelation:
    """Compute the first matching normative relation for one hit."""
    verdicts = {item.verdict for item in hit.claim_verdicts}
    refutes = "refutes" in verdicts
    if refutes and hit.scope_relation == "narrower":
        relation = "exception"
    elif refutes and hit.scope_relation in {"equal", "wider"}:
        candidate_strength = EVIDENCE_ORDER[hit.candidate_evidence]
        existing_strength = EVIDENCE_ORDER[hit.existing_evidence]
        if candidate_strength > existing_strength:
            relation = "replace"
        elif candidate_strength == existing_strength:
            candidate_time = _time(hit.candidate_observed_at)
            existing_time = _time(hit.existing_observed_at)
            relation = "replace" if candidate_time and existing_time and candidate_time > existing_time else "blocked"
        else:
            relation = "blocked"
    elif refutes and hit.scope_relation == "disjoint":
        relation = "independent"
    elif hit.overlap in {"same", "contained"} and not refutes:
        relation = "duplicate"
    elif hit.same_topic and not refutes:
        relation = "refine"
    else:
        relation = "independent"
    return HitRelation(relation=relation, anchor=hit.anchor, score=hit.score)


def aggregate_hits(relations: Iterable[HitRelation]) -> AggregateAction:
    """Aggregate hit relations with order-independent deterministic tie breaks."""
    items = tuple(relations)
    kinds = {item.relation for item in items}
    if "blocked" in kinds:
        target = min(item.anchor for item in items if item.relation == "blocked")
        return AggregateAction("reject", "conflicts_stronger", target)
    replaces = [item for item in items if item.relation == "replace"]
    if (replaces or "exception" in kinds) and "duplicate" in kinds:
        return AggregateAction("reject", "incompatible_hits")
    if len(replaces) >= 2:
        return AggregateAction("reject", "incompatible_hits")
    if len(replaces) == 1:
        return AggregateAction("replace", "replace", replaces[0].anchor)
    if "duplicate" in kinds:
        target = min(item.anchor for item in items if item.relation == "duplicate")
        return AggregateAction("reject", "already_exists", target)
    exceptions = [item for item in items if item.relation == "exception"]
    if exceptions:
        target = min(item.anchor for item in exceptions)
        return AggregateAction("add_exception", "exception", target)
    refinements = [item for item in items if item.relation == "refine"]
    if refinements:
        target = sorted(refinements, key=lambda item: (-item.score, item.anchor))[0]
        return AggregateAction("refine", "refine", target.anchor)
    return AggregateAction("add", "independent")
