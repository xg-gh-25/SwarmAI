"""Pipeline run documents — the four human-readable files every run hands back.

A pipeline run records its work as stage artifacts (JSON, for the validator). What makes
it worth more than a bare coding agent — the requirement it agreed, the design it
committed to, and the gates + experts that tried to break it — was only readable inside
one long REPORT.md at the very end. This module projects the artifacts into four files
in the run directory, each written when its source stage completes:

=================  ==========  ======================================================
file               written at  content
=================  ==========  ======================================================
REQUIREMENTS.md    evaluate    intent, requirements + acceptance, clarifications, root
                               cause (why chain, recurrence), Gate-0 verdict
DESIGN_SPEC.md     plan        THINK's alternatives + solution class, the design spec
                               (workflow, mermaid architecture / flow, states,
                               invariants, failure modes), change spec, exit contract
REVIEW.md          deliver     every gate (0 / 1 / 2, Red Team, meta-review) and every
                               expert with its findings and how each was settled
REPORT.md          completion  the full report (``artifact_cli run-report``; not here)
=================  ==========  ======================================================

The documents are PROJECTIONS: code renders them from the artifacts, the model writes
none of them, so they cannot drift from what the validator judged and cost no model
tokens. A doc whose source stage is not in the run's profile is not required (research
has no design spec); ``doc_status`` says so instead of producing an empty file.

Everything here is fail-safe: renderers never raise on odd artifact shapes (a field of
the wrong type renders as absent), and the writers return None instead of raising.

Public: ``DOCS``, ``DOC_FOR_STAGE``, ``doc_for_stage``, ``required_docs``,
``doc_status``, ``completion_check``, ``render_requirements``, ``render_design_spec``, ``render_review``,
``load_stage_artifacts``, ``write_stage_doc``, ``write_all``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from .pipeline_profiles import FINISHED_STATUSES as _DONE  # one "finished" set for every reader

logger = logging.getLogger(__name__)

#: (file name, source stage, one-line purpose). REPORT.md has no source stage: it is
#: written at completion by ``artifact_cli run-report``.
DOCS: tuple[tuple[str, Optional[str], str], ...] = (
    ("REQUIREMENTS.md", "evaluate", "需求 · 根因 · 澄清"),
    ("DESIGN_SPEC.md", "plan", "方案 · 架构/流程 · 不变量"),
    ("REVIEW.md", "deliver", "Gates · 专家审查 · 处置"),
    ("REPORT.md", None, "完整报告"),
)
DOC_FOR_STAGE = {stage: name for name, stage, _ in DOCS if stage}
_ART_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_TEXT = 600
_MAX_ITEMS = 40


# ── small helpers ─────────────────────────────────────────────────────────────

def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _l(v: Any) -> list:
    return v if isinstance(v, list) else []


def _t(v: Any, n: int = _MAX_TEXT) -> str:
    """Model text for one markdown line: newlines folded, capped."""
    if v is None:
        return ""
    s = str(v).replace("\r", "").replace("```", "\'\'\'").strip()   # never opens a fence
    s = re.sub(r"\s*\n\s*", " ⏎ ", s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _cell(v: Any, n: int = 300) -> str:
    return _t(v, n).replace("\\", "\\\\").replace("|", "\\|")


def _diagram(v: Any) -> Optional[str]:
    """A mermaid diagram body, unable to close its fence."""
    if not isinstance(v, str) or not v.strip():
        return None
    return v.replace("```", "'''").strip()


def _bullets(items, n: int = _MAX_ITEMS) -> list[str]:
    out = [f"- {_t(x)}" for x in _l(items)[:n] if _t(x)]
    if len(_l(items)) > n:
        out.append(f"- … {len(_l(items)) - n} more")
    return out


def _header(title: str, run_state: dict) -> list[str]:
    rs = _d(run_state)
    head = [f"# {title}", "",
            f"**Run** `{_t(rs.get('id'), 64)}` · **profile** {_t(rs.get('profile') or '?', 20)}"
            + (f" · **project** {_t(rs.get('project'), 60)}" if rs.get("project") else ""),
            ""]
    req = rs.get("intent") or rs.get("requirement")
    if req:
        head += [f"> {_t(req, 400)}", ""]
    head += ["_Generated from the run's stage artifacts by the pipeline (not hand-written)._", ""]
    return head


def _sev(f: dict) -> str:
    return _t(f.get("severity") or "?", 20).upper()


# ── profile → which documents are required ────────────────────────────────────

def _profile_stages(profile: Optional[str]) -> list[str]:
    try:
        from .pipeline_profiles import get_profile_stages
        return list(get_profile_stages(profile))
    except Exception:  # noqa: BLE001 — unknown profile → the full stage list
        return ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"]


def doc_for_stage(stage: str) -> Optional[str]:
    return DOC_FOR_STAGE.get(stage)


def required_docs(profile: Optional[str]) -> list[str]:
    """The documents a run of ``profile`` must hand back: each whose source stage is in
    the profile's stage list, plus REPORT.md always."""
    stages = set(_profile_stages(profile))
    return [name for name, stage, _ in DOCS if stage is None or stage in stages]


