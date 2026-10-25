"""Pipeline v1 document-schema tests (v13 §B1-§B2, §D4).

Methodology: valid full/lite fixtures with one-field mutations. Tests prove EARS is
validated from structured fields rather than language, unknown legacy self-claims fail,
bugfix triads are explicit, and design IDs/references are resolvable.
"""
from __future__ import annotations

import copy

import pytest

from core.run_protocol import schemas as sc


def ac(ac_id="R1.1", pattern="event", condition="a request arrives",
       response="return 200", negative=False, universal=False):
    return {"id": ac_id, "pattern": pattern, "condition": condition,
            "response": response, "negative": negative, "universal": universal}


def full_requirements(profile="full"):
    doc = {
        "requirements": [{"id": "R1", "story": "As a user", "acceptance": [ac()]}],
        "analysis": [{"kind": "missing_edge", "refs": ["R1.1"], "note": "empty input",
                      "resolution": "rewritten"}],
        "clarifications": [{"qid": "Q1", "question": "Which port?", "answer": "8000",
                            "answered_by": "evidence"}],
        "understanding": {"claim": "serve locally", "readings": ["one reading"],
                          "evidence": ["existing server"], "root_cause": None},
        "prior_knowledge": [{"entry": "local only", "store": "project"}],
    }
    if profile == "bugfix":
        doc["bugfix"] = {
            "current": [ac("C1", response="returns 500")],
            "expected": [ac("E1", response="returns 200")],
            "unchanged": [ac("U1", pattern="ubiquitous", condition="",
                             response="other routes stay unchanged")],
        }
    return doc


def lite_requirements():
    return {"requirements": [{"id": "R1", "acceptance": [ac()]}]}


def full_design():
    return {
        "alternatives": [{"what": "patch"}, {"what": "replace"}],
        "chosen": "replace",
        "design": {"user_workflow": "send request", "architecture": "router to service",
                   "flow": "request -> validate; invalid -> error; valid -> response",
                   "states": [{"name": "open", "created_by": "request",
                               "removed_by": "response"}]},
        "properties": [{"id": "P1", "statement": "all requests finish",
                        "validates": ["R1.1"], "test": "tests/test_x.py::test_p",
                        "kind": "pbt"}],
        "failure_modes": [{"id": "F1", "scenario": "empty", "expected": "400",
                           "test": "tests/test_x.py::test_empty"}],
        "tasks": [{"id": "T1", "file": "backend/core/x.py", "symbol": "run",
                   "change": "handle request", "requirements": ["R1.1"],
                   "depends_on": [], "creates_test_files": []}],
        "exit_criteria": [{"id": "X1", "kind": "test",
                           "ref": "tests/test_x.py::test_p", "covers": ["R1.1", "P1"]}],
        "consumers": [{"id": "K1", "contract": "return status", "pattern": "run(",
                       "paths": ["backend"], "readers": ["backend/router.py"]}],
        "security_boundaries": ["localhost only"],
    }


def lite_design():
    return {
        "tasks": [{"id": "T1", "file": "backend/x.py", "symbol": "f", "change": "fix",
                   "requirements": ["R1.1"], "depends_on": [], "creates_test_files": []}],
        "exit_criteria": [{"id": "X1", "kind": "test", "ref": "tests/x.py::test_f",
                           "covers": ["R1.1"]}],
        "no_contract_change_reason": "private implementation only",
    }


def paths(issues):
    return {issue.path for issue in issues}


def ids(issues):
    return {issue.check_id for issue in issues}


@pytest.mark.parametrize("profile", ["full", "trivial", "research"])
def test_valid_full_requirements(profile):
    assert sc.validate_requirements(full_requirements(profile), variant="full",
                                    profile=profile) == []


def test_valid_bugfix_and_direct_lite_requirements():
    assert sc.validate_requirements(full_requirements("bugfix"), variant="full",
                                    profile="bugfix") == []
    assert sc.validate_requirements(lite_requirements(), variant="lite", profile="direct") == []


def test_ears_is_structured_and_language_independent():
    doc = full_requirements()
    doc["requirements"][0]["acceptance"][0] = {
        "id": "R1.1", "pattern": "state", "condition": "当服务正在启动时",
        "response": "系统不得接受外部连接", "negative": True, "universal": True,
        "text": "中文原话不需要长得像英文 EARS",
    }
    assert sc.validate_requirements(doc, variant="full", profile="full") == []


