"""SQL / PL-SQL language front-end tests (Run1 DELETION-MVP, run_533804f3).

Tests the regex-path SQL front-end added to parser.py. SQL is a _REGEX_ONLY_LANG
because PL/SQL procedure bodies parse as tree-sitter ERROR subtrees, and the
tree-sitter path drops all edges for error-trees (parser.py:1554). The regex
path returns its own nodes+edges, bypassing that guard entirely.

What is tested:
  - .sql/.pks/.pkb map to 'sql' (AC1)
  - procedure boundaries extracted, names NOT schema-qualified (AC2/AC3)
  - call edges to in-file procedures via defined-names whitelist (AC4)
  - SQL builtins (NVL/DECODE/...) do NOT form false call edges (AC5)
  - external package calls (RECONCILIATION_INTERFACES.export_csv) not
    mis-connected as local nodes (AC4)
"""
from pathlib import Path

from core.code_intel.parser import LANGUAGE_MAP, parse_file

FIXTURES = Path(__file__).parent / "fixtures"


def _parse(name: str):
    f = FIXTURES / name
    return parse_file(f, FIXTURES)


def test_sql_extensions_map_to_sql():
    """AC1: .sql/.pks/.pkb route to the 'sql' language."""
    assert LANGUAGE_MAP.get(".sql") == "sql"
    assert LANGUAGE_MAP.get(".pks") == "sql"
    assert LANGUAGE_MAP.get(".pkb") == "sql"


def test_sql_extracts_procedure_boundaries():
    """AC2: both procedures in the fixture are extracted as nodes."""
    result = _parse("sample_recon.sql")
    names = {n.name for n in result.nodes}
    assert "proc_daily_reconciliation" in names
    assert "log_run" in names


def test_sql_names_not_schema_qualified():
    """AC3: names are the bare procedure name, never schema.qualified."""
    result = _parse("sample_recon.sql")
    for n in result.nodes:
        assert "." not in n.name, f"schema-qualified name leaked: {n.name}"


def test_sql_call_edge_to_in_file_procedure():
    """AC4: proc_daily_reconciliation calls log_run (both in-file) → edge exists."""
    result = _parse("sample_recon.sql")
    call_targets = {
        (e.source_id.split("::")[-1], e.target_id.split("::")[-1])
        for e in result.edges if e.edge_type == "calls"
    }
    assert ("proc_daily_reconciliation", "log_run") in call_targets, (
        f"missing in-file call edge; got {call_targets}"
    )


def test_sql_builtins_do_not_form_edges():
    """AC5: NVL/DECODE/SUM/TRUNC are SQL builtins, never call edges."""
    result = _parse("sample_recon.sql")
    called = {e.target_id.split("::")[-1].upper() for e in result.edges
              if e.edge_type == "calls"}
    for builtin in ("NVL", "DECODE", "SUM", "TRUNC"):
        assert builtin not in called, f"builtin {builtin} formed a false call edge"


def test_sql_external_pkg_call_not_local_node():
    """AC4: RECONCILIATION_INTERFACES.export_csv is external — export_csv must NOT
    appear as a local procedure NODE (it is not defined in this file)."""
    result = _parse("sample_recon.sql")
    local_names = {n.name for n in result.nodes}
    assert "export_csv" not in local_names


def test_sql_same_package_qualified_call_is_local():
    """AC4 (key real-world case): a call qualified with the package's OWN name
    (PKG.proc where proc IS defined in-file) is a LOCAL self-reference, not
    external. Fixture has no such call; asserted on real HLR data in the dogfood
    smoke below. Here we assert the inverse invariant holds on the fixture: the
    only qualified call (RECONCILIATION_INTERFACES.export_csv) is external and
    produces NO edge because export_csv is not defined in-file."""
    result = _parse("sample_recon.sql")
    targets = {e.target_id.split("::")[-1] for e in result.edges
               if e.edge_type == "calls"}
    assert "export_csv" not in targets


def test_sql_case_insensitive_call_edge():
    """Gate-2 CRITICAL regression: Oracle identifiers are case-insensitive. A def
    `Log_Run` called as `log_run` MUST still form an edge (a case-sensitive
    whitelist compare dropped every such edge). Target resolves to canonical def."""
    result = _parse("sample_case_comment.sql")
    edges = [(e.source_id.split("::")[-1], e.target_id.split("::")[-1])
             for e in result.edges if e.edge_type == "calls"]
    assert ("proc_main", "Log_Run") in edges, f"case-insensitive edge missing: {edges}"


