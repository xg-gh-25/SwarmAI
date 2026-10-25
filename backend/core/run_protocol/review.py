"""Event-driven Pipeline v1 REVIEW orchestration.

Public symbols:
- ``reviewers`` — lightweight P3a roster from deterministic scope facts.
- ``ReviewOrchestrator`` — handouts, projections, R2 routing and document rendering.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.run_protocol import events as ev
from core.run_protocol.findings import cluster_findings, fold as fold_findings, push_effect, r2_experts
from core.run_protocol.store import AuditStore

OUTPUT_CONTRACT = """OUTPUT CONTRACT (exact; prose may precede rows):
Gate-0 only: first non-empty line is VERDICT: SUPPORTED | SUPPORTED-WITH-CHANGES | UNSUPPORTED | WRONG-FRAME | ALREADY-SATISFIED
R2 status: {\"finding_id\":\"CF-1234abcd\",\"status\":\"RESOLVED|OPEN|REFUTED|ADEQUATE|TEST_INADEQUATE\",\"kind_confirm\":null,\"note\":\"...\"}
New finding: {\"severity\":\"HIGH|MEDIUM|LOW\",\"kind\":\"behavior|contract|absence|docs|design|security_policy\",\"file\":\"path\",\"line\":1,\"claim\":\"...\",\"fix\":\"...\"}
When there are no new findings, include a separate line: NO FINDINGS
Do not emit a VERDICT line for REVIEW experts."""

_SECURITY_PATH = re.compile(
    r"(?:^|/)(?:auth|credential|permission|security_hooks|hook_builder|"
    r"permission_manager|context)(?:[._/-]|$)|(?:^|/)[^/]*_gate\.py$|cors|listen|port",
    re.IGNORECASE,
)
_SECURITY_CODE = re.compile(
    r"\b(?:subprocess\.|shell\s*=\s*True|requests\.|httpx\.|urllib\.|pickle\.|"
    r"yaml\.load\s*\(|eval\s*\(|exec\s*\(|host\s*=|port\s*=|listen_port\s*=|daemon_port\s*=)",
    re.IGNORECASE,
)


class ReviewError(ValueError):
    """REVIEW input, event stream, fixing spec, or handout is inconsistent."""


def security_risk(changed: Sequence[Mapping[str, Any]]) -> str | None:
    for row in changed:
        if row.get("is_test") is True:
            continue
        path = str(row.get("path") or row.get("rel") or "")
        if _SECURITY_PATH.search(path):
            return f"security-sensitive path: {path}"
        added = row.get("added_lines")
        if isinstance(added, list):
            hit = next((_SECURITY_CODE.search(line) for line in added
                        if isinstance(line, str) and _SECURITY_CODE.search(line)), None)
            if hit:
                return f"adds {hit.group(0)!r} in {path}"
    return None


def lint_prompt(prompt: str, *, expert_id: str, round: int) -> list[str]:
    """Return drift errors for a generated Gate-0/REVIEW prompt."""
    errors: list[str] = []
    if not prompt.startswith(f"EXPERT-ID: {expert_id}\n"):
        errors.append("EXPERT-ID must be the first line")
    for phrase in ("OUTPUT CONTRACT (exact", "New finding:", "NO FINDINGS",
                   "Do not emit a VERDICT line for REVIEW experts"):
        if phrase not in prompt:
            errors.append(f"missing output contract phrase: {phrase}")
    if round == 2 and "R2 status:" not in prompt:
        errors.append("R2 prompt lacks status-row contract")
    if expert_id == "gate0-skeptic" and "Gate-0 only: first non-empty line is VERDICT:" not in prompt:
        errors.append("Gate-0 prompt lacks verdict contract")
    return errors


_SPAWN_PROMPT = (Path(__file__).resolve().parents[2] / "skills" / "s_autonomous-pipeline"
                 / "scripts" / "spawn_prompt.py")
_SPAWN_PROMPT_MOD: list[Any] = []


def build_prompt(expert_id: str, instructions: str, review_input: str) -> str:
    """The exact text a handout gives an expert (one definition: handout and reuse)."""
    return (f"EXPERT-ID: {expert_id}\n\n{instructions.strip()}\n\n{review_input.strip()}"
            f"\n\n{OUTPUT_CONTRACT}\n")


def _spawn_prompt():
    """spawn_prompt.py by path (a skill script, not a package): the one REVIEW_PATTERNS
    parser and selector, shared with the ``--patterns-for`` CLI."""
    if not _SPAWN_PROMPT_MOD:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_v1_spawn_prompt", _SPAWN_PROMPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _SPAWN_PROMPT_MOD.append(mod)
    return _SPAWN_PROMPT_MOD[0]


_EXT_GLOB = re.compile(r"\*\.[a-z0-9]+")


def _specific_hit(row: Mapping[str, Any], paths: Sequence[str], diff_text: str) -> bool:
    """The row matched on evidence about THIS change: a keyword in a path or the diff,
    or a glob narrower than a bare extension (``*.py`` fires on every Python file, so
    on its own it says nothing about the change)."""
    if row.get("always"):
        return False
    import fnmatch
    norm = [str(p).replace("\\", "/").lower() for p in paths]
    for g in row.get("globs") or ():
        if _EXT_GLOB.fullmatch(g):
            continue
        if any(fnmatch.fnmatchcase(p, g) or fnmatch.fnmatchcase(p.rsplit("/", 1)[-1], g)
               for p in norm):
            return True
    hay = "\n".join(norm) + "\n" + diff_text.lower()
    return any(k in hay for k in row.get("keywords") or ())


def review_patterns(paths: Sequence[str], diff_text: str = "") -> dict[str, Any]:
    """The REVIEW_PATTERNS rows this change triggers (v13 E1: the RP lens is part of
    correctness's checklist, so the ENGINE selects the rows; the model never does).

    Returns ``{"compact": str, "triggered": [ids], "error": str | None}``. compact is
    the --patterns-for listing (every applying row, one line each) for the
    correctness handout; triggered are the rows matched on specific evidence
    (_specific_hit: a keyword, or a glob narrower than ``*.<ext>``), which make even
    a small change get a correctness review.
    On any failure: no listing, error set (the caller still dispatches correctness).
    """
    try:
        sp = _spawn_prompt()
        text = sp.REVIEW_PATTERNS_PATH.read_text(encoding="utf-8")
        _, _, hits = sp._select(text, list(paths), diff_text)
        compact = sp.render_patterns_compact(text, list(paths), diff_text)
        return {"compact": compact,
                "triggered": [r["id"] for r in hits if _specific_hit(r, paths, diff_text)],
                "error": None}
    except Exception as exc:  # noqa: BLE001 — a broken table must not stop REVIEW
        return {"compact": "", "triggered": [], "error": f"{type(exc).__name__}: {exc}"}


def _table_only(compact: str) -> str:
    """The listing's table rows and warnings, without its closing instruction (that
    one asks for per-row pass / N/A / fail; a v1 reviewer answers in findings)."""
    keep = [ln for ln in compact.splitlines() if ln.startswith("|") or ln.startswith("WARNING")]
    return "\n".join(keep)


def patterns_block(patterns: Mapping[str, Any]) -> str:
    """The REVIEW_PATTERNS section of the correctness handout: the selected rows and
    how to read one in full. A failed selection says so and points at the file."""
    if patterns.get("compact"):
        return ("REVIEW PATTERNS (RP) for this change, selected by the engine. Check each "
                "listed row against the diff; report a violated row as a finding naming its "
                "RP id. Before judging a row whose trigger is present in the change, print "
                "its full row (what to verify + example bug): python "
                "backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py --patterns-rows "
                "RP<n> [RP<m> ...]\n\n" + _table_only(str(patterns["compact"])))
    if patterns.get("error"):
        return ("REVIEW PATTERNS: the engine could not select rows "
                f"({patterns['error']}); apply backend/skills/s_autonomous-pipeline/"
                "REVIEW_PATTERNS.md rows whose trigger is present in the diff.")
    return "REVIEW PATTERNS: no source file changed; no rows apply."


# Experts removed by the §3.5.1 merge -> the roster expert that took their job.
_RETIRED = {"red-team": "security", "spec-compliance": "correctness",
            "code-quality": "correctness", "ux-test": "correctness"}


def reviewers(*, profile: str, changed: Sequence[Mapping[str, Any]],
              required: Sequence[str] = (), small_change: bool = False,
              patterns: Sequence[str] = ()) -> list[dict[str, str]]:
    """P3a E1 roster: default correctness; security only on clear risk. A >=50-line
    risky change gets security's "High-risk change" section (§3.5.1: red-team merged
    into security), not a second reviewer.

    *patterns*: REVIEW_PATTERNS rows the change triggers by path or diff
    (review_patterns). Any of them dispatches correctness, also for a small change:
    the RP lens has no other reviewer (code-quality was deleted, §3.5.1).

    *required*: an id from the roster is kept; a retired id maps to the expert that
    took its job (``_RETIRED``); anything else falls back to correctness."""
    picked: dict[str, str] = {}
    high_risk = False
    if patterns:
        shown = ", ".join(patterns[:6]) + (f" +{len(patterns) - 6}" if len(patterns) > 6 else "")
        picked["correctness"] = f"review patterns triggered: {shown}"
    if small_change:
        return [{"id": expert, "reason": reason} for expert, reason in picked.items()]
    source = [row for row in changed if row.get("is_test") is not True
              and not str(row.get("path") or "").endswith((".md", ".txt", ".rst"))]
    total = sum(int(row.get("insertions") or 0) + int(row.get("deletions") or 0)
                for row in source)
    risk = security_risk(source)
    if source and total >= 50:
        picked["correctness"] = "changeset >= 50 source lines"
        if risk:
            picked["security"] = f"high-risk change: {risk}"
            high_risk = True
    elif risk:
        picked["security"] = risk
    allowed = {"correctness", "security"}
    for expert in required:
        if isinstance(expert, str) and expert:
            target = expert if expert in allowed else _RETIRED.get(expert, "correctness")
            picked.setdefault(target, f"required expert {expert}")
    return [{"id": expert, "reason": reason,
             **({"high_risk": True} if expert == "security" and high_risk else {})}
            for expert, reason in picked.items()]


def high_risk_notice(reason: str) -> str:
    """The line a high-risk security handout starts with: it is what makes security do
    its "High-risk change" section (§3.5.1)."""
    return (f"HIGH-RISK CHANGE ({reason}): do the \"High-risk change\" section of your "
            "instructions in full, after the categories.\n\n")





class ReviewOrchestrator:
    """Deterministic REVIEW coordinator over one run's ``AuditStore``."""

    def __init__(self, store: AuditStore, run_dir: str | Path):
        self.store = store
        self.run_dir = Path(run_dir)
        self.missing_blobs: list[str] = []

    @staticmethod
    def _atomic(path: Path, data: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "x", encoding="utf-8") as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try: temp.unlink()
            except FileNotFoundError: pass

    def handout(self, *, expert_id: str, stage: str, round: int, attempt: int,
                at: str, expert_instructions: str, review_input: str,
                finding_ids: Sequence[str] = (),
                code_trees: Mapping[str, Any] | None = None,
                incremental_from: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if round not in (1, 2) or not expert_id:
            raise ReviewError("invalid review handout identity")
        prompt = build_prompt(expert_id, expert_instructions, review_input)
        prompt_errors = lint_prompt(prompt, expert_id=expert_id, round=round)
        if prompt_errors:
            raise ReviewError(f"review prompt contract drift: {prompt_errors}")
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        # the full prompt goes into the audit area as a blob (F-4): the evidence prompts
        # file in the run dir is agent-writable, the blob is not
        payload: dict[str, Any] = {"expert_id": expert_id, "prompt_sha": digest,
                                   "prompt": prompt}
        if round == 2:
            if not finding_ids:
                raise ReviewError("R2 needs a finding roster")
            payload["finding_ids"] = list(finding_ids)
        if code_trees:          # what the expert sees: RV-4 / LG-1 bind to it (S6-2)
            payload["code_trees"] = dict(code_trees)
        if incremental_from:
            payload["incremental_from"] = dict(incremental_from)
        event, _ = self.store.append_new(
            writer=self.store.authority.writer("cli"), kind="handout", at=at,
            stage=stage, round=round, attempt=attempt, payload=payload,
        )
        name = f"{expert_id}-r{round}-a{attempt}.md"
        path = self.run_dir / "evidence" / "prompts" / name
        self._atomic(path, prompt)
        return {"event": event, "prompt": prompt, "path": str(path), "sha": digest}

    def findings(self, events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        return fold_findings(self.store.read_events() if events is None else events,
                             self.store.authority)

    def clusters(self) -> list[dict[str, Any]]:
        return cluster_findings(self.findings())

    def r2_roster(self, addressed_ids: set[str] | None = None) -> dict[str, list[str]]:
        rows = self.findings()
        if addressed_ids is None:
            addressed_ids = {event["payload"]["canonical_id"]
                              for event in self.store.read_events()
                              if event.get("kind") == "address"}
        experts = r2_experts(rows, addressed_ids)
        return {expert: [row["canonical_id"] for row in rows
                         if row["expert"] == expert and row["canonical_id"] in addressed_ids]
                for expert in experts}

    def record_fix_handoff(self, value: Mapping[str, Any], *, at: str,
                           attempt: int) -> dict[str, Any]:
        """Record one fixing pass and return immutable inputs for minimal R2."""
        if not isinstance(value, Mapping) or set(value) != {"pass", "items"}:
            raise ReviewError("review handoff must contain exactly pass and items")
        pass_number, items = value["pass"], value["items"]
        if not isinstance(pass_number, int) or isinstance(pass_number, bool) or pass_number < 1 \
                or not isinstance(items, list):
            raise ReviewError("review handoff pass/items are malformed")
        findings = {row["canonical_id"]: row for row in self.findings()}
        # ±10-line clusters are fixed together (F-9, expert simplification E4): a pass
        # that touches one open finding of a cluster addresses all of its open ones
        named = {item.get("finding_id") for item in items if isinstance(item, Mapping)}
        open_states = {"open", "invalid_test"}   # a proven neighbour stays as it is
        for cluster in cluster_findings(list(findings.values())):
            members = [cid for cid in cluster["findings"]
                       if findings[cid].get("status") in open_states]
            if named & set(members) and set(members) - named:
                raise ReviewError(
                    f"cluster {cluster['cluster_id']} ({cluster['sites'][0]['file']}) is fixed "
                    f"as one: also address {sorted(set(members) - named)}")
        addressed: list[str] = []
        hunks: dict[str, str] = {}
        proofs: dict[str, str] = {}
        reclassifications: dict[str, Mapping[str, Any]] = {}
        causes: dict[str, str] = {}
        solutions: dict[str, str] = {}
        plans: dict[str, str] = {}
        for index, item in enumerate(items, 1):
            if not isinstance(item, Mapping):
                raise ReviewError(f"handoff item {index} must be an object")
            allowed = {"finding_id", "hunk", "proof", "reclassify", "cause", "solution",
                       "evidence_plan"}
            if set(item) - allowed:
                raise ReviewError(f"handoff item {index} has unknown fields")
            finding_id, hunk = item.get("finding_id"), item.get("hunk")
            row = findings.get(finding_id)
            occurrence = next((event for event in self.store.read_events()
                               if event.get("event_id") == (row or {}).get("occurrence_event_id")), None)
            if row is None or occurrence is None or occurrence.get("round") != 1 \
                    or row.get("status") not in {"open", "invalid_test", "closed_proven"}:
                raise ReviewError(f"handoff item {index} is not an addressable R1 finding")
            if finding_id in addressed or not isinstance(hunk, str) or not hunk.strip():
                raise ReviewError(f"handoff item {index} needs a unique finding and non-empty hunk")
            proposal = item.get("reclassify")
            if proposal is not None:
                if not isinstance(proposal, Mapping) or set(proposal) != {"from", "to", "reason"}:
                    raise ReviewError(f"handoff item {index} reclassification is malformed")
                reclassifications[finding_id] = dict(proposal)
            proof = item.get("proof")
            if proof is not None:
                if not isinstance(proof, Mapping):
                    raise ReviewError(f"handoff item {index} proof is malformed")
                evidence_type = proof.get("evidence_type")
                if evidence_type not in {None, "diff_assertion", "refuted", "reported_test"}:
                    raise ReviewError(
                        f"{finding_id}: a {evidence_type!r} proof cannot be recorded; use "
                        "reported_test with a command of your validation report")
                proofs[finding_id] = json.dumps(dict(proof), ensure_ascii=False, sort_keys=True)
            addressed.append(finding_id)
            hunks[finding_id] = hunk
            if isinstance(item.get("cause"), str) and item["cause"].strip():
                causes[finding_id] = item["cause"].strip()[:400]
            # design §B6: the fix in words, and how it will be proven (planned before
            # the proof exists; the proof event is the evidence that it was)
            if item.get("solution") is not None:
                if not isinstance(item["solution"], str) or not item["solution"].strip():
                    raise ReviewError(f"handoff item {index} solution must be text")
                solutions[finding_id] = item["solution"].strip()[:800]
            plan = item.get("evidence_plan")
            if plan is not None:
                if not isinstance(plan, Mapping) or set(plan) - {"type", "ref"} \
                        or plan.get("type") not in EVIDENCE_TYPES \
                        or not isinstance(plan.get("ref", ""), str):
                    raise ReviewError(f"handoff item {index} evidence_plan must be "
                                      f"{{type: {sorted(EVIDENCE_TYPES)}, ref?: text}}")
                plans[finding_id] = json.dumps(dict(plan), ensure_ascii=False, sort_keys=True)
        # every item is valid: only now record the pass (no partial pass in the log)
        for finding_id in addressed:
            proposal = reclassifications.get(finding_id)
            if proposal is not None:
                self.store.append_new(
                    writer=self.store.authority.writer("cli"), kind="reclassify", at=at,
                    stage="review", attempt=attempt,
                    payload={"canonical_id": finding_id, "pass": pass_number,
                             "from": proposal["from"], "to": proposal["to"],
                             "reason": proposal["reason"]})
            payload = {"canonical_id": finding_id, "pass": pass_number,
                       "hunk": hunks[finding_id]}
            if finding_id in causes:
                payload["cause"] = causes[finding_id]
            if finding_id in proofs:
                payload["proof"] = proofs[finding_id]
            if finding_id in solutions:
                payload["solution"] = solutions[finding_id]
            if finding_id in plans:
                payload["evidence_plan"] = plans[finding_id]
            self.store.append_new(
                writer=self.store.authority.writer("cli"), kind="address", at=at,
                stage="review", attempt=attempt, payload=payload)
        # the pass lives only in its logged address events (K4, F21): no side file
        return {"pass": pass_number, "addressed": sorted(addressed),
                "hunks": hunks, "proofs": proofs, "causes": causes,
                "reclassifications": reclassifications}

    def _text(self, value: Any) -> str | None:
        """An event payload string, read from its audit blob when it was moved there. A
        blob that cannot be read is listed in ``missing_blobs`` (the engine records it)."""
        if isinstance(value, Mapping) and isinstance(value.get("blob"), str):
            try:
                return self.store.read_blob(value["blob"]).decode("utf-8")
            except (OSError, UnicodeDecodeError):
                self.missing_blobs.append(value["blob"][:12])
                return None
        return value if isinstance(value, str) else None

    def record_cli_proofs(self, value: Mapping[str, Any], *, at: str,
                          attempt: int) -> list[dict[str, Any]]:
        if not isinstance(value, Mapping) or set(value) != {"pass", "proofs"}:
            raise ReviewError("proof artifact must contain exactly pass and proofs")
        pass_number, proof_rows = value["pass"], value["proofs"]
        if not isinstance(pass_number, int) or isinstance(pass_number, bool) or pass_number < 1 \
                or not isinstance(proof_rows, list) or not proof_rows:
            raise ReviewError("proof pass/rows are malformed")
        findings = {row["canonical_id"]: row for row in self.findings()}
        emitted = []
        for index, proof in enumerate(proof_rows, 1):
            if not isinstance(proof, Mapping):
                raise ReviewError(f"proof {index} must be an object")
            finding_id, evidence_type = proof.get("finding_id"), proof.get("evidence_type")
            if finding_id not in findings:
                raise ReviewError(f"proof {index} references an unknown finding")
            if evidence_type not in {"diff_assertion", "refuted"}:
                raise ReviewError(f"proof {index} has unsupported evidence_type")
            payload = {key: item for key, item in proof.items() if key != "finding_id"}
            payload.update({"canonical_id": finding_id, "pass": pass_number})
            event, _ = self.store.append_new(
                writer=self.store.authority.writer("cli"), kind="proof", at=at,
                stage="review", attempt=attempt, payload=payload)
            emitted.append(event)
        return emitted

    def r2_input(self, expert: str, finding_ids: Sequence[str], *,
                 hunks: Mapping[str, str], proofs: Mapping[str, str],
                 reclassifications: Mapping[str, Mapping[str, Any]]) -> str:
        rows = {row["canonical_id"]: row for row in self.findings()}
        payload = []
        for finding_id in finding_ids:
            row = rows.get(finding_id)
            if row is None or row["expert"] != expert:
                raise ReviewError("R2 roster does not match expert findings")
            payload.append({"canonical_id": finding_id, "finding": row,
                            "fix_hunk": hunks.get(finding_id),
                            "proof": proofs.get(finding_id),
                            "reclassification": reclassifications.get(finding_id)})
        return "R2 ONLY — review these addressed findings, not the whole diff:\n" + json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2)

    def render_review(self, events: list[dict[str, Any]] | None = None) -> str:
        """REVIEW.md, from events only (design §E2, conformance F20): each dispatch with
        its prompt sha and parse state, each finding with its R2 status, its
        reclassification and whether that was confirmed, and the event that decided it;
        the recorded checks; and the rules by which the review checks decide."""
        from core.run_protocol import checks as check_registry
        events = self.store.read_events() if events is None else events
        findings = self.findings(events)
        handouts = {row["event_id"]: row for row in events if row["kind"] == "handout"}
        answers = {row["payload"].get("handout_event_id"): row
                   for row in events if row["kind"] == "answer"}
        lines = ["# Review", "", "## Dispatches", "",
                 "| Stage | Round | Expert | Prompt sha | Handout | Answer | State | Verdict |",
                 "|---|---:|---|---|---|---|---|---|"]
        for handout in handouts.values():
            payload = handout["payload"]
            answer = answers.get(handout["event_id"])
            lines.append(
                f"| {handout.get('stage') or '—'} | {handout.get('round') or '—'} | "
                f"{payload.get('expert_id') or '—'} | {str(payload.get('prompt_sha') or '—')[:12]} | "
                f"{handout['event_id'][:12]} | {answer['event_id'][:12] if answer else '—'} | "
                f"{answer['payload'].get('state') if answer else 'unanswered'} | "
                f"{(answer['payload'].get('verdict') if answer else None) or '—'} |")
        lines += ["", "## Findings", "",
                  "| ID | Severity | Kind | Site | State | R2 | Reclassified | Evidence | Push | Claim |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for row in findings:
            change = row.get("reclassification")
            reclass = (f"{change['from']}→{change['to']} "
                       f"({'confirmed' if change.get('confirmed') else 'unconfirmed'})"
                       if isinstance(change, Mapping) else "—")
            evidence = str(row.get("evidence_event_id") or "—")[:12]
            lines.append(f"| {row['canonical_id']} | {row['severity']} | {row['kind']} | "
                         f"{row['file']}:{row['line']} | {row['status']} | "
                         f"{row.get('r2_status') or '—'} | {reclass} | {evidence} | "
                         f"{'yes' if push_effect(row) else 'no'} | {plain(row['claim'])} |")
        lines += ["", "## Recorded checks", ""]
        lines += [f"- {row['payload']['check_id']}: "
                  f"{'PASS' if row['payload']['passed'] else 'FAIL'} — {row['payload']['detail']}"
                  f" (event {row['event_id'][:12]})"
                  for row in events if row["kind"] == "check"]
        effect_text = {"stage": "blocks the stage", "land": "blocks landing", "push": "blocks push",
                       "": "recorded only"}
        lines += ["", "## Decision rules", ""]
        for spec in check_registry.CHECKS.values():
            if spec.document == "review":
                effects = sorted(spec.effects) or [""]
                lines.append(f"- {spec.check_id} ({', '.join(spec.stages)}): a failure "
                             + " and ".join(effect_text.get(e, e) for e in effects))
        return "\n".join(lines).rstrip() + "\n"

    def render_fixing_passes(self, events: list[dict[str, Any]] | None = None) -> str | None:
        """FIXING_SPEC.md from events (design §B6, F-10): one section per fix pass —
        its clusters (the ±10-line groups of what the pass addressed), the hunk and cause
        the fixer gave, refutations and reclassifications, and each finding's result
        (folded state + the proof's evidence type). None when no pass exists."""
        events = self.store.read_events() if events is None else events
        passes = sorted({e["payload"]["pass"] for e in events if e.get("kind") == "address"})
        if not passes:
            return None
        rows = {row["canonical_id"]: row for row in self.findings(events)}
        lines = ["# Fixing Spec", ""]
        for number in passes:
            mine = [e for e in events
                    if e.get("kind") == "address" and e["payload"]["pass"] == number]
            addressed = [e["payload"]["canonical_id"] for e in mine]
            given = {e["payload"]["canonical_id"]: e["payload"] for e in mine}
            lines += [f"## Pass {number}", ""]
            for cluster in cluster_findings([rows[c] for c in addressed if c in rows]):
                lines += [f"### {cluster['cluster_id']}", "",
                          f"- Findings: {', '.join(cluster['findings'])}",
                          "- Sites: " + ", ".join(f"{site['file']}:{site['line']}"
                                                  for site in cluster["sites"])]
                for cid in cluster["findings"]:
                    cause = (given.get(cid) or {}).get("cause")
                    if cause:
                        lines.append(f"- Cause ({cid}): {plain(cause)}")
                    given_row = given.get(cid) or {}
                    if given_row.get("solution"):
                        lines.append(f"- Solution ({cid}): {plain(given_row['solution'])}")
                    plan = _json_or_none(given_row.get("evidence_plan"))
                    if plan:
                        lines.append(f"- Evidence plan ({cid}): {plan.get('type')}"
                                     + (f" — {plain(plan['ref'])}" if plan.get("ref") else ""))
                    hunk = self._text(given_row.get("hunk"))
                    if hunk is None and given_row.get("hunk") is not None:
                        lines.append(f"- Hunk ({cid}): missing from the audit area")
                    if hunk:
                        lines += [f"- Hunk ({cid}):", "", "```diff", hunk.rstrip(), "```"]
                lines.append("")
            refuted = [e for e in events if e.get("kind") == "proof"
                       and e["payload"]["pass"] == number
                       and e["payload"].get("evidence_type") == "refuted"]
            for e in refuted:
                lines.append(f"- Refuted {e['payload']['canonical_id']}: "
                             + ", ".join(_ref_text(ref) for ref in e["payload"].get("refs") or []))
            for e in events:
                if e.get("kind") == "reclassify" and e["payload"]["pass"] == number:
                    p = e["payload"]
                    lines.append(f"- Reclassify {p['canonical_id']}: {p['from']} → {p['to']} "
                                 f"({p['reason']})")
            proofs = {e["payload"]["canonical_id"]: e["payload"]["evidence_type"]
                      for e in events if e.get("kind") == "proof" and e["payload"]["pass"] == number}
            lines += ["", "| Finding | State | Evidence |", "|---|---|---|"]
            lines += [f"| {cid} | {rows.get(cid, {}).get('status', 'unknown')} | "
                      f"{proofs.get(cid, '—')} |" for cid in addressed]
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _events_line(events: list[dict[str, Any]]) -> str:
        """The projection's last line (r6 §2.2): the very events its body was rendered
        from (one read serves both, so the line never names events the body lacks)."""
        tail = events[-1]["event_id"] if events else "none"
        return f"\n<!-- rendered from {len(events)} events, last {tail} -->\n"

    def write_review(self) -> str:
        path = self.run_dir / "REVIEW.md"
        events = self.store.read_events()
        self._atomic(path, self.render_review(events) + self._events_line(events))
        return str(path)

    def write_fixing_spec(self) -> str | None:
        events = self.store.read_events()
        fixing = self.render_fixing_passes(events)
        if fixing is None:
            return None
        path = self.run_dir / "FIXING_SPEC.md"
        self._atomic(path, fixing + self._events_line(events))
        return str(path)

    def write_documents(self) -> list[str]:
        """Render REVIEW.md and FIXING_SPEC.md, projections of the logged events only."""
        return [p for p in (self.write_review(), self.write_fixing_spec()) if p]


def plain(value: Any) -> str:
    """Model- or person-written words as plain Markdown text (one encoder: audit's)."""
    from core.run_protocol.audit import _text
    return _text(value, 800)


EVIDENCE_TYPES = frozenset({"reported_test", "diff_assertion", "refuted"})


def _json_or_none(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _ref_text(ref: Any) -> str:
    """One resolved refutation ref (facts.resolve_refs) as REVIEW / FIXING_SPEC show it."""
    if not isinstance(ref, Mapping):
        return str(ref)
    if ref.get("kind") == "path":
        start, end = ref["lines"]
        return f"{ref['path']}:{start}" + (f"-{end}" if end != start else "")
    if ref.get("kind") == "event":
        return f"event:{ref['event_id'][:12]}"
    return f"test:{ref.get('node')} ({ref.get('result')})"