@pytest.mark.parametrize("mutation,path", [
    (lambda d: d["requirements"][0]["acceptance"][0].update(pattern="sometimes"),
     "requirements[0].acceptance[0].pattern"),
    (lambda d: d["requirements"][0]["acceptance"][0].update(condition=""),
     "requirements[0].acceptance[0].condition"),
    (lambda d: d["requirements"][0]["acceptance"][0].update(response=""),
     "requirements[0].acceptance[0].response"),
    (lambda d: d["requirements"][0]["acceptance"][0].update(negative="yes"),
     "requirements[0].acceptance[0].negative"),
])
def test_invalid_ears_fields(mutation, path):
    doc = full_requirements()
    mutation(doc)
    assert path in paths(sc.validate_requirements(doc, variant="full", profile="full"))


def test_ubiquitous_rejects_a_condition_and_other_patterns_require_one():
    doc = full_requirements()
    row = doc["requirements"][0]["acceptance"][0]
    row.update(pattern="ubiquitous", condition="always")
    assert "requirements[0].acceptance[0].condition" in paths(
        sc.validate_requirements(doc, variant="full", profile="full"))


def test_lite_forbids_full_fields_and_unknown_self_claims():
    doc = lite_requirements()
    doc["analysis"] = []
    doc["all_green"] = True
    issues = sc.validate_requirements(doc, variant="lite", profile="direct")
    assert {"$.analysis", "$.all_green"} <= paths(issues)


def test_requirement_and_acceptance_ids_are_unique_and_well_formed():
    doc = full_requirements()
    doc["requirements"].append(copy.deepcopy(doc["requirements"][0]))
    doc["requirements"][1]["acceptance"][0]["id"] = "R0.1"
    issues = sc.validate_requirements(doc, variant="full", profile="full")
    assert "RQ-1" in ids(issues)
    assert any("duplicate id R1" in issue.message for issue in issues)
    assert "requirements[1].acceptance[0].id" in paths(issues)


def test_bugfix_requires_all_three_nonempty_groups_and_the_right_prefixes():
    doc = full_requirements("bugfix")
    doc["bugfix"]["unchanged"] = []
    doc["bugfix"]["current"][0]["id"] = "E2"
    issues = sc.validate_requirements(doc, variant="full", profile="bugfix")
    assert "RQ-4" in ids(issues)
    assert "bugfix.unchanged" in paths(issues)
    assert "bugfix.current[0].id" in paths(issues)


def test_non_bugfix_profile_rejects_a_bugfix_section():
    doc = full_requirements()
    doc["bugfix"] = full_requirements("bugfix")["bugfix"]
    issues = sc.validate_requirements(doc, variant="full", profile="full")
    assert any(issue.check_id == "RQ-4" and issue.path == "bugfix" for issue in issues)


def test_acceptance_ids_are_global_and_belong_to_the_parent_requirement():
    doc = full_requirements()
    second = {"id": "R2", "story": "second", "acceptance": [ac("R1.1")]}
    doc["requirements"].append(second)
    issues = sc.validate_requirements(doc, variant="full", profile="full")
    assert any(issue.path == "requirements.acceptance" and "duplicate" in issue.message
               for issue in issues)
    assert any(issue.path == "requirements[1].acceptance[0].id" and "belong to R2" in issue.message
               for issue in issues)


def test_full_requires_resolved_analysis_answered_clarifications_and_understanding():
    doc = full_requirements()
    doc["analysis"][0]["resolution"] = "pending"
    doc["clarifications"][0]["answer"] = ""
    doc["understanding"]["claim"] = ""
    issues = sc.validate_requirements(doc, variant="full", profile="full")
    assert {"RQ-5", "RQ-6"} <= ids(issues)
    assert {"analysis[0].resolution", "clarifications[0].answer", "understanding.claim"} \
        <= paths(issues)


def test_unknown_requirements_variant_is_rejected():
    issues = sc.validate_requirements({}, variant="v2", profile="full")
    assert issues == [sc.SchemaIssue("RQ-1", "$", "unknown requirements variant 'v2'")]


