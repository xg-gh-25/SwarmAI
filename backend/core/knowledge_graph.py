"""Knowledge Graph — cross-entry relation layer for DDD knowledge.

Provides typed, temporal edges between knowledge entries (MEMORY, IMPROVEMENT,
EVOLUTION, Knowledge/) enabling 2-hop semantic retrieval. Replaces "global
popularity" injection with "contextual relevance" in pipeline stages.

Storage: .context/.knowledge-graph.yaml (YAML sidecar, agent-owned)
Scale: ~100-500 relations (auto-extracted from pipeline usage + backfill)

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

import re
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
    Uses fcntl advisory lock to prevent concurrent write corruption.

    Returns the created or existing Relation.
    """
    # F1 fix: validate predicate
    if p not in VALID_PREDICATES:
        raise ValueError(
            f"Invalid predicate '{p}'. Must be one of: {sorted(VALID_PREDICATES)}"
        )

    _result_rel: list[Relation] = []  # Capture the result from inside closure

    def _mutate(relations: list[Relation]) -> tuple[list[Relation], None]:
        today = date.today()
        # F2 fix: check for existing duplicate
        for r in relations:
            if r.subject == s and r.predicate == p and r.object == o:
                r.last_used = today
                if r.expired:
                    r.expired = None  # Un-expire if re-added
                _result_rel.append(r)
                return relations, None
        new_rel = Relation(subject=s, predicate=p, object=o,
                           created=today, last_used=today)
        relations.append(new_rel)
        _result_rel.append(new_rel)
        return relations, None

    _locked_read_modify_write(path, _mutate)
    return _result_rel[0] if _result_rel else Relation(s, p, o, date.today(), date.today())


def expire_relation(path: Path, s: str, p: str, o: str) -> bool:
    """Mark a relation as expired (sets the `e` field to today).

    Uses fcntl advisory lock. Returns True if match found, False otherwise.
    """
    found = False

    def _mutate(relations: list[Relation]) -> tuple[list[Relation], None]:
        nonlocal found
        today = date.today()
        for r in relations:
            if r.subject == s and r.predicate == p and r.object == o:
                r.expired = today
                found = True
                break
        return relations, None

    _locked_read_modify_write(path, _mutate, skip_save_if_unchanged=True,
                              changed_flag=lambda: found)
    return found


def touch_relation(path: Path, s: str, p: str, o: str) -> bool:
    """Update last_used to today for a specific relation.

    Uses fcntl advisory lock. Returns True if match found, False otherwise.
    """
    found = False

    def _mutate(relations: list[Relation]) -> tuple[list[Relation], None]:
        nonlocal found
        today = date.today()
        for r in relations:
            if r.subject == s and r.predicate == p and r.object == o:
                r.last_used = today
                found = True
                break
        return relations, None

    _locked_read_modify_write(path, _mutate, skip_save_if_unchanged=True,
                              changed_flag=lambda: found)
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
    today = date.today()

    for r in relations:
        if r.expired:
            continue  # AC4: expired excluded
        if (r.subject.lower() == entity_lower or
                r.object.lower() == entity_lower):
            results.append(r)

    # Sort: fresh relations before stale (stale = last_used > threshold days ago)
    results.sort(key=lambda r: (today - r.last_used).days > stale_threshold_days)
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


# ── Backfill & Auto-Extraction ────────────────────────────────────────────────

# Pattern to detect file/module mentions in entry text
# Matches: word.py, word.ts, word.md, word.sh, word.rs, word.yaml, word.json
# Minimum 5 chars total (e.g., "main.py") to avoid false positives
_FILE_PATTERN = re.compile(
    r'\b([\w\-/]+\.(?:py|ts|tsx|rs|sh|md|yaml|yml|json|toml))\b'
)

# Minimum file name length to avoid noise like "a.py" or "io.py"
_MIN_FILE_NAME_LEN = 6


def backfill_from_entries(entries: list, graph_path: Path) -> int:
    """Scan entry raw_text for file/module mentions and generate relations.

    For each entry, extracts file patterns (*.py, *.ts, etc.) from raw_text
    and creates `applies_to` relations. Skips very short filenames.
    Uses add_relation's built-in dedup to avoid duplicates.

    Args:
        entries: List of EntryMetadata objects (from ddd_entry_lifecycle.parse_entries)
        graph_path: Path to .knowledge-graph.yaml

    Returns:
        Number of new relations created.
    """
    created = 0
    for entry in entries:
        raw = entry.raw_text or entry.title
        files_found = _FILE_PATTERN.findall(raw)
        for fname in files_found:
            # Skip very short file names (false positives)
            if len(fname) < _MIN_FILE_NAME_LEN:
                continue
            # Use basename only (strip path prefixes)
            basename = fname.split("/")[-1] if "/" in fname else fname
            if len(basename) < _MIN_FILE_NAME_LEN:
                continue
            # Use first 60 chars of title as subject (keep YAML readable)
            subject = entry.title[:60]
            try:
                rel = add_relation(graph_path, subject, "applies_to", basename)
                if rel.created == date.today():  # Newly created (not dedup'd)
                    created += 1
            except ValueError:
                continue  # Invalid predicate (shouldn't happen for "applies_to")
    return created


# ── Locking ──────────────────────────────────────────────────────────────────


def _locked_read_modify_write(
    path: Path,
    mutate_fn,
    skip_save_if_unchanged: bool = False,
    changed_flag=None,
) -> list[Relation]:
    """Execute a read-modify-write cycle under fcntl advisory lock.

    Prevents concurrent writers from corrupting the YAML file.
    Lock is on a sibling .lock file (not the YAML itself) to allow
    atomic rename in save_graph.

    Args:
        path: Path to the .knowledge-graph.yaml file
        mutate_fn: Callable that takes list[Relation] and returns
                   (modified_relations, extra_return_value)
        skip_save_if_unchanged: If True, only save when changed_flag() returns True
        changed_flag: Callable returning bool (used with skip_save_if_unchanged)

    Returns:
        The modified relations list.
    """
    import fcntl

    lock_path = path.parent / (path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = None

    try:
        lock_fd = open(lock_path, "w")
        # Blocking lock — waits until available (serializes concurrent writers)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        relations = load_graph(path)
        relations, _ = mutate_fn(relations)

        should_save = True
        if skip_save_if_unchanged and changed_flag:
            should_save = changed_flag()

        if should_save:
            save_graph(path, relations)

        return relations

    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
            lock_fd.close()


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