def doc_status(run_dir: Any, profile: Optional[str], run_state: Optional[dict] = None) -> list[dict]:
    """One entry per document: ``{name, purpose, path, exists, required, reason}``.
    ``reason`` explains a document that is not required (its stage is not in the
    profile, or the run recorded that stage as skipped) or required but missing."""
    req = set(required_docs(profile))
    recs = _records(run_state) if isinstance(run_state, dict) else {}
    arts: Optional[dict] = None   # loaded once, only when a finished stage lacks its file
    out: list[dict] = []
    for name, stage, purpose in DOCS:
        p = Path(str(run_dir)) / name if run_dir else None
        exists = bool(p and p.is_file())
        required = name in req
        if required and stage and str(_d(recs.get(stage)).get("status") or "") == "skipped":
            required = False
            reason = f"skipped: the {stage} stage was skipped"
        elif not required:
            reason = f"skipped: profile '{profile}' has no {stage} stage"
        elif not exists:
            done = str(_d(recs.get(stage)).get("status") or "") in _DONE if stage else False
            if stage is None:
                reason = "missing"
            elif not done:
                reason = f"missing: {stage} has not completed"
            else:
                # the same split completion_check makes: artifact readable or not
                if arts is None:
                    arts = load_stage_artifacts(run_dir, run_state) if run_dir and run_state else {}
                reason = (f"not generated: {stage} completed before run documents existed, or the write failed"
                          if stage in arts else f"not generated: the {stage} artifact could not be read")
        else:
            reason = None
        out.append({"name": name, "purpose": purpose, "path": str(p) if p else None,
                    "exists": exists, "required": required, "reason": reason})
    return out


# ── REQUIREMENTS.md ───────────────────────────────────────────────────────────

