"""Governance proposal sink — the one file proposals are written to and read from.

``.context/.evolution_proposals.json`` holds governance proposals for the human.
The only producer is the governance miner's GC path (evolution cycle, Phase 3c);
the readers are eval_service (dashboard list and accept/reject), the overlay's
Approve count and the Need-You cards. This module NEVER writes SOUL/AGENT/STEERING.

The automatic chain that used to live here (classify every correction with a
model, count it into a bucket, escalate a bucket count into a rule/gate
proposal) was deleted: a bucket count carries no text a human can act on
(docs/design/2026-10-26-evolution-loop-admission.md §3.1).

Key public symbols:
    _append_proposal — flock-safe, identity-deduped append
    remove_governance_proposal — flock-safe removal by id
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _flock(fd, exclusive=True):
    try:
        from utils.file_lock import flock_exclusive

        flock_exclusive(fd)
    except ImportError:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _funlock(fd):
    try:
        from utils.file_lock import flock_unlock

        flock_unlock(fd)
    except ImportError:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _default_proposals_path() -> Path:
    # The governance-proposal sink, read by eval_service / the dashboard / Need-You.
    return Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / ".evolution_proposals.json"


def _append_proposal(proposal: dict, proposals_path: Path) -> None:
    """Append a governance proposal under an exclusive lock, kind-aware dedup.

    Identity = (source_class, proposal_kind) so a rule and a gate proposal for the
    same class coexist (never overwrite each other). Re-reads under lock.
    """
    proposals_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = proposals_path.with_suffix(".json.lock")
    fd = open(lock_path, "w")
    try:
        _flock(fd)
        existing: list = []
        if proposals_path.exists():
            try:
                loaded = json.loads(proposals_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing = loaded
            except (json.JSONDecodeError, OSError):
                existing = []
        # Dedup identity: gc_id (if present) OR (source_class, proposal_kind).
        # Mirrors the optimizer's identity so both writers converge — a rule and a
        # gate proposal for the same class coexist; same-identity replaces.
        gc_id = proposal.get("gc_id")
        src = proposal.get("source_class")
        kind = proposal.get("proposal_kind", "rule")
        # Stamp a stable id = the dedup identity itself (gc_id, or source:kind).
        # Derived-but-persisted: stable across reads (Gate-1 Phase-3 Check-1 — the
        # accept/reject API targets this id; the raw items had no unique key).
        proposal["id"] = gc_id or f"{src}:{kind}"
        existing = [
            p
            for p in existing
            if not (
                p.get("target") == "governance"
                and (
                    (gc_id and p.get("gc_id") == gc_id)
                    or (
                        src
                        and p.get("source_class") == src
                        and p.get("proposal_kind", "rule") == kind
                    )
                )
            )
        ]
        existing.append(proposal)
        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=proposals_path.parent, suffix=".tmp", delete=False, encoding="utf-8"
        )
        try:
            json.dump(existing, tmp, indent=2, ensure_ascii=False)
            tmp.close()
            Path(tmp.name).replace(proposals_path)
        except Exception:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)
            raise
    finally:
        _funlock(fd)
        fd.close()


def remove_governance_proposal(proposal_id: str, proposals_path: Path | None = None) -> bool:
    """Remove ONE governance proposal by id, under the SHARED flock.

    The dashboard accept/reject path must mutate .evolution_proposals.json through
    the SAME lock _append_proposal uses — otherwise an unlocked read-modify-write
    races the miner's writer and loses a write. Re-reads under the lock, removes
    exactly the first id match (not all — adversarial LOW), atomic tmp+replace.

    Non-governance rows (skill-optimization) and other governance rows are preserved.
    Returns True if a row was removed.
    """
    proposals_path = proposals_path or _default_proposals_path()
    if not proposals_path.exists():
        return False
    lock_path = proposals_path.with_suffix(".json.lock")
    fd = open(lock_path, "w")
    removed = False
    try:
        _flock(fd)
        try:
            rows = json.loads(proposals_path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except (json.JSONDecodeError, OSError):
            rows = []
        # Remove exactly the FIRST matching id (governance row), preserve the rest.
        out, dropped = [], False
        for r in rows:
            if (
                not dropped
                and isinstance(r, dict)
                and r.get("target") == "governance"
                and (r.get("id") or r.get("gc_id")
                     or f"{r.get('source_class')}:{r.get('proposal_kind', 'rule')}") == proposal_id
            ):
                dropped = True
                removed = True
                continue
            out.append(r)
        if removed:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", dir=proposals_path.parent, suffix=".tmp", delete=False, encoding="utf-8"
            )
            try:
                json.dump(out, tmp, indent=2, ensure_ascii=False)
                tmp.close()
                Path(tmp.name).replace(proposals_path)
            except Exception:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise
    finally:
        _funlock(fd)
        fd.close()
    return removed
