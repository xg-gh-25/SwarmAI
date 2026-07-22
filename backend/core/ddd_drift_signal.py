"""DDD semantic-drift signal — the read-only bridge from ddd-self-audit → eval/dashboard.

Closes the DDD-drift → eval feedback loop (option B-minimal, run_562f45c7). The weekly
`ddd_self_audit` job produces a per-project SEMANTIC-drift report (internal
contradictions / superseded claims that a grep-detector cannot catch — run_b2e85d61).
Those findings previously reached ONLY Radar todos + the archived report; they never
surfaced on the Eval OS Dashboard and never touched eval.

This helper is the missing read-side: it parses the LATEST self-audit report and maps
each finding to the golden cases at risk (the cases whose `affected_by` cites the same
DDD doc — so a drifted doc flags the cases whose judgment depends on it). It invents NO
scoring: drift influences eval through the EXISTING `affected_by`/gate_refs chain
(a case pointed at a drifted doc is at risk of stale-context failure), not by
subtracting from any score, and it seeds NO draft cases (that would inflate
intelligence_velocity's growth_score — drift would perversely raise the health number).

DESIGN INVARIANTS (Gate-0/Gate-1 verified):
- READ-ONLY. No file writes, no persisted drift count (R30#4: a dynamic decision-inert
  number must never be stored — it is computed LIVE per request).
- GRACEFUL. Finding titles are LLM-generated: a title may lack the `[project/DOC]` tag,
  and a report section may be `review failed` / `timed out` / `0 finding(s)` with no
  RADAR_TODOS block. None of these crash — they degrade to "no finding" / project=None.
- PROJECT-AWARE mapping. Deliberately NOT `eval_service.get_affected_cases` (it
  filename-normalizes `TECH.md` and would cross-match across all 7 projects). Matches
  the finding's project+doc against `affected_by`, tolerating BOTH the prefixed form
  (`Projects/SwarmAI/TECH.md`) and the bare form (`SwarmAI/TECH.md`).

Reuses (does NOT reimplement) `ddd_self_audit._count_parseable_findings` — the same
RADAR_TODOS JSON parser the producer uses, so read and write stay in lock-step.
"""

from __future__ import annotations

import re
from pathlib import Path

# Report filename pattern: 2026-07-20-ddd-self-audit.md → the date prefix sorts + dates it.
_REPORT_GLOB = "*-ddd-self-audit.md"
_REPORT_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-ddd-self-audit\.md$")

# A finding title tag: "DDD drift [Project/DOC.md]: ..." or "[Project/A.md vs B.md]".
# The bracket content is `<project>/<doc-expr>`; doc-expr may list several docs joined
# by " vs " / "," (the multi-doc contradiction shape).
_TITLE_TAG_RE = re.compile(r"\[([^\]/]+)/([^\]]+)\]")
_DOC_RE = re.compile(r"[A-Za-z_]+\.md")

# A per-project section header in the report body: "## PhysicalAI (non-code) — 1 finding(s)"
# or "## AIDLC — ⚠️ review failed". We split on these to attribute each RADAR_TODOS
# block to the project section that precedes it.
_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def _latest_report(root: Path) -> Path | None:
    """Return the newest ddd-self-audit report under Knowledge/JobResults/, or None."""
    jr = root / "Knowledge" / "JobResults"
    if not jr.is_dir():
        return None
    reports = sorted(jr.glob(_REPORT_GLOB))  # date-prefixed filenames sort chronologically
    return reports[-1] if reports else None


def _report_date(report: Path) -> str | None:
    m = _REPORT_DATE_RE.search(report.name)
    return m.group(1) if m else None


def _parse_doc_targets(title: str) -> tuple[str | None, list[str]]:
    """Extract (project, [docs]) from a finding title's `[project/doc...]` tag.

    LLM-generated titles are not guaranteed to carry the tag — a title with no
    bracket returns (None, []) rather than raising (graceful degradation).
    """
    m = _TITLE_TAG_RE.search(title or "")
    if not m:
        return None, []
    project = m.group(1).strip()
    docs = _DOC_RE.findall(m.group(2))
    return (project or None), docs


