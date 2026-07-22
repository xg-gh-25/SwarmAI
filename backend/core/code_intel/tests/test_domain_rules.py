"""Per-domain business-rule manifest tests (run_28a8f99d, language-agnostic).

domain_rules.compute_domain_rules reads the shared graph (graph_clusters
extraction_candidates + dynamic_sql_write edges) and, per bounded domain, emits
machine-readable business rules with graph-derived anchors, a disposition joined
from recon-call literal args BY TABLE-NAME EQUALITY (Gate-1 C1: NOT proximity),
and an ADVISORY engine_status that NEVER suppresses a rule (Gate-1 H1: false-100%
banking red line). verify_traceability enforces every rule_id appears in the
spec's behavioral chapters §1-8, not only the §9 matrix (fail-closed).

Tests are mutation-oriented: each asserts a behavior that a plausible wrong
implementation would fail.
"""
from core.code_intel.domain_rules import compute_domain_rules, verify_traceability


class _FakeGraph:
    """Minimal stand-in exposing get_full_graph() + count_edges()."""
    def __init__(self, nodes, edges):
        self._nodes, self._edges = nodes, edges

    def get_full_graph(self):
        return {"nodes": self._nodes, "edges": self._edges}

    def count_edges(self):
        return len(self._edges)


def _node(nid, lang="sql", ntype="function", name=None):
    return {"id": nid, "file_path": nid.split("::")[0], "node_type": ntype,
            "name": name or nid.split("::")[-1], "language": lang,
            "is_export": 1, "is_entry_point": 0}


def _edge(s, t, etype="calls", conf=1.0, line=1):
    return {"source_id": s, "target_id": t, "edge_type": etype,
            "confidence": conf, "line_number": line}


def _recon_domain_graph():
    """A realistic recon community, dense enough to clear the extraction_candidate
    bar (size>=3, cohesion>=0.5). The CURRENT engine RUN_RECON writes the two
    report tables + shares staging tables that bind the whole domain into ONE
    cluster; the DEPRECATED RUN_RECON_OLD writes a strict SUBSET of RUN_RECON's
    targets (so it is detectable as superseded, and lands in the same community
    via the shared targets)."""
    nodes = [
        _node("body.sql::RUN_RECON"),
        _node("body.sql::RUN_RECON_OLD"),
        _node("body.sql::table:REP_SV_NOT_HLR", ntype="data_object", name="REP_SV_NOT_HLR"),
        _node("body.sql::table:REP_HLR_NOT_SV", ntype="data_object", name="REP_HLR_NOT_SV"),
        _node("body.sql::table:CLEAN_MERGED", ntype="data_object", name="CLEAN_MERGED"),
        _node("body.sql::table:MAIN_SYS", ntype="data_object", name="MAIN_SYS"),
    ]
    W = "dynamic_sql_write:CREATE"
    edges = [
        # current engine builds staging + both report tables → binds the community
        _edge("body.sql::RUN_RECON", "body.sql::table:MAIN_SYS", etype=W, conf=0.4, line=80),
        _edge("body.sql::RUN_RECON", "body.sql::table:CLEAN_MERGED", etype=W, conf=0.4, line=90),
        _edge("body.sql::RUN_RECON", "body.sql::table:REP_SV_NOT_HLR", etype=W, conf=0.4, line=100),
        _edge("body.sql::RUN_RECON", "body.sql::table:REP_HLR_NOT_SV", etype=W, conf=0.4, line=120),
        # deprecated engine writes a STRICT SUBSET of RUN_RECON's targets (overlap →
        # same community; subset → suspected_deprecated)
        _edge("body.sql::RUN_RECON_OLD", "body.sql::table:MAIN_SYS", etype=W, conf=0.4, line=30),
        _edge("body.sql::RUN_RECON_OLD", "body.sql::table:REP_SV_NOT_HLR", etype=W, conf=0.4, line=40),
    ]
    return _FakeGraph(nodes, edges)


# Source text a recon domain's file would contain — recon calls carry the
# disposition as the 3rd literal arg, table as the 2nd (the real HLR shape).
_RECON_SOURCE = """\
PROCEDURE RUN_RECON IS BEGIN
  SQL_TXT := 'CREATE TABLE REP_SV_NOT_HLR AS SELECT * FROM sv WHERE hlr IS NULL';
  RECONCILIATION_INTERFACES.prov_recon_services_cps('FAFIF','REP_SV_NOT_HLR','CREATE_ACTIVATE','SMS','1');
  SQL_TXT := 'CREATE TABLE REP_HLR_NOT_SV AS SELECT * FROM hlr WHERE sv IS NULL';
  RECONCILIATION_INTERFACES.prov_recon_services_cps('FAFIF','REP_HLR_NOT_SV','DELETE','SMS','1');
END;
"""