def test_valid_full_and_lite_design_specs():
    assert sc.validate_design_spec(full_design(), variant="full",
                                   requirement_ids={"R1.1"}) == []
    assert sc.validate_design_spec(lite_design(), variant="lite",
                                   requirement_ids={"R1.1"}) == []


def test_lite_rejects_full_only_sections_and_legacy_self_claims():
    doc = lite_design()
    doc.update(alternatives=[], chosen="x", design={}, tests_pass=True)
    issues = sc.validate_design_spec(doc, variant="lite", requirement_ids={"R1.1"})
    assert {"$.alternatives", "$.chosen", "$.design", "$.tests_pass"} <= paths(issues)


@pytest.mark.parametrize("key", ["tasks", "exit_criteria"])
def test_tasks_and_exit_criteria_are_mandatory_nonempty(key):
    doc = lite_design()
    doc[key] = []
    issues = sc.validate_design_spec(doc, variant="lite", requirement_ids={"R1.1"})
    assert key in paths(issues)


def test_consumers_xor_reason_is_required():
    doc = lite_design()
    del doc["no_contract_change_reason"]
    assert "DS-7" in ids(sc.validate_design_spec(doc, variant="lite",
                                                requirement_ids={"R1.1"}))
    doc["consumers"] = full_design()["consumers"]
    doc["no_contract_change_reason"] = "also supplied"
    issues = sc.validate_design_spec(doc, variant="lite", requirement_ids={"R1.1"})
    assert any("not both" in issue.message for issue in issues)


def test_every_task_and_exit_reference_must_resolve():
    doc = full_design()
    doc["tasks"][0]["requirements"] = ["R9.9"]
    doc["tasks"][0]["depends_on"] = ["T9"]
    doc["exit_criteria"][0]["covers"] = ["P9"]
    issues = sc.validate_design_spec(doc, variant="full", requirement_ids={"R1.1"})
    assert "DS-5" in ids(issues)
    assert {"tasks[0].requirements[0]", "tasks[0].depends_on[0]",
            "exit_criteria[0].covers[0]"} <= paths(issues)


def test_property_failure_task_and_exit_ids_are_strict_and_unique():
    doc = full_design()
    doc["properties"].append(copy.deepcopy(doc["properties"][0]))
    doc["failure_modes"][0]["id"] = "bad"
    doc["tasks"][0]["id"] = "T0"
    doc["exit_criteria"][0]["id"] = "X0"
    issues = sc.validate_design_spec(doc, variant="full", requirement_ids={"R1.1"})
    assert {"properties", "failure_modes[0].id", "tasks[0].id",
            "exit_criteria[0].id"} <= paths(issues)


def test_full_design_requires_alternatives_chosen_flow_and_state_lifecycle():
    doc = full_design()
    doc["alternatives"] = []
    doc["chosen"] = ""
    doc["design"]["flow"] = ""
    del doc["design"]["states"][0]["removed_by"]
    issues = sc.validate_design_spec(doc, variant="full", requirement_ids={"R1.1"})
    assert {"alternatives", "chosen", "design.flow", "design.states[0].removed_by"} \
        <= paths(issues)


def test_a_probe_exit_is_unknown_and_covers_must_resolve():
    doc = lite_design()
    doc["exit_criteria"][0] = {"id": "X1", "kind": "probe", "covers": ["R9.9"]}
    issues = sc.validate_design_spec(doc, variant="lite", requirement_ids={"R1.1"})
    assert {"exit_criteria[0].kind", "exit_criteria[0].covers[0]"} <= paths(issues)


def test_optional_lite_properties_are_validated_when_present():
    doc = lite_design()
    doc["properties"] = [{"id": "P1", "statement": "p", "validates": ["R1.1"],
                          "test": "tests/x.py::test_p", "kind": ""}]
    issues = sc.validate_design_spec(doc, variant="lite", requirement_ids={"R1.1"})
    # S6-4c: kind is optional free text, but empty text is refused
    assert "properties[0].kind" in paths(issues)


def test_unknown_design_variant_and_bad_requirement_id_set_are_rejected():
    assert sc.validate_design_spec({}, variant="v2", requirement_ids=set())[0].path == "$"
    issues = sc.validate_design_spec(lite_design(), variant="lite",
                                     requirement_ids={1})
    assert "requirement_ids" in paths(issues)


