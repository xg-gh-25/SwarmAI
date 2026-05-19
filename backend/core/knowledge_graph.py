"""Knowledge Graph — cross-entry relation layer for DDD knowledge.

Provides typed, temporal edges between knowledge entries (MEMORY, IMPROVEMENT,
EVOLUTION, Knowledge/) enabling 2-hop semantic retrieval. Replaces "global
popularity" injection with "contextual relevance" in pipeline stages.

Storage: .context/.knowledge-graph.yaml (YAML sidecar, agent-owned)
Scale: ~30-200 relations (hand-curated, no auto-extraction in v1)

Public API:
    Relation          — dataclass for a single edge
    load_graph(path)  → list[Relation]
    save_graph(path, relations)
    add_relation(path, s, p, o) → Relation
    expire_relation(path, s, p, o)
    touch_relation(path, s, p, o)
    query_relations(relations, entity, ...) → list[Relation]
    query_related_entries(relations, entities) → list[str]
    VALID_PREDICATES  — set of allowed predicate names
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import logging

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    logger.warning("PyYAML not available — knowledge_graph will return empty results")
    yaml = None  # type: ignore


# ── Constants ────────────────────────────────────────────────────────────────

VALID_PREDICATES = frozenset({
    "motivated_by",     # A exists because of B
    "supersedes",       # A replaces B
    "extends",          # A builds on B
    "addresses",        # A solves problem described in B
    "serves_thesis",    # A serves thesis B
    "applies_to",       # knowledge applies to module/file
    "conflicts_with",   # A contradicts B
    "requires",         # A depends on B
    "informs",          # A provides input to B
    "produced_by",      # A was output of process B
})

# Default stale threshold (days since last_used)
DEFAULT_STALE_THRESHOLD_DAYS = 180


# ── Dataclass ────────────────────────────────────────────────────────────────


@dataclass
class Relation:
    """A single directed edge between two knowledge entries."""
    subject: str            # source entry (ID or title)
    predicate: str          # relation type (must be in VALID_PREDICATES)
    object: str             # target entry (ID, title, or filename)
    created: date           # when the relation was first established
    last_used: date         # last time this relation was confirmed/referenced
    expired: Optional[date] = None  # if set, relation is dead

    def is_stale(self, threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
                 today: Optional[date] = None) -> bool:
        """Check if the relation is stale (old but not expired)."""
        if self.expired:
            return False  # Expired is a different state
        _today = today or date.today()
        return (_today - self.last_used).days > threshold_days

    def is_active(self, today: Optional[date] = None) -> bool:
        """Check if the relation is active (not expired, not stale by default)."""
        if self.expired:
            return False
        return True


# ── Public API ───────────────────────────────────────────────────────────────


def load_graph(path: Path) -> list[Relation]:
    """Load relations from a .knowledge-graph.yaml file.

    Returns empty list if file doesn't exist or is empty/invalid.
    """
    if yaml is None:
        return []
    if not path.exists():
        return []

    try:
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return []
        data = yaml.safe_load(content)
        if not data or not isinstance(data, dict):
            return []
    except (yaml.YAMLError, OSError):
        return []

    raw_relations = data.get("relations", [])
    if not raw_relations or not isinstance(raw_relations, list):
        return []

    relations: list[Relation] = []
    for r in raw_relations:
        if not isinstance(r, dict):
            continue
        try:
            relations.append(Relation(
                subject=str(r.get("s", "")),
                predicate=str(r.get("p", "")),
                object=str(r.get("o", "")),
                created=_parse_date(r.get("c")),
                last_used=_parse_date(r.get("u")),
                expired=_parse_date(r.get("e")) if r.get("e") else None,
            ))
        except (ValueError, TypeError):
            continue  # Skip malformed entries

    return relations


def save_graph(path: Path, relations: list[Relation]) -> None:
    """Save relations to a .knowledge-graph.yaml file.

    Uses atomic write (write to temp + rename) to prevent corruption on crash.
    Overwrites the file completely. Creates parent dirs if needed.
    """
    import os
    import tempfile

    if yaml is None:
        return

    data = {
        "version": 1,
        "updated": date.today().isoformat(),
        "relations": [
            _relation_to_dict(r) for r in relations
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(data, default_flow_style=False, allow_unicode=True,
                             sort_keys=False)

    # F6 fix: Atomic write — temp file + rename prevents corruption on crash
    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp"
        )
        os.write(tmp_fd, content.encode("utf-8"))
        os.close(tmp_fd)
        tmp_fd = None
        os.replace(tmp_path, str(path))
    except Exception:
        if tmp_fd is not None:
            os.close(tmp_fd)
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def add_relation(path: Path, s: str, p: str, o: str) -> Relation:
    """Add a new relation to the graph. Creates file if needed.

    If an identical (s, p, o) triple already exists, touches it instead
    of creating a duplicate. Validates predicate against VALID_PREDICATES.

    Returns the created or existing Relation.
    """
    # F1 fix: validate predicate
    if p not in VALID_PREDICATES:
        raise ValueError(
            f"Invalid predicate '{p}'. Must be one of: {sorted(VALID_PREDICATES)}"
        )

    relations = load_graph(path)
    today = date.today()

    # F2 fix: check for existing duplicate
    for r in relations:
        if r.subject == s and r.predicate == p and r.object == o:
            r.last_used = today
            if r.expired:
                r.expired = None  # Un-expire if re-added
            save_graph(path, relations)
            return r

    new_rel = Relation(
        subject=s, predicate=p, object=o,
        created=today, last_used=today,
    )
    relations.append(new_rel)
    save_graph(path, relations)
    return new_rel


def expire_relation(path: Path, s: str, p: str, o: str) -> bool:
    """Mark a relation as expired (sets the `e` field to today).

    Returns True if a matching relation was found and expired, False otherwise.
    """
    relations = load_graph(path)
    today = date.today()
    found = False
    for r in relations:
        if r.subject == s and r.predicate == p and r.object == o:
            r.expired = today
            found = True
            break
    # F7 fix: only save if a match was found
    if found:
        save_graph(path, relations)
    return found


def touch_relation(path: Path, s: str, p: str, o: str) -> bool:
    """Update last_used to today for a specific relation.

    Returns True if a matching relation was found, False otherwise.
    """
    relations = load_graph(path)
    today = date.today()
    found = False
    for r in relations:
        if r.subject == s and r.predicate == p and r.object == o:
            r.last_used = today
            found = True
            break
    # F7 fix: only save if a match was found
    if found:
        save_graph(path, relations)
    return found


def query_relations(
    relations: list[Relation],
    entity: str,
    max_hops: int = 1,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> list[Relation]:
    """Find all non-expired relations involving an entity (as subject or object).

    Returns relations where entity appears as subject OR object.
    Excludes expired relations. Stale relations are included but can be
    identified via `relation.is_stale()`.

    Args:
        relations: All relations to search
        entity: The entity to find (matched against subject and object)
        max_hops: Currently only 1-hop supported
        stale_threshold_days: Days after which a relation is considered stale
    """
    results: list[Relation] = []
    entity_lower = entity.lower()

    for r in relations:
        if r.expired:
            continue  # AC4: expired excluded
        if (r.subject.lower() == entity_lower or
                r.object.lower() == entity_lower):
            results.append(r)

    return results


def query_related_entries(
    relations: list[Relation],
    entities: list[str],
    max_hops: int = 1,
) -> list[str]:
    """Given file/module/entry names, return related entry IDs/titles via 1-hop.

    Searches for relations where any entity in the list appears as object,
    then returns the subjects of those relations (the "related entries").
    Also finds relations where entities appear as subjects and returns objects.

    Excludes expired relations.

    Args:
        relations: All relations to search
        entities: List of entity identifiers (file names, entry IDs, etc.)

    Returns:
        Deduplicated list of related entry identifiers (subjects and objects).
    """
    related: set[str] = set()
    entities_lower = {e.lower() for e in entities}

    for r in relations:
        if r.expired:
            continue
        if r.object.lower() in entities_lower:
            related.add(r.subject)
        elif r.subject.lower() in entities_lower:
            related.add(r.object)

    # Remove the input entities themselves from results
    return [e for e in related if e.lower() not in entities_lower]


# ── Private Helpers ──────────────────────────────────────────────────────────


def _parse_date(val) -> date:
    """Parse a date from YAML value (string or date object).

    Handles datetime.datetime subclass (PyYAML returns this for datetime strings)
    by extracting the date component to prevent format drift on save roundtrip.
    """
    import datetime as dt
    if isinstance(val, dt.datetime):
        # F5 fix: datetime subclass → extract date to prevent isoformat drift
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val)
    raise ValueError(f"Cannot parse date: {val!r}")


def _relation_to_dict(r: Relation) -> dict:
    """Convert a Relation to a YAML-serializable dict."""
    d = {
        "s": r.subject,
        "p": r.predicate,
        "o": r.object,
        "c": r.created.isoformat(),
        "u": r.last_used.isoformat(),
    }
    if r.expired:
        d["e"] = r.expired.isoformat()
    return d