def render_requirements(run_state: dict, evaluate: dict) -> str:
    ev = _d(evaluate)
    und = _d(ev.get("understanding"))
    lines = _header("Requirements", run_state)
    rec = ev.get("recommendation")
    scores = _d(ev.get("scores"))
    meta = [f"**Verdict** {_t(rec, 30)}" if rec else "",
            f"**Scope** {_t(ev.get('scope'), 30)}" if ev.get("scope") else "",
            ("**Scores** " + ", ".join(f"{_t(k, 20)} {_t(v, 6)}" for k, v in scores.items()))
            if scores else ""]
    meta = [m for m in meta if m]
    if meta:
        lines += [" · ".join(meta), ""]

    reqs = [r for r in _l(ev.get("requirements")) if isinstance(r, dict)]
    lines += ["## Requirements", ""]
    if reqs:
        lines += ["| ID | Requirement | Source | Status |", "|---|---|---|---|"]
        for r in reqs[:_MAX_ITEMS]:
            src = _d(r.get("source"))
            lines.append(f"| {_cell(r.get('id'), 20)} | {_cell(r.get('statement'))} | "
                         f"{_cell(src.get('kind'), 30)} | {_cell(r.get('status'), 20)} |")
        lines.append("")
        for r in reqs[:_MAX_ITEMS]:
            acc = [a for a in _l(r.get("acceptance")) if isinstance(a, dict)]
            if acc:
                lines.append(f"**{_t(r.get('id'), 20)} acceptance**")
                for a in acc[:8]:
                    lines.append(f"- Given {_t(a.get('given'), 200)} · when {_t(a.get('when'), 200)}"
                                 f" · then {_t(a.get('then'), 300)}")
                lines.append("")
    acs = _l(ev.get("acceptance_criteria"))
    if acs:
        lines += ["### Acceptance criteria", ""] + _bullets(acs) + [""]
    if not reqs and not acs:
        lines += ["_No structured requirements recorded._", ""]

    clar = [c for c in _l(ev.get("clarifications")) if isinstance(c, dict)]
    if clar:
        lines += ["## Clarifications", "", "| Q | Question | Answer | Decided by |", "|---|---|---|---|"]
        for c in clar[:_MAX_ITEMS]:
            lines.append(f"| {_cell(c.get('qid'), 12)} | {_cell(c.get('question'))} | "
                         f"{_cell(c.get('answer'))} | {_cell(c.get('answered_by'), 20)} |")
        lines.append("")

    if und:
        lines += ["## Understanding (Gate 0)", ""]
        for key, label in (("claim", "Present state"), ("evidence", "Evidence"),
                           ("alternative_considered", "Alternative framing considered")):
            if und.get(key):
                lines.append(f"- **{label}:** {_t(und.get(key))}")
        if und.get("skeptic_verdict"):
            lines.append(f"- **Gate-0 skeptic:** {_t(und.get('skeptic_verdict'), 40)}")
        lines.append("")
        rc = _d(und.get("root_cause"))
        if rc:
            lines += ["## Root cause", ""]
            chain = [w for w in _l(rc.get("why_chain")) if isinstance(w, dict)]
            for w in chain[:12]:
                lines.append(f"{_t(w.get('id'), 6)}. {_t(w.get('because'))}  \n"
                             f"   _evidence:_ {_t(w.get('evidence'), 300)}")
            if chain:
                lines.append("")
            if rc.get("root_layer"):
                lines.append(f"- **Root layer:** {_t(rc.get('root_layer'), 30)}")
            if rc.get("missed_invariant"):
                lines.append(f"- **Missed invariant:** {_t(rc.get('missed_invariant'))}")
            recur = _d(rc.get("recurrence"))
            if recur:
                lines.append("- **Searched:** " + "; ".join(_t(s, 160) for s in _l(recur.get("searched"))[:8]))
                prior = _l(recur.get("prior_instances"))
                sib = [s for s in _l(recur.get("siblings")) if isinstance(s, dict)]
                lines.append(f"- **Prior instances:** {len(prior)}" + (":" if prior else ""))
                lines += [f"  - {_t(p, 300)}" for p in prior[:10]]
                lines.append(f"- **Siblings (same shape elsewhere):** {len(sib)}" + (":" if sib else ""))
                lines += [f"  - {_t(s.get('id'), 6)} {_t(s.get('where'), 160)} — {_t(s.get('shape'), 200)}"
                          for s in sib[:10]]
            lines.append("")
    pm = _l(ev.get("pre_mortem"))
    if pm:
        lines += ["## Pre-mortem", ""] + _bullets(pm, 10) + [""]
    return "\n".join(lines).rstrip() + "\n"


# ── DESIGN_SPEC.md ────────────────────────────────────────────────────────────