def test_design_rejects_unsafe_probe_and_traversing_files():
    doc = lite_design()
    doc["tasks"][0]["file"] = "../../outside.py"
    doc["tasks"][0]["creates_test_files"] = ["/tmp/test.py"]
    doc["exit_criteria"][0] = {"id": "X1", "kind": "probe",
                               "argv": ["sh", "-c", "true"],
                               "expect": {"rc": 0, "stdout": "empty"},
                               "covers": ["R1.1"]}
    issues = sc.validate_design_spec(doc, variant="lite", requirement_ids={"R1.1"})
    assert {"tasks[0].file", "tasks[0].creates_test_files[0]", "exit_criteria[0].argv"} \
        <= paths(issues)


def test_full_design_requires_security_boundaries():
    doc = full_design()
    del doc["security_boundaries"]
    assert "security_boundaries" in paths(
        sc.validate_design_spec(doc, variant="full", requirement_ids={"R1.1"}))


def test_contract_checks_requirement_task_exit_and_failure_flow():
    req, design = full_requirements(), full_design()
    design["design"]["flow"] = "request -> validate; invalid -> error; valid -> response"
    assert sc.validate_contract(req, design, requirement_variant="full",
                                design_variant="full", profile="full") == []
    design["tasks"][0]["requirements"] = []
    design["exit_criteria"][0]["covers"] = ["P1"]
    design["design"]["flow"] = "request -> response"
    issues = sc.validate_contract(req, design, requirement_variant="full",
                                  design_variant="full", profile="full")
    assert {"DS-1", "DS-6"} <= ids(issues)


def test_negative_or_universal_acceptance_requires_test_or_probe_exit():
    req, design = lite_requirements(), lite_design()
    req["requirements"][0]["acceptance"][0]["negative"] = True
    design["exit_criteria"][0]["kind"] = "manual"
    issues = sc.validate_contract(req, design, requirement_variant="lite",
                                  design_variant="lite", profile="direct")
    assert any(issue.check_id == "DS-9a" and "executable acceptance" in issue.message
               for issue in issues)


def test_bugfix_unchanged_behavior_requires_regression_test():
    req, design = full_requirements("bugfix"), full_design()
    design["design"]["flow"] = "request -> validate; invalid -> error; valid -> response"
    # Add U1 to a task so DS-1 isolates the missing regression exit.
    design["tasks"][0]["requirements"].append("U1")
    issues = sc.validate_contract(req, design, requirement_variant="full",
                                  design_variant="full", profile="bugfix")
    assert any(issue.check_id == "DS-4a" for issue in issues)


def test_ds2a_test_file_must_exist_or_be_planned_for_creation():
    doc = full_design()
    issues = sc.validate_design_spec(doc, variant="full", requirement_ids={"R1.1"},
                                     existing_files=set())
    assert any(issue.check_id == "DS-2a" and "not planned" in issue.message
               for issue in issues)
    doc["tasks"][0]["creates_test_files"] = ["tests/test_x.py"]
    issues = sc.validate_design_spec(doc, variant="full", requirement_ids={"R1.1"},
                                     existing_files=set())
    assert not [issue for issue in issues if issue.check_id == "DS-2a"
                and "not planned" in issue.message]


def test_ds1_does_not_require_bugfix_c_e_u_ids_as_primary_requirements():
    req, design = full_requirements("bugfix"), full_design()
    design["exit_criteria"].append({"id": "X2", "kind": "test",
        "ref": "tests/test_x.py::test_unchanged", "covers": ["U1"]})
    issues = sc.validate_contract(req, design, requirement_variant="full",
                                  design_variant="full", profile="bugfix")
    ds1 = [issue.message for issue in issues if issue.check_id == "DS-1"]
    assert not any(identifier in message for identifier in ("C1", "E1", "U1")
                   for message in ds1)


def test_ds6_does_not_treat_incidental_no_as_a_failure_branch():
    req, design = full_requirements(), full_design()
    design["design"]["flow"] = "request -> response with no delay"
    issues = sc.validate_contract(req, design, requirement_variant="full",
                                  design_variant="full", profile="full")
    assert any(issue.check_id == "DS-6" for issue in issues)