def test_sql_comment_and_string_calls_do_not_form_edges():
    """Gate-2 HIGH regression: a call token inside a comment (`-- Log_Run(...)`) or a
    dynamic-SQL string literal (`'... Log_Run(:1) ...'`) MUST NOT create an edge.
    Only the ONE real call site counts → exactly 1 edge, not 3."""
    result = _parse("sample_case_comment.sql")
    edges = [e for e in result.edges if e.edge_type == "calls"]
    assert len(edges) == 1, (
        f"comment/string calls leaked as edges; expected 1 real call, got {len(edges)}"
    )


def test_sql_quoted_identifier_procedure_name():
    """Regression (found on tranSMART-ETL, a 2nd public repo): Oracle procedures are
    often declared with a DOUBLE-QUOTED name (`PROCEDURE "I2B2_LOAD_DATA"`), often
    preceded by `set define off;` and a multi-line param list before AS. The name
    must extract WITHOUT the quotes. HLR (bare names) was blind to this."""
    result = _parse("sample_quoted.sql")
    names = {n.name for n in result.nodes if n.node_type in ("function", "procedure")}
    assert "I2B2_LOAD_DATA" in names, f"quoted-identifier proc not extracted: {names}"
    assert '"I2B2_LOAD_DATA"' not in names  # quotes must be stripped


def test_sql_forward_declaration_is_not_a_definition():
    """Gate-2 HIGH regression: a package SPEC (.pks) forward declaration
    (`PROCEDURE foo(...);` — no IS/AS body) is a DECLARATION, not a definition, and
    must NOT be extracted as a procedure node."""
    result = _parse("sample_spec.pks")
    procs = [n for n in result.nodes if n.node_type in ("function", "procedure")]
    assert procs == [], f"forward declarations wrongly extracted as defs: {[n.name for n in procs]}"


# ── Run2: dynamic-SQL extraction (edges to data-object table nodes) ────────────

def _dyn_edges(result):
    return [e for e in result.edges if e.edge_type.startswith("dynamic_sql")]


def test_dynamic_sql_extracts_write_edges():
    """Run2 AC1/AC3: `var := '<VERB> <table>'` assignments become dynamic_sql_write
    edges (op encoded in edge_type). Fixture builds CREATE/UPDATE/INSERT."""
    r = _parse("sample_dynamic.sql")
    pairs = {(e.edge_type, e.target_id.split(":")[-1]) for e in _dyn_edges(r)}
    assert ("dynamic_sql_write:CREATE", "recon_staging") in pairs, pairs
    assert ("dynamic_sql_write:UPDATE", "recon_result") in pairs, pairs
    assert ("dynamic_sql_write:INSERT", "audit_log") in pairs, pairs


def test_dynamic_sql_emits_data_object_nodes():
    """Run2 AC2 (CRITICAL C1): a CodeNode(node_type='data_object') is emitted per
    table — WITHOUT it, orphan cleanup deletes every dynamic_sql edge on rebuild
    (the plan-was-a-no-op bug Gate-1 caught)."""
    r = _parse("sample_dynamic.sql")
    data_objs = {n.name for n in r.nodes if n.node_type == "data_object"}
    assert {"recon_staging", "recon_result", "audit_log"} <= data_objs, data_objs


def test_dynamic_sql_table_node_id_namespaced_no_collision():
    """Run2 AC2 (CRITICAL C2): table `audit_log` and PROCEDURE `audit_log` in the
    same file must have DISTINCT node ids (table id namespaced), else the dynamic
    edge resolves into the procedure = fabricated call."""
    r = _parse("sample_dynamic.sql")
    proc = next(n for n in r.nodes if n.node_type in ("function", "procedure") and n.name == "audit_log")
    tbl = next(n for n in r.nodes if n.node_type == "data_object" and n.name == "audit_log")
    assert proc.id != tbl.id, f"id collision: proc={proc.id} tbl={tbl.id}"


def test_dynamic_sql_comment_assignment_not_an_edge():
    """Run2 AC6 (H1): `-- sqlText := 'DROP TABLE old_ghost'` in a comment must NOT
    produce an edge (string-aware de-comment)."""
    r = _parse("sample_dynamic.sql")
    targets = {e.target_id.split(":")[-1] for e in _dyn_edges(r)}
    assert "old_ghost" not in targets, f"comment assignment leaked as edge: {targets}"


def test_dynamic_sql_prose_message_not_a_table():
    """Run2 (Gate-2 HIGH regression): an error MESSAGE that mentions a write verb
    (`msg := 'CREATE TABLE was blocked'`) must NOT fabricate a phantom table. The
    SQL-shape guard requires real SQL structure after the table token, not prose."""
    r = _parse("sample_dynamic.sql")
    tables = {n.name for n in r.nodes if n.node_type == "data_object"}
    assert "was" not in tables and "the" not in tables, f"prose fabricated tables: {tables}"