def render_design_spec(run_state: dict, think: dict, plan: dict) -> str:
    th, pl = _d(think), _d(plan)
    lines = _header("Design Spec", run_state)

    sc = _d(th.get("solution_class"))
    alts = [a for a in _l(th.get("alternatives")) if isinstance(a, dict)]
    if sc or alts or th.get("recommendation"):
        lines += ["## Approach (THINK)", ""]
        if sc:
            verdict = _t(sc.get("verdict"), 20)
            lines.append(f"**Solution class: {verdict.upper() or '?'}**"
                         + (f" · addresses {_t(sc.get('addresses'), 8)}" if sc.get("addresses") else "")
                         + (f" · structural change: {_t(sc.get('structural_change'), 20)}"
                            if sc.get("structural_change") else ""))
            for key, label in (("why_root", "Why this is the root"),
                               ("structural_reason", "Structural change"),
                               ("why_not_deepest", "Why not the deepest level"),
                               ("p0_reason", "P0 reason (patch)"), ("follow_up", "Root-fix follow-up")):
                if sc.get(key):
                    lines.append(f"- **{label}:** {_t(sc.get(key))}")
            cov = [c for c in _l(sc.get("covers")) if isinstance(c, dict)]
            if cov:
                lines.append("- **Sibling coverage:** " + "; ".join(
                    f"{_t(c.get('id'), 6)} {_t(c.get('how') or ('excluded: ' + _t(c.get('excluded'), 200)), 200)}"
                    for c in cov[:10]))
            lines.append("")
        if alts:
            lines += ["| | Approach | Effort | Risk | Tradeoff |", "|---|---|---|---|---|"]
            for a in alts[:8]:
                mark = "🎯 " if a.get("targets_root") is True else ""
                lines.append(f"| {mark}{_cell(a.get('constraint'), 20)} | {_cell(a.get('what'))} | "
                             f"{_cell(a.get('effort'), 30)} | {_cell(a.get('risk'), 200)} | "
                             f"{_cell(a.get('tradeoff'), 200)} |")
            lines.append("")
        if th.get("recommendation"):
            lines += [f"**Recommendation:** {_t(th.get('recommendation'))}", ""]

    d = _d(pl.get("design"))
    if d:
        lines += ["## Design", ""]
        wf = _l(d.get("user_workflow"))
        if wf:
            lines += ["### User workflow", ""] + [f"{_t(s)}" if re.match(r"^\s*\d+[.)]", str(s))
                                                   else f"- {_t(s)}" for s in wf[:20]] + [""]
        for key, title in (("architecture", "Architecture"), ("flow", "Flow")):
            body = _diagram(d.get(key))
            if body:
                lines += [f"### {title}", "", "```mermaid", body, "```", ""]
        states = [s for s in _l(d.get("states")) if isinstance(s, dict)]
        if states:
            lines += ["### State lifecycle", "", "| State | Created by | Removed by | Crosses |",
                      "|---|---|---|---|"]
            for s in states[:20]:
                cr = "; ".join(f"{_t(c.get('with'), 80)} → {_t(c.get('outcome'), 160)}"
                               for c in _l(s.get("crosses"))[:8] if isinstance(c, dict))
                lines.append(f"| {_cell(s.get('id'), 8)} {_cell(s.get('name'), 80)} | "
                             f"{_cell(s.get('created_by'), 120)} | {_cell(s.get('removed_by'), 120)} | "
                             f"{_cell(cr, 500)} |")
            lines.append("")
        elif d.get("no_state_reason"):
            lines += [f"**No state:** {_t(d.get('no_state_reason'))}", ""]
        inv = [i for i in _l(d.get("invariants")) if isinstance(i, dict)]
        if inv:
            root = str(d.get("root_invariant") or "")
            lines += ["### Invariants", "", "| ID | Invariant | Enforced at |", "|---|---|---|"]
            for i in inv[:30]:
                tag = " (root)" if root and str(i.get("id")) == root else ""
                lines.append(f"| {_cell(i.get('id'), 8)}{tag} | {_cell(i.get('statement'))} | "
                             f"{_cell(i.get('enforced_at'), 160)} |")
            lines.append("")
        fm = [f for f in _l(d.get("failure_modes")) if isinstance(f, dict)]
        if fm:
            lines += ["### Failure modes", "", "| ID | Scenario | Expected |", "|---|---|---|"]
            for f in fm[:30]:
                lines.append(f"| {_cell(f.get('id'), 8)} | {_cell(f.get('scenario'))} | {_cell(f.get('expected'))} |")
            lines.append("")

    if pl.get("approach"):
        lines += ["## Plan", "", _t(pl.get("approach"), 2000), ""]
    dd = _d(pl.get("design_doc"))
    cs = dd.get("change_spec")
    if isinstance(cs, list) and cs:
        lines += ["### Change spec", ""]
        for c in cs[:30]:
            if isinstance(c, dict):
                lines.append(f"- **{_t(c.get('file') or c.get('id'), 120)}** — "
                             f"{_t(c.get('target') or c.get('change') or c.get('what'), 400)}")
            else:
                lines.append(f"- {_t(c, 400)}")
        lines.append("")
    acs = _l(pl.get("acceptance_criteria"))
    if acs:
        lines += ["### Acceptance criteria", ""] + _bullets(acs) + [""]
    ex = [e for e in _l(pl.get("exit_criteria")) if isinstance(e, dict)]
    if ex:
        lines += ["### Exit contract", "", "| ID | Kind | Ref | Expect |", "|---|---|---|---|"]
        for e in ex[:30]:
            lines.append(f"| {_cell(e.get('id'), 8)} | {_cell(e.get('kind'), 12)} | "
                         f"{_cell(e.get('ref'), 200)} | {_cell(e.get('expect'), 20)} |")
        lines.append("")
    if len(lines) <= len(_header("Design Spec", run_state)):
        lines += ["_No design recorded._", ""]
    return "\n".join(lines).rstrip() + "\n"


