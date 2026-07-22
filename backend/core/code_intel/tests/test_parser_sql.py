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
