"""The ONE commit message builder for pipeline runs.

Used by ``artifact_cli.cmd_run_commit`` (main checkout) and
``pipeline_worktree.land_deliver`` (worktree landing), so the two commit paths can
never word a commit differently.

- ``build(run_id, requirement, deliver)`` — the full message
- ``cut_subject(text)`` — fold + cut to ``SUBJECT_MAX`` at a word boundary
- ``TRAILER`` — the project identity trailer (AGENTS.md): always the LAST line; no
  git hook or CI step adds it, so this builder is its only guarantor
"""
from __future__ import annotations

SUBJECT_MAX = 72
TRAILER = "Co-Authored-By: Swarm <swarm@swarmai.dev>"


def fold(value) -> str:
    """One line: every whitespace run folded to a single space ("" for a non-str)."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def cut_subject(text: str) -> str:
    """``text`` folded and, when longer than SUBJECT_MAX, cut at the last word boundary
    in reach (only a single token longer than the limit is hard-cut) with the
    punctuation the cut left dangling dropped. Text that fits is returned as written."""
    text = fold(text)
    if len(text) <= SUBJECT_MAX:
        return text
    head = text[:SUBJECT_MAX + 1]
    space = head.rfind(" ")
    text = head[:space] if space > 0 else text[:SUBJECT_MAX]
    return text.rstrip(" .,;:")


def _deliver_template() -> dict:
    try:
        import pipeline_validator as pv
    except ImportError:  # the builder must never stop a commit
        return {}
    t = pv.STAGE_TEMPLATES.get("deliver") if isinstance(pv.STAGE_TEMPLATES, dict) else None
    return t if isinstance(t, dict) else {}


def title_from_requirement(requirement: str) -> str:
    """A delivery title derived from the requirement (its first line, cut at a word
    boundary) for a writer that has no real title — never a mid-word slice."""
    raw = (requirement or "").strip()
    return cut_subject(raw.splitlines()[0]) if raw else ""


def build(run_id: str, requirement: str, deliver: "dict | None") -> str:
    """Subject: the DELIVER ``title`` as written (folded, cut at a word boundary), else
    the requirement's first line cut the same way. A title that is empty or the
    template placeholder is no title. Body: ``outcome.result_now``, the ``approach``
    lines as ``- `` bullets, the run-id note, then TRAILER last.
    """
    tmpl = _deliver_template()
    tmpl_outcome = tmpl.get("outcome") if isinstance(tmpl.get("outcome"), dict) else {}
    d = deliver if isinstance(deliver, dict) else {}
    outcome = d.get("outcome") if isinstance(d.get("outcome"), dict) else {}

    title = fold(d.get("title"))
    if title == fold(tmpl.get("title")):
        title = ""
    subject = cut_subject(title) or title_from_requirement(requirement) or "pipeline changes"

    result_now = fold(outcome.get("result_now"))
    if result_now == fold(tmpl_outcome.get("result_now")):
        result_now = ""
    tmpl_approach = {fold(a) for a in (tmpl_outcome.get("approach") or []) if isinstance(a, str)}
    raw_approach = outcome.get("approach") if isinstance(outcome.get("approach"), list) else []
    approach = [fold(a) for a in raw_approach if isinstance(a, str)]
    approach = [a for a in approach if a and a not in tmpl_approach]
    parts = [p for p in (result_now, "\n".join(f"- {a}" for a in approach)) if p]
    parts.append(f"(auto local-commit, run {run_id}; not pushed)")
    parts.append(TRAILER)
    return subject + "\n\n" + "\n\n".join(parts)