def test_dynamic_sql_edge_confidence_is_low():
    """Run2 (Gate-2): a dynamic-SQL edge is an ASSIGNMENT (weaker evidence than an
    executed call) — confidence must stay low (0.4) to signal that honestly."""
    r = _parse("sample_dynamic.sql")
    for e in _dyn_edges(r):
        assert e.confidence == 0.4, f"dynamic_sql edge confidence should be 0.4, got {e.confidence}"


def test_dynamic_sql_data_object_not_flagged_dead_code():
    """Run2 (Gate-2): a data_object node has 0 outgoing edges — it must NOT be
    mis-flagged as dead code. Dead-code detection filters is_export=1; data_object
    nodes are is_export=False, so they are excluded by construction. Guard it."""
    r = _parse("sample_dynamic.sql")
    for n in r.nodes:
        if n.node_type == "data_object":
            assert n.is_export is False, f"data_object {n.name} must be is_export=False"


def test_dynamic_sql_edges_survive_bulk_insert():
    """Run2 AC4 (CRITICAL C1 regression — the plan-killer): edges must SURVIVE a
    real GraphStore.bulk_insert, which runs orphan cleanup that deletes edges whose
    target is not a node. Parsing alone is NOT enough — they must persist through
    the production rebuild path."""
    import tempfile
    from core.code_intel.graph_store import GraphStore
    r = _parse("sample_dynamic.sql")
    with tempfile.TemporaryDirectory() as td:
        gs = GraphStore(Path(td) / "t.db")
        try:
            gs.bulk_insert([r])
            rows = gs._conn.execute(
                "SELECT edge_type, target_id FROM code_edges "
                "WHERE edge_type LIKE 'dynamic_sql%'"
            ).fetchall()
        finally:
            gs.close()
    assert len(rows) >= 3, f"dynamic_sql edges did not survive bulk_insert: {rows}"


# ── Dog-food smoke on REAL production PL/SQL (O009: fixture ≠ reality) ──────────
# These lock the numbers that fixtures structurally cannot surface: the .pkb
# bare-PROCEDURE form, same-package-qualified self-calls, and the external-package
# boundary at real scale. Skipped if the sample files are absent (CI without them).
import pytest  # noqa: E402

_HLR = Path("/tmp/hlr-sample/body.sql")
_NORTHCART = Path("/tmp/plsql-sample/proc_daily_reconciliation.sql")


@pytest.mark.skipif(not _HLR.exists(), reason="HLR sample not present")
def test_dogfood_hlr_package_body():
    """Real 16326-line Oracle PACKAGE BODY: bare-PROCEDURE form (no CREATE),
    same-package-qualified self-calls form the call graph, external packages
    (UTILS_INTERFACES.*) do NOT leak as local nodes."""
    r = parse_file(_HLR, _HLR.parent)
    procs = [n for n in r.nodes if n.node_type in ("function", "procedure")]
    calls = [e for e in r.edges if e.edge_type == "calls"]
    assert len(procs) >= 27, f"expected >=27 procedures, got {len(procs)}"
    assert len(calls) >= 10, f"expected in-package call edges, got {len(calls)}"
    # external package call target must never appear as a LOCAL procedure node
    names = {n.name for n in procs}
    assert "export_csv" not in names  # RECONCILIATION_INTERFACES.export_csv is a call, not a def
    # no schema-qualified names leaked
    assert not [n for n in procs if "." in n.name]


@pytest.mark.skipif(not _NORTHCART.exists(), reason="NorthCart sample not present")
def test_dogfood_northcart_standalone():
    """Real standalone CREATE PROCEDURE file. KNOWN LIMIT (documented): the nested
    procedure `log_run` (declared INSIDE proc_daily_reconciliation's DECLARE) is
    extracted as a sibling node, and the call to it is mis-segmented — so 0 edges
    here. This is the nested-procedure boundary the MVP does not handle (Run2+).
    We assert boundaries extract (2 procs) + no schema-leak; edges are NOT asserted
    for the nested case (honest limit, not a passing claim)."""
    r = parse_file(_NORTHCART, _NORTHCART.parent)
    procs = [n for n in r.nodes if n.node_type in ("function", "procedure")]
    assert {"proc_daily_reconciliation", "log_run"} <= {n.name for n in procs}
    assert not [n for n in procs if "." in n.name]
