"""SwarmAI repo's own wording / frontend-style lint, scripts/lint_terms.py (P7, S4b-D).

The rules moved from the removed product hooks (inclusive_term_guard, design_lint_guard);
these are the same flag / exemption cases those hooks were tested with, plus the diff mode
that scans only added lines and the exit codes. The product no longer registers either hook.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("lint_terms", REPO / "scripts" / "lint_terms.py")
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)

W, B = "white" + "list", "black" + "list"   # keep this file clean for the linter itself
M, S = "mas" + "ter", "sla" + "ve"


def test_inclusive_flags():
    for text in (f"a {W}", f"the {W}ed section", f"_{W.upper()} = []", f"get{W.title()}()",
                 f"{M}/{S} replication", f"the {S} node", f"add to {B}"):
        assert lt.scan_inclusive_terms(text), text
    assert set(lt.scan_inclusive_terms("whi" + "teday and bla" + "ckday")) == {
        "whiteday", "blackday"}


def test_inclusive_exemptions():
    for text in (f"st.characters({W}_categories=('L','N'))", f"{W}_characters=' -_'",
                 f"from an implicit {W} to a {B} model", "inclusive of both endpoints",
                 f"git checkout {M}  # the {M} copy", "en" + S + "d for centuries"):
        assert lt.scan_inclusive_terms(text) == [], text


STYLE = "desktop/src/components/X.tsx"


def test_design_flags():
    for text, rule in ((".t { color: #000; }", "pure-black-text"),
                       ("<div style={{color: 'black'}}/>", "pure-black-text"),
                       (".x { transition: transform 200ms ease-elastic; }", "bounce-easing"),
                       (".x { animation-timing-function: cubic-bezier(0.5, 0, 0.5, 1.6); }",
                        "bounce-easing"),
                       ('<div className="text-gray-400 bg-blue-600"/>', "gray-on-color")):
        assert rule in lt.scan_design_rules(STYLE, text), text
    assert "pure-black-text" in lt.scan_design_rules("hive/x.css", ".t { color: black; }")


def test_design_no_false_positives():
    for text in (".t { color: #1a1a2e; } .b { background: #000; }",
                 ".x { background-color: #000; border-color: black; }",
                 "<div style={{backgroundColor: '#000'}}/>", "<div style={{color: '#333'}}/>",
                 ".x { transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }",
                 '<div className="text-gray-400 bg-white"/>'):
        assert lt.scan_design_rules(STYLE, text) == [], text


def test_design_scope():
    assert lt.scan_design_rules("backend/core/foo.py", 'x = "color: #000"') == []
    assert lt.scan_design_rules("Knowledge/note.md", "color: black") == []
    assert lt.scan_design_rules("scratch/Thing.tsx", ".t { color: #000; }") == []
    assert lt.scan_design_rules("desktop/src/foo.ts", "color: #000") == []


def test_diff_mode_scans_only_added_lines():
    diff = ("diff --git a/x.md b/x.md\n--- a/x.md\n+++ b/x.md\n@@ -1 +1 @@\n"
            f"-old {W} line\n+new clean line\n"
            "--- a/desktop/src/A.tsx\n+++ b/desktop/src/A.tsx\n@@ -0,0 +1 @@\n+.t { color: #000; }\n"
            "--- a/gone.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n")
    added = lt._added_text(diff)
    assert added == {"x.md": "new clean line", "desktop/src/A.tsx": ".t { color: #000; }"}


def test_cli_exit_codes(tmp_path):
    bad = tmp_path / "a.md"
    bad.write_text(f"the {S} node\n")
    good = tmp_path / "b.md"
    good.write_text("clean\n")
    run = lambda *a: subprocess.run([sys.executable, str(REPO / "scripts" / "lint_terms.py"), *a],
                                    capture_output=True, text=True, timeout=30)
    r = run(str(bad))
    assert r.returncode == 1 and "replica" in r.stdout
    assert run("--warn", str(bad)).returncode == 0
    assert run(str(good)).returncode == 0


def test_the_product_no_longer_registers_the_hooks():
    hb = (REPO / "backend" / "core" / "hook_builder.py").read_text()
    rh = (REPO / "backend" / "core" / "runtime_hooks.py").read_text()
    # a registration names the hook as a quoted string; the explanatory comments do not
    assert '"inclusive_term_guard"' not in hb and '"design_lint_guard"' not in hb
    assert '"change_triggered_eval"' not in rh
    assert not (REPO / "backend" / "core" / "design_hooks.py").exists()


def test_ci_runs_the_lint():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "scripts/lint_terms.py" in ci
