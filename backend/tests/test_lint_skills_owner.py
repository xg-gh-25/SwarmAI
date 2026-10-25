"""lint_skills K3: shipped files never name one specific person.

Closing guards K3 (docs/design/2026-10-25-usable-by-anyone-closed-world-guards.md).
The names live in the repo's own config, scripts/owner_identity.txt; the scope is
every shipped tree (skills, DDD skill templates, experts, context seeds).
"""
import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("lint_skills_k3", _REPO / "scripts" / "lint_skills.py")
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)


def _names(tmp_path, text):
    f = tmp_path / "names.txt"
    f.write_text(text)
    return L.load_owner_names(f)


def _hits(patterns, line):
    return [m.group() for rx in patterns for m in rx.finditer(line)]


def test_all_caps_names_are_case_sensitive_and_whole_word(tmp_path):
    pats = _names(tmp_path, "# comment\nXG\n\n")
    assert _hits(pats, "needs XG approval") == ["XG"]
    assert _hits(pats, "XG's quotes") == ["XG"]
    assert _hits(pats, "github.com/xg-gh-25/SwarmAI") == []      # slug, not the person
    assert _hits(pats, "XGBoost and FXG") == []


def test_other_names_match_any_case(tmp_path):
    pats = _names(tmp_path, "gawan\n")
    assert _hits(pats, "login Gawan, alias GAWAN") == ["Gawan", "GAWAN"]
    assert _hits(pats, "gawanx") == []


def test_missing_names_file_declares_no_names(tmp_path):
    assert L.load_owner_names(tmp_path / "absent.txt") == []


def test_this_repo_declares_its_owner_names():
    loaded = {rx.pattern for rx in L.load_owner_names()}
    assert len(loaded) >= 3, "scripts/owner_identity.txt is missing or emptied"
    assert L.OWNER_NAMES, "the module-level list is what lint_body uses"


def test_lint_body_reports_an_owner_name(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "OWNER_NAMES", _names(tmp_path, "XG\n"))
    doc = tmp_path / "SKILL.md"
    doc.write_text("ok line\ntouching a system file **needs XG approval**\n")
    out = L.lint_body(doc)
    assert len(out) == 1 and ":2:" in out[0] and "'XG'" in out[0]


def test_the_body_scope_includes_experts_and_context_seeds():
    roots = {p.relative_to(_REPO).parts[:2] for p in L.corpus_files()}
    for need in (("backend", "skills"), ("backend", "experts"), ("backend", "context")):
        assert need in roots, f"{'/'.join(need)} is not body-linted"


def test_shipped_corpus_names_no_owner_today():
    found = [f for p in L.corpus_files() for f in L.lint_body(p) if "names one specific person" in f]
    assert found == []
