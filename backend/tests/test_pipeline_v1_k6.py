"""K6 (conformance L2, L4): two checks follow the design text exactly.

- (L1, RQ-8b's zh/en keyword table, was removed in S6-4c: Gate-0 judges the wording
  in any language; tests/test_design_spec_generic.py.)
- L2: a full DESIGN_SPEC must name ``properties`` and ``failure_modes`` (empty is fine).
- L4: a ``.py`` change is judged on its AST: only comments and layout are not code;
  every string is, docstrings included (XG 2026-10-23); so are an added / deleted
  file, a mode or type change and an unreadable or unparsable side (review 950a628c).
"""
from __future__ import annotations

import pytest

from core.run_protocol import schemas
from core.run_protocol.engine import _comment_only_diff

def _design(**over):
    base = {"alternatives": [{"id": "a"}], "chosen": "a",
            "design": {"user_workflow": "u", "architecture": "a", "flow": "f",
                       "states": [{"name": "s", "created_by": "x", "removed_by": "y"}]},
            "properties": [], "failure_modes": [], "tasks": [], "exit_criteria": [],
            "consumers": [], "no_contract_change_reason": "none",
            "security_boundaries": []}
    base.update(over)
    return {k: v for k, v in base.items() if v is not ...}


def test_a_full_design_must_name_properties_and_failure_modes():
    for key in ("properties", "failure_modes"):
        issues = schemas.validate_design_spec(_design(**{key: ...}), variant="full",
                                              requirement_ids=set())
        assert any(issue.path == key for issue in issues), (key, issues)
    issues = schemas.validate_design_spec(_design(), variant="full", requirement_ids=set())
    assert not any(issue.path in ("properties", "failure_modes") for issue in issues)


CODE = 'def f(x):\n    """Doc."""\n    return x + 1  # add\n'


def _py(text: str, mode: str = "100644") -> tuple[str, bytes]:
    return mode, text.encode()


@pytest.mark.parametrize("new,unchanged", [
    ('def f(x):\n    """Other doc,\n    two lines."""\n    return x + 1\n', False),  # a docstring is data
    ('# header\ndef f(x):\n    """Doc."""\n    return x + 1  # changed comment\n', True),
    ('def f(x):\n    """Doc."""\n    return x + 2  # add\n', False),
    ('def f(x):\n    """Doc."""\n    return x + 1 or "doc"\n', False),
    ('def f(x):\n    return (x +\n', False),                 # does not parse
])
def test_python_changes_are_judged_without_comments(new, unchanged):
    assert _comment_only_diff("a.py", "", old=_py(CODE), new=_py(new)) is unchanged


@pytest.mark.parametrize("old,new", [
    # review 950a628c: each changes what runs, so each is executable
    ('"""safe"""\nmode = __doc__\n', '"""danger"""\nmode = __doc__\n'),   # docstring as data
    ('events=[]\nf"{events.append(1)}"\n', 'events=[]\nf"{events.append(2)}"\n'),  # f-string
    ('x = 1\n"""note"""\n', 'x = 1\n"""other"""\nprint(x)\n'),
    ('log(\n    "one"\n)\n', 'log(\n    "two"\n)\n'),           # a string argument
])
def test_strings_that_are_data_are_code(old, new):
    assert _comment_only_diff("a.py", "", old=_py(old), new=_py(new)) is False


@pytest.mark.parametrize("old,new", [
    (("absent", None), _py('"""plugin"""\n')),                 # a new module
    (_py('"""plugin"""\n'), ("absent", None)),                 # a deleted module
    (_py("x = 1\n"), _py("x = 1\n", "100755")),               # a mode change
    (("120000", None), ("120000", None)),                        # a symlink
    (None, _py("x = 1\n")),                                     # an unreadable side
    (_py("x = 1\n"), ("100644", b"\xff\xfe x = 1\n")),           # does not decode
])
def test_presence_mode_and_read_errors_fail_closed(old, new):
    assert _comment_only_diff("a.py", "", old=old, new=new) is False


def test_blob_tells_absent_from_unreadable(tmp_path):
    import subprocess
    from core.run_protocol.engine import _blob
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "-odd: name.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=a",
                    "commit", "-qm", "m"], check=True)
    assert _blob(str(repo), "HEAD", "-odd: name.py") == ("100644", b"x = 1\n")
    assert _blob(str(repo), "HEAD", "missing.py") == ("absent", None)
    assert _blob(str(repo), "no-such-tree", "x.py") is None