def get_semantic_drift(root: Path | str) -> dict:
    """Parse the latest ddd-self-audit report into a live drift signal.

    Returns ``{report_date, findings: [{project, docs, title, detail}], drift_count}``.
    No report / no findings → an empty signal, never an error. Read-only: nothing is
    written, no count is persisted (computed fresh every call — R30#4).
    """
    root = Path(root)
    empty = {"report_date": None, "findings": [], "drift_count": 0}

    report = _latest_report(root)
    if report is None:
        return empty

    try:
        text = report.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return empty

    # Lazy import (core→jobs.handlers is a novel direction in this codebase; keep it
    # contained to call-time, mirroring the other core→jobs lazy hops). Reuse the
    # producer's parser so read/write shapes never drift apart.
    try:
        from jobs.handlers.ddd_self_audit import _count_parseable_findings
    except Exception:
        return {**empty, "report_date": _report_date(report)}

    findings: list[dict] = []
    # Split the report into per-project sections so each RADAR_TODOS block is attributed
    # to the project whose section precedes it. (The section header project name is a
    # fallback; the finding's own [project/doc] title tag is authoritative.)
    matches = list(_SECTION_RE.finditer(text))
    for i, sec in enumerate(matches):
        start = sec.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        # Section header project: "PhysicalAI (non-code) — 1 finding(s)" → "PhysicalAI".
        section_project = sec.group(1).split("(")[0].split("—")[0].split(" - ")[0].strip()

        for f in _count_parseable_findings(block):  # [] for failed/timed-out/0-finding
            if not isinstance(f, dict):
                continue
            title = f.get("title", "") or ""
            tag_project, docs = _parse_doc_targets(title)
            findings.append({
                "project": tag_project,  # None when the LLM title lacked a [proj/doc] tag
                "section_project": section_project or None,
                "docs": docs,
                "title": title,
                "detail": (f.get("description", "") or "")[:400],
            })

    return {
        "report_date": _report_date(report),
        "findings": findings,
        "drift_count": len(findings),
    }


def _affected_by_matches(affected_by: str, project: str, doc: str) -> bool:
    """True if an `affected_by` entry references THIS project's doc.

    Tolerates both forms found in the real golden set:
      - prefixed:  ``Projects/SwarmAI/TECH.md``
      - bare:      ``SwarmAI/TECH.md``
    Project-aware ON PURPOSE (a bare basename ``TECH.md`` is NOT a match — that is the
    cross-project false-positive `eval_service.get_affected_cases` would produce).
    """
    entry = (affected_by or "").strip()
    if not entry.endswith(doc):
        return False
    # Strip an optional leading "Projects/" and require the segment before the doc to be
    # exactly the finding's project.
    normalized = entry[len("Projects/"):] if entry.startswith("Projects/") else entry
    return normalized == f"{project}/{doc}"


def map_at_risk_cases(findings: list[dict], cases: list[dict]) -> list[dict]:
    """Map drift findings → the golden cases at risk (project+doc-aware affected_by match).

    A case is at-risk if any of its ``affected_by`` entries references a (project, doc)
    that a finding hits. Returns ``[{case_id, project, doc}]`` (deduped). Pure — no I/O.
    A finding with no project tag (project=None) matches nothing; a case with no
    ``affected_by`` never crashes.
    """
    at_risk: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for f in findings:
        project = f.get("project")
        if not project:
            continue  # untagged finding → cannot attribute → maps nothing
        for doc in f.get("docs", []):
            for case in cases:
                case_id = case.get("id")
                if not case_id:
                    continue
                for ab in (case.get("affected_by") or []):
                    if _affected_by_matches(ab, project, doc):
                        key = (case_id, project, doc)
                        if key not in seen:
                            seen.add(key)
                            at_risk.append({"case_id": case_id, "project": project, "doc": doc})
    return at_risk