# ── REVIEW.md ─────────────────────────────────────────────────────────────────

def _finding_text(f: dict) -> str:
    return _t(f.get("finding") or f.get("summary") or f.get("description"), 400)


def _findings_table(findings: list, *, show_expert: bool = True) -> list[str]:
    rows = [f for f in findings if isinstance(f, dict)]
    if not rows:
        return []
    head = ("| Expert | Severity | Finding | Status |" if show_expert else "| Severity | Finding | Status |")
    out = [head, "|---|---|---|---|" if show_expert else "|---|---|---|"]
    for f in rows[:60]:
        status = _finding_status(f)
        cells = [_cell(f.get("specialist") or f.get("source") or "?", 40)] if show_expert else []
        cells += [_cell(_sev(f), 12), _cell(_finding_text(f), 400), _cell(status, 200)]
        out.append("| " + " | ".join(cells) + " |")
    if len(rows) > 60:
        out.append(f"\n… {len(rows) - 60} more")
    return out


def _finding_status(f: dict) -> str:
    text = str(f.get("finding") or "")
    if re.match(r"^\s*not\s+a\s+bug\b", text, re.IGNORECASE) or f.get("disposition") == "refuted":
        return "refuted (not a bug)"
    if not f.get("resolved"):
        return "OPEN"
    extra = []
    if f.get("invariant"):
        extra.append(f"inv {_t(f.get('invariant'), 8)}")
    if f.get("class_fix"):
        extra.append("class fix: " + _t(f.get("class_fix"), 140))
    return "fixed" + (" · " + " · ".join(extra) if extra else "")


def _experts_from(findings: list, required: list, extra: list) -> list[str]:
    names = {str(x).strip().lower() for x in _l(extra) + _l(required) if isinstance(x, str) and x.strip()}
    for f in findings:
        if isinstance(f, dict) and isinstance(f.get("specialist"), str) and f["specialist"].strip():
            names.add(f["specialist"].strip().lower())
    return sorted(names)


