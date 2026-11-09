"""validation_report — the agent's own test report for BUILD / TEST (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.3.3, S6-1).

The pipeline does not run or observe tests. BUILD and TEST end with
``stage-complete --artifact -`` carrying ``{"reports": [<one per repo>]}``:

    {"repo": "<expected repo name>",
     "commands": [{"command": "...", "cwd": "...",
                   "result": "pass|fail|not_run|pre_existing",
                   "reason": "..."}],          # reason required unless pass
     "tests_changed": ["path", ...],            # tests added or changed
     "rationale": "why these tests show the change is right",
     "risk": ["security"],                      # optional (§3.5.2): dispatches security
     "decisions": [{"id": "D1", "question": "...", "choice": "...",
                    "reason": "..."}]}           # optional: judgment calls made here

``decisions`` (optional, an empty list is fine) are the agent's judgment calls made while
building or testing, in the shape DESIGN_SPEC uses (``schemas.decision_issues`` checks
both). The event payload carries them inline, so the REPORT reads them from the log.

The engine writes one ``validation_report`` event per repo; the event's ``code_tree``
is the tree the engine computes for that repo's worktree at that moment, never a value
the agent supplied. The log records only that this report was submitted on this tree;
whether its content is true is for review to judge.

Public: ``parse``, ``event_payload``, ``read``, ``latest``, ``report_lines``, ``reports_reviewed``,
``STAGES``, ``RESULTS``, ``RISKS``, ``ReportError``.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

# review: a fix's tests, reported for a ``reported_test`` proof (S6-2)
STAGES = frozenset({"build", "review", "test"})
# pre_existing: it ran and failed the same way on the run's base (red before the run;
# design 2026-11-01-pipeline-delivery-friction D6): like not_run it does not block, and
# the REPORT lists it apart, as the agent's statement
RESULTS = ("pass", "fail", "not_run", "pre_existing")
MAX_COMMANDS = 50
MAX_TESTS = 200
MAX_COMMAND_CHARS = 2_000
MAX_REASON_CHARS = 1_000
MAX_RATIONALE_CHARS = 6_000
_KEYS = {"repo", "commands", "tests_changed", "rationale", "risk", "decisions"}
# what a report may declare about its change (§3.5.2); each name dispatches a reviewer
RISKS = ("security",)
_COMMAND_KEYS = {"command", "cwd", "result", "reason"}


class ReportError(ValueError):
    """The submitted report does not follow the schema; the message says what to fix."""


def _text(value: Any, where: str, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ReportError(f"{where} must be a non-empty string")
    if len(value) > limit:
        raise ReportError(f"{where} is longer than {limit} characters")
    return value


def _one(row: Any, i: int, expected: Sequence[str]) -> dict[str, Any]:
    where = f"reports[{i}]"
    if not isinstance(row, Mapping):
        raise ReportError(f"{where} must be an object")
    extra = set(row) - _KEYS
    if extra:
        raise ReportError(f"{where} has unknown keys {sorted(extra)}")
    repo = row.get("repo")
    if repo not in expected:
        raise ReportError(f"{where}.repo must be one of this run's repos {list(expected)}, "
                          f"not {repo!r}")
    commands = row.get("commands")
    if not isinstance(commands, list) or len(commands) > MAX_COMMANDS:
        raise ReportError(f"{where}.commands must be a list of at most {MAX_COMMANDS}")
    out_commands = []
    for j, cmd in enumerate(commands):
        cw = f"{where}.commands[{j}]"
        if not isinstance(cmd, Mapping) or set(cmd) - _COMMAND_KEYS:
            raise ReportError(f"{cw} must be an object with keys {sorted(_COMMAND_KEYS)}")
        result = cmd.get("result")
        if result not in RESULTS:
            raise ReportError(f"{cw}.result must be one of {list(RESULTS)}")
        item = {"command": _text(cmd.get("command"), f"{cw}.command", MAX_COMMAND_CHARS),
                "cwd": _text(cmd.get("cwd", ""), f"{cw}.cwd", MAX_COMMAND_CHARS, empty=True),
                "result": result}
        if result != "pass":
            item["reason"] = _text(cmd.get("reason"), f"{cw}.reason (required when the "
                                   f"result is {result})", MAX_REASON_CHARS)
        elif cmd.get("reason"):
            item["reason"] = _text(cmd.get("reason"), f"{cw}.reason", MAX_REASON_CHARS)
        out_commands.append(item)
    tests = row.get("tests_changed")
    if not isinstance(tests, list) or len(tests) > MAX_TESTS:
        raise ReportError(f"{where}.tests_changed must be a list of at most {MAX_TESTS}")
    tests = [_text(t, f"{where}.tests_changed[{k}]", MAX_COMMAND_CHARS)
             for k, t in enumerate(tests)]
    out = {"repo": repo, "commands": out_commands, "tests_changed": tests,
           "rationale": _text(row.get("rationale"), f"{where}.rationale",
                              MAX_RATIONALE_CHARS)}
    if "risk" in row:
        risk = row["risk"]
        if not isinstance(risk, list) or any(r not in RISKS for r in risk):
            raise ReportError(f"{where}.risk must be a list of {list(RISKS)}")
        if risk:
            out["risk"] = sorted(set(risk))
    if "decisions" in row:
        from core.run_protocol.schemas import decision_issues
        problems = decision_issues(row["decisions"], f"{where}.decisions")
        if problems:
            raise ReportError("; ".join(f"{path}: {message}" for path, message in problems))
        if row["decisions"]:
            out["decisions"] = [{key: entry[key].strip()
                                 for key in ("id", "question", "choice", "reason")}
                                for entry in row["decisions"]]
    return out


def parse(artifact: Any, expected_repos: Sequence[str]) -> list[dict[str, Any]]:
    """The normalised reports in *artifact* (``{"reports": [...]}``): exactly one per
    expected repo. Raises ReportError."""
    if not isinstance(artifact, Mapping) or not isinstance(artifact.get("reports"), list):
        raise ReportError('the artifact must be {"reports": [one report per repo]}')
    expected = list(expected_repos)
    rows = [_one(row, i, expected) for i, row in enumerate(artifact["reports"])]
    names = [row["repo"] for row in rows]
    if len(names) != len(set(names)):
        raise ReportError("one report per repo: a repo appears twice")
    missing = sorted(set(expected) - set(names))
    if missing:
        raise ReportError(f"missing a report for {missing}")
    return rows


def event_payload(stage: str, report: Mapping[str, Any], code_tree: str) -> dict[str, Any]:
    """The ``validation_report`` event payload: summary inline, the report as a blob."""
    counts = {r: sum(1 for c in report["commands"] if c["result"] == r) for r in RESULTS}
    return {"stage": stage, "repo": report["repo"], "code_tree": code_tree,
            "n_pass": counts["pass"], "n_fail": counts["fail"],
            "n_not_run": counts["not_run"],
            **({"n_pre_existing": counts["pre_existing"]} if counts["pre_existing"] else {}),
            # inline, so the REPORT's Decisions section is a function of the log alone
            **({"decisions": [dict(d) for d in report["decisions"]]}
               if report.get("decisions") else {}),
            "report_json": json.dumps(
                report, ensure_ascii=False, sort_keys=True)}


def read(store: Any, event: Mapping[str, Any]) -> dict[str, Any]:
    """The full report of one ``validation_report`` event (its blob)."""
    raw = event["payload"]["report_json"]
    if isinstance(raw, Mapping):
        raw = store.read_blob(raw["blob"]).decode("utf-8")
    return json.loads(raw)


def latest(events: Sequence[Mapping[str, Any]], stage: str | None = None) -> dict[str, dict]:
    """repo -> the last ``validation_report`` event (of *stage*, when given)."""
    out: dict[str, dict] = {}
    for event in events:
        if event.get("kind") != "validation_report":
            continue
        payload = event.get("payload") or {}
        if stage is None or payload.get("stage") == stage:
            out[payload.get("repo")] = dict(event)
    return out


def find_command(store: Any, events: Sequence[Mapping[str, Any]], *, repo: str,
                 code_tree: str, command: str) -> "tuple[dict, dict] | None":
    """The newest report of *repo* on *code_tree* (any stage) with a ``pass`` command
    whose text is exactly *command*: ``(event, report)``, else None (S6-2)."""
    for event in reversed(list(events)):
        payload = event.get("payload") or {}
        if event.get("kind") != "validation_report" or payload.get("repo") != repo \
                or payload.get("code_tree") != code_tree:
            continue
        report = read(store, event)
        if any(row.get("command") == command and row.get("result") == "pass"
               for row in report.get("commands", [])):
            return dict(event), report
    return None


def report_lines(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """REPORT lines from the events alone (the REPORT is a pure function of the log):
    per stage and repo, the latest report's tree and counts. The commands themselves
    are in the report blob (``read``)."""
    lines: list[str] = []
    for stage in ("build", "test"):
        for repo, event in sorted(latest(events, stage).items()):
            p = event["payload"]
            lines.append(f"- {stage} · {repo} @ {str(p['code_tree'])[:12]}: "
                         f"{p['n_pass']} pass, {p['n_fail']} fail, {p['n_not_run']} not run")
    if lines:
        head = ("## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)"
                if reports_reviewed(events) else
                "## Tests (reported by the agent only: not rerun by the pipeline, not checked by a review expert)")
        lines = [head, ""] + lines
    return lines


def reports_reviewed(events: Sequence[Mapping[str, Any]]) -> bool:
    """A REVIEW expert answered: someone other than the agent read the run's reports
    (the review handout carries them). Without one, the reports are the agent's word
    alone, and the REPORT says so."""
    return any(event.get("kind") == "answer" and event.get("stage") == "review"
               for event in events)