def test_manifest_emits_rules_per_domain_with_anchor():
    """AC1: each rule carries source_symbol, operation, target, graph-derived anchor."""
    r = compute_domain_rules(_recon_domain_graph(), source_reader=lambda f: _RECON_SOURCE)
    assert r["rules"], "no rules emitted"
    # the CURRENT engine's write of REP_SV_NOT_HLR anchors at its edge line (100)
    rule = next(x for x in r["rules"]
                if x["target_data_object"] == "REP_SV_NOT_HLR"
                and x["source_symbol"] == "RUN_RECON")
    assert rule["operation"] == "CREATE"
    assert rule["anchor"]["file"] == "body.sql"
    assert rule["anchor"]["line"] == 100  # from the edge line_number, not invented
    assert "rule_id" in rule and rule["rule_id"]


def test_disposition_joined_by_table_name_not_proximity():
    """AC1/Gate-1 C1: disposition comes from the recon call whose arg[2] == the
    write target (table-name equality), NOT the physically nearest call."""
    r = compute_domain_rules(_recon_domain_graph(), source_reader=lambda f: _RECON_SOURCE)
    by_tbl = {x["target_data_object"]: x for x in r["rules"]}
    assert by_tbl["REP_SV_NOT_HLR"]["disposition"] == "CREATE_ACTIVATE"
    assert by_tbl["REP_HLR_NOT_SV"]["disposition"] == "DELETE"


def test_engine_status_advisory_never_suppresses():
    """AC2/Gate-1 H1: the deprecated (_OLD / subset) engine's rule is FLAGGED
    suspected_deprecated but STILL present — never dropped (false-100% red line)."""
    r = compute_domain_rules(_recon_domain_graph(), source_reader=lambda f: _RECON_SOURCE)
    old_rules = [x for x in r["rules"] if x["source_symbol"] == "RUN_RECON_OLD"]
    assert old_rules, "deprecated engine rule was SUPPRESSED — violates never-suppress"
    assert old_rules[0]["engine_status"] == "suspected_deprecated"
    cur_rules = [x for x in r["rules"] if x["source_symbol"] == "RUN_RECON"]
    assert all(x["engine_status"] == "current" for x in cur_rules)


def test_disposition_null_when_no_recon_call():
    """AC1: a write with no matching recon call → disposition null, rule still emitted."""
    g = _recon_domain_graph()
    # source with NO recon calls at all
    r = compute_domain_rules(g, source_reader=lambda f: "PROCEDURE RUN_RECON IS BEGIN NULL; END;")
    assert r["rules"], "rules dropped when no disposition — should stay"
    assert all(x["disposition"] is None for x in r["rules"])


def test_verify_traceability_fail_closed_matrix_only():
    """AC4: a rule_id referenced ONLY in the §9 matrix (not §1-8) = uncovered."""
    manifest = {"rules": [{"rule_id": "BR-1"}, {"rule_id": "BR-2"}]}
    spec = """\
## 4. Business Rules
BR-1 — subscribers in SV not in HLR are activated.

## 9. Traceability Matrix
| BR-1 | ... |
| BR-2 | ... |
"""
    v = verify_traceability(spec, manifest)
    assert v["ok"] is False
    assert "BR-2" in v["uncovered_rule_ids"]
    assert "BR-1" not in v["uncovered_rule_ids"]  # BR-1 is in §4 behavioral


def test_verify_traceability_no_matrix_section_fails_closed():
    """AC4/Gate-1 H2: a spec with NO §9/matrix section fails CLOSED, not open."""
    manifest = {"rules": [{"rule_id": "BR-1"}]}
    v = verify_traceability("## 4. Rules\nBR-1 discussed here.", manifest)
    assert v["ok"] is False
    assert v.get("reason")


def test_verify_traceability_word_boundary_no_substring_match():
    """AC4/Gate-1 H2: BR-1 must not be counted 'covered' by BR-12 appearing in prose."""
    manifest = {"rules": [{"rule_id": "BR-1"}]}
    spec = """\
## 4. Business Rules
Only BR-12 is discussed in the behavioral text.

## 9. Traceability Matrix
| BR-1 | ... |
"""
    v = verify_traceability(spec, manifest)
    assert "BR-1" in v["uncovered_rule_ids"], "substring match falsely counted BR-1 covered"


def test_empty_graph_no_crash():
    """AC1: empty graph → empty manifest, no crash."""
    r = compute_domain_rules(_FakeGraph([], []), source_reader=lambda f: "")
    assert r["rules"] == []
    assert r["domains"] == []