def render_review(run_state: dict, evaluate: dict, records: dict, review: dict,
                  deliver: dict, experts: Optional[list] = None) -> str:
    rs, ev, rv, dl = _d(run_state), _d(evaluate), _d(review), _d(deliver)
    recs = _d(records)
    und = _d(ev.get("understanding"))
    ar = _d(dl.get("adversarial_review"))
    gate2 = [f for f in _l(ar.get("findings")) if isinstance(f, dict)]
    lines = _header("Review — gates and experts", rs)

    # Gate summary table
    build = _d(recs.get("build")) or _d(recs.get("goal_cycle"))
    meta = _d(dl.get("meta_review"))
    deliver_rec = _d(recs.get("deliver"))
    open_n = sum(1 for f in gate2 if not f.get("resolved"))
    rows = [
        ("Gate 0 — understanding skeptic", und.get("skeptic_verdict") or "—",
         "framing: is the claim observed, is the root the root"),
        ("Gate 1 — plan skeptic", build.get("gate1_verdict") or "—", "plan before BUILD"),
        ("REVIEW — spec + conformance", _review_verdict(rv), f"{_t(rv.get('findings_count'), 6) or 0} finding(s)"),
        ("Gate 2 — adversarial experts", (f"{ar.get('profile_tier') or '?'} · "
                                           f"{len(gate2)} finding(s), {open_n} open") if ar else "—",
         "Red Team: " + ("yes" if ar.get("red_team_dispatched") else "no")),
        ("Meta-review", meta.get("verdict") or "—", f"{len(_l(meta.get('risks')))} risk(s)"),
        ("Push-ready", ("yes" if deliver_rec.get("push_ready") is True else
                        "no" if deliver_rec.get("push_ready") is False else "—"),
         _t(deliver_rec.get("push_ready_reason"), 200)),
    ]
    lines += ["## Gates", "", "| Gate | Verdict | Notes |", "|---|---|---|"]
    lines += [f"| {_cell(a, 60)} | {_cell(b, 80)} | {_cell(c, 240)} |" for a, b, c in rows]
    lines.append("")

    # Experts
    names = _experts_from(gate2, rs.get("required_experts"), experts or [])
    if names:
        lines += ["## Experts", "", "| Expert | Findings | Blocking | Fixed | Refuted | Open |",
                  "|---|---|---|---|---|---|"]
        for n in names:
            mine = [f for f in gate2 if str(f.get("specialist") or "").strip().lower() == n]
            blocking = sum(1 for f in mine if _sev(f) in ("HIGH", "CRITICAL", "BLOCK", "BLOCKER")
                           or (_sev(f).startswith("MED") and _conf(f) >= 7))
            refuted = sum(1 for f in mine if _finding_status(f).startswith("refuted"))
            open_f = sum(1 for f in mine if not f.get("resolved")
                         and not _finding_status(f).startswith("refuted"))
            fixed = len(mine) - refuted - open_f
            lines.append(f"| {_cell(n, 40)} | {len(mine)} | {blocking} | {fixed} | {refuted} | {open_f} |")
        lines.append("")
        if ar.get("evidence"):
            lines += [f"_Dispatch:_ {_t(ar.get('evidence'), 400)}", ""]

    if gate2:
        lines += ["## Gate 2 findings", ""] + _findings_table(gate2) + [""]
        cls = [f for f in gate2 if f.get("resolved") and (f.get("root_cause") or f.get("class_fix"))]
        if cls:
            lines += ["### Fixed by class", ""]
            for f in cls[:30]:
                lines.append(f"- **{_t(f.get('specialist'), 30)} · {_sev(f)}** — {_finding_text(f)}")
                for key, label in (("invariant", "invariant"), ("root_cause", "root cause"),
                                   ("class_fix", "class fix"), ("break_test", "break test")):
                    if f.get(key):
                        lines.append(f"  - {label}: {_t(f.get(key), 300)}")
            lines.append("")

    rv_find = [f for f in _l(rv.get("findings")) if isinstance(f, dict)]
    if rv_find:
        lines += ["## REVIEW findings", ""] + _findings_table(rv_find) + [""]
    risks = [r for r in _l(meta.get("risks")) if isinstance(r, dict)]
    if risks:
        lines += ["## Meta-review risks", "", "| Severity | Risk | Status |", "|---|---|---|"]
        for r in risks[:30]:
            status = "resolved" if r.get("resolved") else "OPEN"
            if r.get("mitigation"):
                status += " · " + _t(r.get("mitigation"), 160)
            lines.append(f"| {_cell(_sev(r), 12)} | {_cell(r.get('description'))} | {_cell(status, 220)} |")
        lines.append("")
    flags = _l(dl.get("attention_flags")) + _l(deliver_rec.get("attention_flags"))
    if flags:
        lines += ["## Open items", ""] + _bullets(flags, 20) + [""]
    return "\n".join(lines).rstrip() + "\n"


def _conf(f: dict) -> float:
    m = re.match(r"\s*(\d+(?:\.\d+)?)", str(f.get("confidence") if f.get("confidence") is not None else ""))
    return float(m.group(1)) if m else 10.0


def _review_verdict(rv: dict) -> str:
    if not rv:
        return "—"
    lit = _d(rv.get("litmus_gate")).get("verdict")
    approved = rv.get("approved")
    parts = []
    if approved is not None:
        parts.append("approved" if approved else "NOT approved")
    if lit:
        parts.append(f"litmus {lit}")
    return " · ".join(parts) or "—"


# ── loading + writing ─────────────────────────────────────────────────────────

