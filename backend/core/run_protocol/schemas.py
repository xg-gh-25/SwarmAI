"""Strict document schemas for Pipeline v1 (v13 §B1-§B2).

Public symbols:
- ``validate_requirements`` — validate full/lite requirements and structured EARS.
- ``validate_design_spec`` — validate full/lite design, IDs and references.
- ``SchemaIssue`` — one check-id/path/message result.

Validation is pure and language-independent. Unknown fields are errors: obsolete model
self-claims cannot silently survive the v1 cutover.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

REQ_ID = re.compile(r"^R[1-9][0-9]*$")
AC_ID = re.compile(r"^R[1-9][0-9]*\.[1-9][0-9]*$")
BUG_AC_ID = re.compile(r"^[CEU][1-9][0-9]*$")
PROP_ID = re.compile(r"^P[1-9][0-9]*$")
FAIL_ID = re.compile(r"^F[1-9][0-9]*$")
TASK_ID = re.compile(r"^T[1-9][0-9]*$")
EXIT_ID = re.compile(r"^X[1-9][0-9]*$")
EARS_PATTERNS = frozenset({"ubiquitous", "event", "state", "unwanted", "optional"})
ANALYSIS_KINDS = frozenset({"conflict", "vague", "undefined_term", "missing_edge"})
ANALYSIS_RESOLUTIONS = frozenset({"rewritten", "confirmed", "asked"})


@dataclass(frozen=True)
class SchemaIssue:
    """A deterministic document-schema failure."""

    check_id: str
    path: str
    message: str


def _issue(out: list[SchemaIssue], check_id: str, path: str, message: str) -> None:
    out.append(SchemaIssue(check_id, path, message))


def _object(value: Any, out: list[SchemaIssue], check: str, path: str) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        _issue(out, check, path, "must be an object")
        return None
    return value


def _strict(obj: Mapping[str, Any], allowed: set[str], out: list[SchemaIssue],
            check: str, path: str) -> None:
    for key in sorted(set(obj) - allowed):
        _issue(out, check, f"{path}.{key}", "unknown field")


def _text(obj: Mapping[str, Any], key: str, out: list[SchemaIssue],
          check: str, path: str, *, required: bool = True) -> str | None:
    value = obj.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        _issue(out, check, f"{path}.{key}", "must be non-empty text")
        return None
    return value.strip()


def _list(value: Any, out: list[SchemaIssue], check: str, path: str,
          *, nonempty: bool = False) -> list[Any] | None:
    if not isinstance(value, list) or (nonempty and not value):
        _issue(out, check, path, "must be a non-empty list" if nonempty else "must be a list")
        return None
    return value


def _repo_path(value: Any, out: list[SchemaIssue], check: str, path: str) -> bool:
    if not isinstance(value, str) or not value:
        _issue(out, check, path, "must be a repo-relative file path")
        return False
    normalized = value.replace("\\", "/")
    if value.startswith(("/", ":")) or (len(value) > 1 and value[1] == ":") \
            or "\x00" in value or normalized.endswith("/") \
            or any(part in ("", ".", "..") for part in normalized.split("/")) \
            or any(char in value for char in "*?[]"):
        _issue(out, check, path, "must be a repo-relative file path without traversal or glob")
        return False
    return True


def _nodeid(value: Any, out: list[SchemaIssue], check: str, path: str) -> bool:
    if not isinstance(value, str) or not value:
        _issue(out, check, path, "must be a test nodeid")
        return False
    file = value.split("::", 1)[0]
    if not _repo_path(file, out, check, path):
        return False
    if not file.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".rs")):
        _issue(out, check, path, "test nodeid must begin with a supported test file")
        return False
    return True


def _acceptance(value: Any, out: list[SchemaIssue], path: str,
                expected: re.Pattern[str]) -> str | None:
    obj = _object(value, out, "RQ-2", path)
    if obj is None:
        return None
    _strict(obj, {"id", "pattern", "condition", "response", "negative", "universal", "text"},
            out, "RQ-2", path)
    ac_id = _text(obj, "id", out, "RQ-1", path)
    if ac_id and not expected.fullmatch(ac_id):
        _issue(out, "RQ-1", f"{path}.id", "has the wrong acceptance ID form")
    pattern = obj.get("pattern")
    if pattern not in EARS_PATTERNS:
        _issue(out, "RQ-2", f"{path}.pattern", "must be a supported EARS pattern")
    if pattern != "ubiquitous":
        _text(obj, "condition", out, "RQ-2", path)
    elif obj.get("condition") not in (None, ""):
        _issue(out, "RQ-2", f"{path}.condition", "ubiquitous acceptance has no condition")
    _text(obj, "response", out, "RQ-2", path)
    for key in ("negative", "universal"):
        if not isinstance(obj.get(key), bool):
            _issue(out, "RQ-2", f"{path}.{key}", "must be boolean")
    if "text" in obj and not isinstance(obj["text"], str):
        _issue(out, "RQ-2", f"{path}.text", "must be text")
    return ac_id


def _unique(ids: list[str], out: list[SchemaIssue], check: str, path: str) -> None:
    seen: set[str] = set()
    for value in ids:
        if value in seen:
            _issue(out, check, path, f"duplicate id {value}")
        seen.add(value)


def _requirements(value: Any, out: list[SchemaIssue], variant: str) -> set[str]:
    rows = _list(value, out, "RQ-1", "requirements", nonempty=True) or []
    ids, refs, all_ac_ids = [], set(), []
    for i, value in enumerate(rows):
        path = f"requirements[{i}]"
        obj = _object(value, out, "RQ-1", path)
        if obj is None:
            continue
        _strict(obj, {"id", "story", "acceptance"}, out, "RQ-1", path)
        rid = _text(obj, "id", out, "RQ-1", path)
        if rid:
            ids.append(rid)
            if not REQ_ID.fullmatch(rid):
                _issue(out, "RQ-1", f"{path}.id", "must match R<n>")
        if variant == "full":
            _text(obj, "story", out, "RQ-1", path)
        elif "story" in obj and obj["story"] is not None and not isinstance(obj["story"], str):
            _issue(out, "RQ-1", f"{path}.story", "must be text")
        acceptance = _list(obj.get("acceptance"), out, "RQ-1",
                           f"{path}.acceptance", nonempty=True) or []
        ac_ids = [_acceptance(ac, out, f"{path}.acceptance[{j}]", AC_ID)
                  for j, ac in enumerate(acceptance)]
        clean = [x for x in ac_ids if x]
        _unique(clean, out, "RQ-1", f"{path}.acceptance")
        for j, ac_id in enumerate(ac_ids):
            if ac_id and rid and not ac_id.startswith(rid + "."):
                _issue(out, "RQ-1", f"{path}.acceptance[{j}].id",
                       f"must belong to {rid}")
        all_ac_ids.extend(clean)
        refs.update(clean)
    _unique([x for x in ids if x], out, "RQ-1", "requirements")
    _unique(all_ac_ids, out, "RQ-1", "requirements.acceptance")
    return refs


def _bugfix(value: Any, out: list[SchemaIssue]) -> None:
    obj = _object(value, out, "RQ-4", "bugfix")
    if obj is None:
        return
    _strict(obj, {"current", "expected", "unchanged"}, out, "RQ-4", "bugfix")
    all_ids: list[str] = []
    for key, prefix in (("current", "C"), ("expected", "E"), ("unchanged", "U")):
        rows = _list(obj.get(key), out, "RQ-4", f"bugfix.{key}", nonempty=True) or []
        for i, row in enumerate(rows):
            ac_id = _acceptance(row, out, f"bugfix.{key}[{i}]", BUG_AC_ID)
            if ac_id:
                all_ids.append(ac_id)
                if not ac_id.startswith(prefix):
                    _issue(out, "RQ-4", f"bugfix.{key}[{i}].id",
                           f"must start with {prefix}")
    _unique(all_ids, out, "RQ-1", "bugfix")


def _analysis(value: Any, out: list[SchemaIssue]) -> None:
    rows = _list(value, out, "RQ-5", "analysis") or []
    for i, value in enumerate(rows):
        path = f"analysis[{i}]"
        obj = _object(value, out, "RQ-5", path)
        if obj is None:
            continue
        _strict(obj, {"kind", "refs", "note", "resolution"}, out, "RQ-5", path)
        if obj.get("kind") not in ANALYSIS_KINDS:
            _issue(out, "RQ-5", f"{path}.kind", "unknown analysis kind")
        refs = _list(obj.get("refs"), out, "RQ-5", f"{path}.refs", nonempty=True) or []
        if any(not isinstance(ref, str) or not ref for ref in refs):
            _issue(out, "RQ-5", f"{path}.refs", "refs must be non-empty text")
        _text(obj, "note", out, "RQ-5", path)
        if obj.get("resolution") not in ANALYSIS_RESOLUTIONS:
            _issue(out, "RQ-5", f"{path}.resolution", "unknown resolution")


def _clarifications(value: Any, out: list[SchemaIssue]) -> None:
    rows = _list(value, out, "RQ-6", "clarifications") or []
    for i, value in enumerate(rows):
        path = f"clarifications[{i}]"
        obj = _object(value, out, "RQ-6", path)
        if obj is None:
            continue
        _strict(obj, {"qid", "question", "answer", "answered_by", "decided_by"},
                out, "RQ-6", path)
        for key in ("qid", "question", "answer"):
            _text(obj, key, out, "RQ-6", path)
        if obj.get("answered_by") not in ("user", "evidence"):
            _issue(out, "RQ-6", f"{path}.answered_by", "must be user or evidence")
        if "decided_by" in obj and (obj["decided_by"] != "agent_after_cap"
                                    or obj.get("answered_by") != "evidence"):
            # the agent's own choice after the decision cap (DC-2, conformance F12)
            _issue(out, "RQ-6", f"{path}.decided_by",
                   "only agent_after_cap, answered by evidence")


def _understanding(value: Any, out: list[SchemaIssue]) -> None:
    obj = _object(value, out, "RQ-5", "understanding")
    if obj is None:
        return
    _strict(obj, {"claim", "readings", "evidence", "root_cause"},
            out, "RQ-5", "understanding")
    _text(obj, "claim", out, "RQ-5", "understanding")
    for key in ("readings", "evidence"):
        rows = _list(obj.get(key), out, "RQ-5", f"understanding.{key}") or []
        if any(not isinstance(row, str) or not row.strip() for row in rows):
            _issue(out, "RQ-5", f"understanding.{key}", "entries must be non-empty text")
    if "root_cause" in obj and obj["root_cause"] is not None \
            and not isinstance(obj["root_cause"], Mapping):
        _issue(out, "RQ-5", "understanding.root_cause", "must be an object")


def validate_requirements(document: Any, *, variant: str, profile: str) -> list[SchemaIssue]:
    """Validate v13 REQUIREMENTS ``full`` or ``lite``; return every issue at once."""
    out: list[SchemaIssue] = []
    obj = _object(document, out, "RQ-1", "$")
    if obj is None:
        return out
    if variant not in ("full", "lite"):
        return [SchemaIssue("RQ-1", "$", f"unknown requirements variant {variant!r}")]
    allowed = {"requirements"}
    if variant == "full":
        allowed |= {"bugfix", "analysis", "clarifications", "understanding", "prior_knowledge"}
    _strict(obj, allowed, out, "RQ-1", "$")
    _requirements(obj.get("requirements"), out, variant)
    if variant == "lite":
        return out
    if profile == "bugfix":
        _bugfix(obj.get("bugfix"), out)
    elif "bugfix" in obj:
        _issue(out, "RQ-4", "bugfix", "is allowed only for the bugfix profile")
    _analysis(obj.get("analysis"), out)
    _clarifications(obj.get("clarifications"), out)
    _understanding(obj.get("understanding"), out)
    prior = _list(obj.get("prior_knowledge"), out, "RQ-5", "prior_knowledge") or []
    for i, value in enumerate(prior):
        path = f"prior_knowledge[{i}]"
        row = _object(value, out, "RQ-5", path)
        if row is not None:
            _strict(row, {"entry", "store"}, out, "RQ-5", path)
            _text(row, "entry", out, "RQ-5", path)
            _text(row, "store", out, "RQ-5", path)
    return out


def _id_rows(value: Any, out: list[SchemaIssue], *, check: str, path: str,
             pattern: re.Pattern[str], allowed: set[str], required: set[str]) -> list[Mapping[str, Any]]:
    rows = _list(value, out, check, path) or []
    valid, ids = [], []
    for i, value in enumerate(rows):
        item_path = f"{path}[{i}]"
        obj = _object(value, out, check, item_path)
        if obj is None:
            continue
        _strict(obj, allowed, out, check, item_path)
        item_id = _text(obj, "id", out, check, item_path)
        if item_id:
            ids.append(item_id)
            if not pattern.fullmatch(item_id):
                _issue(out, check, f"{item_path}.id", "has the wrong ID form")
        for key in required - {"id"}:
            if key not in obj:
                _issue(out, check, f"{item_path}.{key}", "is required")
        valid.append(obj)
    _unique(ids, out, check, path)
    return valid


def _refs(value: Any, out: list[SchemaIssue], path: str, known: set[str],
          *, nonempty: bool = True) -> list[str]:
    rows = _list(value, out, "DS-5", path, nonempty=nonempty) or []
    refs: list[str] = []
    for i, ref in enumerate(rows):
        if not isinstance(ref, str) or not ref:
            _issue(out, "DS-5", f"{path}[{i}]", "reference must be non-empty text")
        else:
            refs.append(ref)
            if ref not in known:
                _issue(out, "DS-5", f"{path}[{i}]", f"unknown reference {ref}")
    return refs


def _consumers(document: Mapping[str, Any], out: list[SchemaIssue]) -> None:
    consumers, reason = document.get("consumers"), document.get("no_contract_change_reason")
    if consumers is not None and reason is not None:
        _issue(out, "DS-7", "$", "provide consumers or no_contract_change_reason, not both")
        return
    if consumers is None and reason is None:
        _issue(out, "DS-7", "$", "consumers or no_contract_change_reason is required")
        return
    if reason is not None:
        if not isinstance(reason, str) or not reason.strip():
            _issue(out, "DS-7", "no_contract_change_reason", "must be non-empty text")
        return
    rows = _list(consumers, out, "DS-7", "consumers", nonempty=True) or []
    for i, value in enumerate(rows):
        path = f"consumers[{i}]"
        obj = _object(value, out, "DS-7", path)
        if obj is None:
            continue
        _strict(obj, {"id", "contract", "pattern", "paths", "readers"}, out, "DS-7", path)
        for key in ("id", "contract", "pattern"):
            _text(obj, key, out, "DS-7", path)
        for key in ("paths", "readers"):
            rows2 = _list(obj.get(key), out, "DS-7", f"{path}.{key}", nonempty=True) or []
            if any(not isinstance(v, (str, Mapping)) for v in rows2):
                _issue(out, "DS-7", f"{path}.{key}", "entries must be text or objects")


def _design_rows(document: Mapping[str, Any], requirement_ids: set[str],
                 out: list[SchemaIssue], existing_files: set[str] | None = None) -> None:
    planned_test_files = {
        file for task in document.get("tasks", []) if isinstance(task, Mapping)
        for file in (task.get("creates_test_files") or []) if isinstance(file, str)
    }

    def test_exists(nodeid: Any, issue_path: str) -> None:
        if existing_files is None or not isinstance(nodeid, str):
            return
        file = nodeid.split("::", 1)[0]
        if file not in existing_files and file not in planned_test_files:
            _issue(out, "DS-2a", issue_path, "test file does not exist and is not planned")
    properties = _id_rows(
        document.get("properties", []), out, check="DS-2a", path="properties",
        pattern=PROP_ID, allowed={"id", "statement", "validates", "test", "kind"},
        required={"id", "statement", "validates", "test", "kind"},
    )
    prop_ids = {row.get("id") for row in properties if isinstance(row.get("id"), str)}
    for i, row in enumerate(properties):
        _text(row, "statement", out, "DS-2a", f"properties[{i}]")
        _nodeid(row.get("test"), out, "DS-2a", f"properties[{i}].test")
        test_exists(row.get("test"), f"properties[{i}].test")
        if row.get("kind") not in ("example", "pbt"):
            _issue(out, "DS-2a", f"properties[{i}].kind", "must be example or pbt")
        _refs(row.get("validates"), out, f"properties[{i}].validates", requirement_ids)

    failures = _id_rows(
        document.get("failure_modes", []), out, check="DS-2a", path="failure_modes",
        pattern=FAIL_ID, allowed={"id", "scenario", "expected", "test"},
        required={"id", "scenario", "expected", "test"},
    )
    fail_ids = {row.get("id") for row in failures if isinstance(row.get("id"), str)}
    for i, row in enumerate(failures):
        for key in ("scenario", "expected"):
            _text(row, key, out, "DS-2a", f"failure_modes[{i}]")
        _nodeid(row.get("test"), out, "DS-2a", f"failure_modes[{i}].test")
        test_exists(row.get("test"), f"failure_modes[{i}].test")

    tasks = _id_rows(
        document.get("tasks"), out, check="DS-3", path="tasks", pattern=TASK_ID,
        allowed={"id", "file", "symbol", "change", "requirements", "depends_on",
                 "creates_test_files"},
        required={"id", "file", "symbol", "change", "requirements", "depends_on",
                  "creates_test_files"},
    )
    task_ids = {row.get("id") for row in tasks if isinstance(row.get("id"), str)}
    for i, row in enumerate(tasks):
        path = f"tasks[{i}]"
        _repo_path(row.get("file"), out, "DS-3", f"{path}.file")
        for key in ("symbol", "change"):
            _text(row, key, out, "DS-3", path)
        _refs(row.get("requirements"), out, f"{path}.requirements", requirement_ids)
        _refs(row.get("depends_on"), out, f"{path}.depends_on", task_ids, nonempty=False)
        files = _list(row.get("creates_test_files"), out, "DS-2a",
                      f"{path}.creates_test_files") or []
        for j, value in enumerate(files):
            _repo_path(value, out, "DS-2a", f"{path}.creates_test_files[{j}]")

    known = requirement_ids | prop_ids | fail_ids
    exits = _id_rows(
        document.get("exit_criteria"), out, check="DS-9a", path="exit_criteria",
        pattern=EXIT_ID, allowed={"id", "kind", "ref", "argv", "expect", "covers"},
        required={"id", "kind", "covers"},
    )
    for i, row in enumerate(exits):
        path = f"exit_criteria[{i}]"
        kind = row.get("kind")
        if kind not in ("test", "probe", "mutation", "manual"):
            _issue(out, "DS-9a", f"{path}.kind", "unknown exit kind")
        if kind == "probe":
            from core.run_protocol import probe as safe_probe
            if "expect" not in row:
                _issue(out, "DS-9a", f"{path}.expect", "is required for a probe")
            try:
                safe_probe.validate(row.get("argv"), row.get("expect"))
            except safe_probe.ProbeError as exc:
                _issue(out, "DS-9a", f"{path}.argv", str(exc))
        elif kind in ("test", "mutation"):
            _nodeid(row.get("ref"), out, "DS-9a", f"{path}.ref")
            test_exists(row.get("ref"), f"{path}.ref")
        else:
            _text(row, "ref", out, "DS-9a", path)
        _refs(row.get("covers"), out, f"{path}.covers", known)


def validate_design_spec(document: Any, *, variant: str,
                         requirement_ids: set[str],
                         existing_files: set[str] | None = None) -> list[SchemaIssue]:
    """Validate v13 DESIGN_SPEC ``full`` or ``lite`` and all ID references."""
    out: list[SchemaIssue] = []
    obj = _object(document, out, "DS-5", "$")
    if obj is None:
        return out
    if variant not in ("full", "lite"):
        return [SchemaIssue("DS-5", "$", f"unknown design variant {variant!r}")]
    common = {"properties", "failure_modes", "tasks", "exit_criteria", "consumers",
              "no_contract_change_reason", "security_boundaries"}
    allowed = common | ({"alternatives", "chosen", "design"} if variant == "full" else set())
    _strict(obj, allowed, out, "DS-5", "$")
    if not isinstance(requirement_ids, set) or any(not isinstance(x, str) for x in requirement_ids):
        _issue(out, "DS-5", "requirement_ids", "must be a set of requirement IDs")
        requirement_ids = set()
    if variant == "full":
        if "security_boundaries" not in obj:
            _issue(out, "DS-6", "security_boundaries", "is required in full DESIGN_SPEC")
        # full names its properties and failure modes, even when there are none
        # (v13 §B2, conformance L2): an empty list is an answer, a missing key is not
        for key in ("properties", "failure_modes"):
            if key not in obj:
                _issue(out, "DS-6", key, "is required in full DESIGN_SPEC (an empty list is fine)")
        alternatives = _list(obj.get("alternatives"), out, "DS-6", "alternatives",
                             nonempty=True) or []
        if any(not isinstance(item, Mapping) for item in alternatives):
            _issue(out, "DS-6", "alternatives", "entries must be objects")
        chosen = obj.get("chosen")
        if not isinstance(chosen, (str, Mapping)) or not chosen:
            _issue(out, "DS-6", "chosen", "must identify the selected alternative")
        design = _object(obj.get("design"), out, "DS-6", "design")
        if design is not None:
            _strict(design, {"user_workflow", "architecture", "flow", "states"},
                    out, "DS-6", "design")
            for key in ("user_workflow", "architecture", "flow"):
                _text(design, key, out, "DS-6", "design")
            states = _list(design.get("states"), out, "DS-6", "design.states",
                           nonempty=True) or []
            for i, value in enumerate(states):
                path = f"design.states[{i}]"
                state = _object(value, out, "DS-6", path)
                if state is not None:
                    _strict(state, {"name", "created_by", "removed_by"},
                            out, "DS-6", path)
                    for key in ("name", "created_by", "removed_by"):
                        _text(state, key, out, "DS-6", path)
    for required in ("tasks", "exit_criteria"):
        if not isinstance(obj.get(required), list) or not obj.get(required):
            _issue(out, "DS-5", required, "must be a non-empty list")
    _design_rows(obj, requirement_ids, out, existing_files)
    _consumers(obj, out)
    if "security_boundaries" in obj:
        rows = _list(obj["security_boundaries"], out, "DS-6",
                     "security_boundaries") or []
        if any(not isinstance(row, (str, Mapping)) for row in rows):
            _issue(out, "DS-6", "security_boundaries", "entries must be text or objects")
    return out




def _has_explicit_failure_edge(flow: str) -> bool:
    """A failure branch is a separate ``failure-condition -> outcome`` edge."""
    for edge in re.split(r"[;\n]+", flow):
        match = re.fullmatch(r"\s*(.+?)\s*->\s*(.+?)\s*", edge)
        if match and re.search(
            r"(?:^|\W)(?:fail(?:ure|ed)?|error|invalid|reject(?:ed)?|denied)(?:\W|$)"
            r"|失败|错误|拒绝|无效",
            match.group(1), re.I,
        ):
            return True
    return False
def validate_contract(requirements: Mapping[str, Any], design: Mapping[str, Any], *,
                      requirement_variant: str, design_variant: str,
                      profile: str, existing_files: set[str] | None = None) -> list[SchemaIssue]:
    """Validate both documents and the v13 cross-document checks."""
    issues = validate_requirements(requirements, variant=requirement_variant, profile=profile)
    req_meta: dict[str, Mapping[str, Any]] = {}
    primary_acceptance: set[str] = set()
    for req in requirements.get("requirements", []) if isinstance(requirements, Mapping) else []:
        if isinstance(req, Mapping):
            for row in req.get("acceptance", []) if isinstance(req.get("acceptance"), list) else []:
                if isinstance(row, Mapping) and isinstance(row.get("id"), str):
                    req_meta[row["id"]] = row
                    primary_acceptance.add(row["id"])
    unchanged: set[str] = set()
    if profile == "bugfix" and isinstance(requirements.get("bugfix"), Mapping):
        for group in ("current", "expected", "unchanged"):
            for row in requirements["bugfix"].get(group, []):
                if isinstance(row, Mapping) and isinstance(row.get("id"), str):
                    if group == "unchanged":
                        unchanged.add(row["id"])
                    req_meta[row["id"]] = row
    issues += validate_design_spec(design, variant=design_variant,
                                   requirement_ids=set(req_meta),
                                   existing_files=existing_files)
    if not isinstance(design, Mapping):
        return issues
    task_refs = {ref for task in design.get("tasks", []) if isinstance(task, Mapping)
                 for ref in (task.get("requirements") or []) if isinstance(ref, str)}
    exits = [row for row in design.get("exit_criteria", []) if isinstance(row, Mapping)]
    exit_refs = {ref for row in exits for ref in (row.get("covers") or []) if isinstance(ref, str)}
    for req_id in sorted(primary_acceptance):
        if req_id not in task_refs:
            _issue(issues, "DS-1", "tasks", f"{req_id} is not referenced by a task")
        if req_id not in exit_refs:
            _issue(issues, "DS-1", "exit_criteria", f"{req_id} is not covered by an exit")
    executable = {req_id for req_id, row in req_meta.items()
                  if row.get("negative") is True or row.get("universal") is True}
    for req_id in sorted(executable):
        if not any(req_id in (row.get("covers") or []) and row.get("kind") in ("test", "probe")
                   for row in exits):
            _issue(issues, "DS-9a", "exit_criteria",
                   f"executable acceptance {req_id} needs a test or probe exit")
    for unchanged_id in sorted(unchanged):
        if not any(unchanged_id in (row.get("covers") or []) and row.get("kind") == "test"
                   for row in exits):
            _issue(issues, "DS-4a", "exit_criteria",
                   f"unchanged behavior {unchanged_id} needs a regression test")
    if design_variant == "full" and isinstance(design.get("design"), Mapping):
        flow = design["design"].get("flow")
        if isinstance(flow, str) and not _has_explicit_failure_edge(flow):
            _issue(issues, "DS-6", "design.flow",
                   "must include an explicit failure-condition -> outcome edge")
    return issues
