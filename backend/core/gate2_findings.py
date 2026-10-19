"""Gate-2 findings: the one classifier every Gate-2 reader calls (pure functions).

Every reader that has to decide what a Gate-2 finding means calls the classifier
below instead of carrying its own severity set and confidence parser:

- ``pipeline_validator._blocked_findings``: does an UNRESOLVED finding block
  delivery/completion? (``is_blocking``)
- ``artifact_cli._binding_findings``: must a finding an expert reported appear in
  the DELIVER record? (``must_record``)
- ``artifact_cli._recheck_items``: is a fixed finding a re-check item? (``is_blocking``)

The rules keep each gate's strictness as it was, and close two holes:

- severity HIGH / CRITICAL / BLOCK / BLOCKER: blocking at ANY confidence (the
  Shared-State Override in gate2-findings.md cannot be judged by code, so a recorded
  low-confidence HIGH blocks until fixed or deliberately deferred); must-record at
  confidence >= 5 or missing (the documented 3-4 appendix and 1-2 drop tiers);
- severity MED / MEDIUM: blocking and must-record at confidence >= 7, OR when the
  confidence is missing or unparseable (fail closed: a gate must not be dodgeable by
  omitting a field). The re-check reader used to read a missing confidence as 0;
- LOW and anything else: neither;
- confidence = the first number in the value ("8/10" -> 8.0), None when there is none.

The identity of a reported finding (``finding_key``, ``entry_id``) lives here too: the
findings ledger, the fixing spec and every check that maps one to the other compute
it with these two functions, never their own copy.

Public symbols: ``severity_of``, ``severity_rank``, ``confidence_of``, ``is_blocking``,
``finding_key``, ``entry_id``,
``must_record``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# The same HIGH words pipeline_validator's Red Team trigger reads (_HIGH_SEVERITY_WORDS)
HIGH_SEVERITIES = frozenset({"HIGH", "CRITICAL", "CRIT", "BLOCKER", "BLOCK", "BLOCKING", "P0", "P1"})
MED_SEVERITIES = frozenset({"MED", "MEDIUM"})
MED_THRESHOLD = 7            # a MED at or above this confidence is fixed and re-checked
HIGH_RECORD_THRESHOLD = 5    # below this a HIGH may go to the appendix tier

_NUM_RE = re.compile(r"\s*(\d+(?:\.\d+)?)")


def severity_of(f: Any) -> str:
    """'high' | 'med' | 'low' | '' for a finding dict (or a bare severity string).

    The label is read by words before any "(" (as the Red Team trigger reads it), the
    most severe word winning: "HIGH (conf 9)", "P0", "MEDIUM-HIGH" → high; "MED (shared
    state)", "Medium" → med."""
    raw = f.get("severity") if isinstance(f, dict) else f
    words = set(re.findall(r"[A-Z0-9]+", str(raw or "").upper().split("(", 1)[0]))
    if words & HIGH_SEVERITIES:
        return "high"
    if words & MED_SEVERITIES:
        return "med"
    if "LOW" in words:
        return "low"
    return ""


def severity_rank(f: Any) -> int:
    """2 high, 1 med, 0 low or unknown."""
    return {"high": 2, "med": 1}.get(severity_of(f), 0)


def confidence_of(v: Any) -> Optional[float]:
    """The first number in a confidence value ("8/10" -> 8.0); None when there is none."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.match(str(v if v is not None else ""))
    return float(m.group(1)) if m else None


def is_blocking(f: dict, med_threshold: float = MED_THRESHOLD) -> bool:
    """Would this finding block delivery while unresolved?"""
    sev = severity_of(f)
    if sev == "high":
        return True
    if sev == "med":
        conf = confidence_of(f.get("confidence"))
        return conf is None or conf >= med_threshold
    return False


def must_record(f: dict) -> bool:
    """Must a reported finding appear in the DELIVER record (main findings, not appendix)?"""
    sev = severity_of(f)
    conf = confidence_of(f.get("confidence"))
    if sev == "high":
        return conf is None or conf >= HIGH_RECORD_THRESHOLD
    if sev == "med":
        return conf is None or conf >= MED_THRESHOLD
    return False


def finding_key(r: dict) -> tuple:
    """A reported finding's identity: path, line, category, and (only when the expert
    gave no path) the start of its summary."""
    path = r.get("path") or r.get("file")
    return ((path or ""), r.get("line"), (r.get("category") or "").lower(),
            (r.get("summary") or "")[:60] if not path else "")


def entry_id(expert_id: str, r: dict) -> str:
    """The ledger id of finding *r* reported by *expert_id*: stable across rounds and
    severity changes (``<expert>-<6 hex>``)."""
    import hashlib
    key = (expert_id, *finding_key(r))
    return f"{expert_id}-{hashlib.sha1(repr(key).encode('utf-8')).hexdigest()[:6]}"