def _records(run_state: dict) -> dict:
    out: dict = {}
    for rec in _l(_d(run_state).get("stages")):
        if isinstance(rec, dict) and isinstance(rec.get("stage"), str):
            out[rec["stage"]] = rec   # the last record per stage (the one judged)
    return out


def load_stage_artifacts(run_dir: Any, run_state: dict) -> dict:
    """{stage: artifact data} for the run's own stage artifacts, found by artifact_id in
    the run directory (``<type>-<date>-<artifact_id>.json``, ``{"data": …}`` unwrapped)."""
    out: dict = {}
    base = Path(str(run_dir))
    for stage, rec in _records(run_state).items():
        art_id = rec.get("artifact_id")
        if not isinstance(art_id, str) or not _ART_ID_RE.fullmatch(art_id):
            continue
        try:
            for hit in base.glob(f"*-{art_id}.json"):
                data = json.loads(hit.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    out[stage] = data["data"] if isinstance(data.get("data"), dict) else data
                break
        except (OSError, ValueError):
            logger.debug("run_docs: unreadable artifact %r", art_id, exc_info=True)
    return out


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _render(name: str, run_state: dict, arts: dict, experts: Optional[list]) -> Optional[str]:
    if name == "REQUIREMENTS.md":
        return render_requirements(run_state, arts["evaluate"]) if "evaluate" in arts else None
    if name == "DESIGN_SPEC.md":
        return (render_design_spec(run_state, arts.get("think", {}), arts["plan"])
                if "plan" in arts else None)
    if name == "REVIEW.md":
        if "deliver" not in arts:
            return None
        return render_review(run_state, arts.get("evaluate", {}), _records(run_state),
                             arts.get("review", {}), arts["deliver"], experts)
    return None


def write_stage_doc(run_dir: Any, run_state: dict, stage: str,
                    experts: Optional[list] = None) -> Optional[str]:
    """Write the document whose source is ``stage`` (None when the stage has none, its
    artifact is missing, or the write failed). Never raises."""
    name = doc_for_stage(stage)
    if not name:
        return None
    try:
        arts = load_stage_artifacts(run_dir, run_state)
        text = _render(name, run_state, arts, experts)
        if text is None:
            return None
        path = Path(str(run_dir)) / name
        _atomic_write(path, text)
        return str(path)
    except Exception as e:  # noqa: BLE001 — a document must never fail a stage
        logger.warning("run_docs: %s for %r not written: %s", name, _d(run_state).get("id"), e)
        return None


def completion_check(run_dir: Any, run_state: dict,
                     experts: Optional[list] = None) -> "tuple[list[str], list[str], list[dict]]":
    """(errors, warnings, status) at run completion: rewrite every stage document from
    the final artifacts, then hold each required one to exist. A required document is
    an ERROR only when its stage completed and its artifact loads (the write itself
    failed); an unloadable artifact is a warning, never a self-lock on an infra error.
    REPORT.md has its own gate (artifact_cli), so it is reported, not judged, here."""
    errors: list[str] = []
    warnings: list[str] = []
    write_all(run_dir, run_state, experts)
    status = doc_status(run_dir, _d(run_state).get("profile"), run_state)
    recs = _records(run_state)
    arts = load_stage_artifacts(run_dir, run_state)
    for d in status:
        stage = next((st for n, st, _ in DOCS if n == d["name"]), None)
        if not d["required"] or d["exists"] or stage is None:
            continue
        rec = _d(recs.get(stage))
        if str(rec.get("status") or "") not in _DONE:
            warnings.append(f"{d['name']}: the {stage} stage is not completed, so it has no document")
        elif stage in arts:
            errors.append(f"{d['name']} could not be written although the {stage} artifact "
                          "loads — re-run this command; if it persists, the run document "
                          "writer is failing (core/run_docs.py)")
        else:
            warnings.append(f"{d['name']}: the {stage} artifact could not be read, so its "
                            "document is missing")
    return errors, warnings, status


def write_all(run_dir: Any, run_state: dict, experts: Optional[list] = None) -> list[str]:
    """(Re)write every stage document the run's artifacts allow; the paths written."""
    written: list[str] = []
    for name, stage, _ in DOCS:
        if stage:
            p = write_stage_doc(run_dir, run_state, stage, experts)
            if p:
                written.append(p)
    return written
