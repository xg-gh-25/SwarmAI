"""The run REPORT is in one language (English) and carries no SwarmAI-only push advice.

What is tested:
- the two modules that write REPORT text (``core/run_protocol/audit.py`` and
  ``validation_report.py``) have no CJK in their string literals: a REPORT is committed
  into the user's repo, so its fixed wording must read the same for every user and
  collaborator (user content, such as the requirement, is quoted as given);
- ``audit.render`` never appends "build + deploy verification before push": that was
  SwarmAI's own release rule, wrong for a repo with nothing to deploy;
- every branch of the renderer (landed, not landed, partial landing, unsettled
  checkout, manual landing, retained work, Library / kept lessons, unreadable and long
  test reports, push reasons) renders without CJK.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from core.run_protocol import audit

CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
# in source, wording is CJK letters; punctuation may appear as data (stripped from input)
CJK_WORDS = re.compile(r"[\u4e00-\u9fff]")
BACKEND = Path(__file__).resolve().parents[1]
REPORT_MODULES = ("core/run_protocol/audit.py", "core/run_protocol/validation_report.py")


def _literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)}
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_report_modules_have_no_cjk_literals():
    found = [f"{rel}:{line}: {text[:60]!r}" for rel in REPORT_MODULES
             for line, text in _literals(BACKEND / rel) if CJK_WORDS.search(text)]
    assert not found, "\n".join(found)


def _audit(**over):
    """A real ``audit.build`` result (as in test_run_protocol_audit), then *over* merged
    into its top-level keys so each renderer branch can be reached."""
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    report = {"kind": "validation_report", "at": "2026-10-22T00:00:20+00:00",
              "payload": {"stage": "test", "repo": "repo", "code_tree": "1" * 40,
                          "n_pass": 2, "n_fail": 0, "n_not_run": 0}}
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}},
              report, {"kind": "land", "at": "2026-10-22T00:00:30+00:00", "payload": {}}]
    data = audit.build(run_id="run_x", projection=projection, events=events,
                       repos_isolated=1, findings=[], contract=contract,
                       open_checks={},
                       audit_through="COMPLETED", commits={"repo": "a" * 40})
    data = dict(data)
    for key, value in over.items():
        data[key] = {**data.get(key, {}), **value} if isinstance(value, dict) else value
    return data


def test_landed_report_has_no_deploy_line_and_no_cjk():
    text = audit.render(_audit(), fmt="report")
    assert "部署" not in text and "deploy" not in text.lower(), text
    assert not CJK.search(text), text


def test_every_branch_renders_in_english():
    details = {
        "requirement": "make total() reject null", "title": "price",
        "validation": ["## Tests"],
        "test_reports": {"a": [{"result": "unreadable", "reason": "bad json"}],
                         "b": [{"command": f"cmd{i}", "result": "fail", "reason": "r"}
                               for i in range(12)]},
        "library_lessons": [{"id": "L1", "statement": "s", "path": "/p.md"}],
        "kept_lessons": [{"id": "L2", "statement": "s"}],
        "open_checks": {f"k{i}": "d" for i in range(7)},
        "commits": {"a": "b" * 40}, "expected_repos": ["a", "b"],
        "unsettled_checkouts": ["a"],
        "manual": {"request_id": "q1", "note": "n"},
        "retained": [{"ledger": "/ledger"}],
    }
    for outcome in ({"status": "completed", "landing": "not_landed",
                     "reason": "partial_land"},
                    {"status": "completed", "landing": "human_override"}):
        text = audit.render(_audit(outcome=outcome, details=details), fmt="report")
        assert not CJK.search(text), text
    text = audit.render(_audit(audit_through="DELIVER", outcome={"landing": None},
                               details=details), fmt="report")
    assert not CJK.search(text), text
