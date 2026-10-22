"""Pipeline v1 tree identity tests (v13 §A3.7, §D4 LG-3).

Methodology: injected deterministic tree writers/differs; no git or filesystem I/O.
Properties: code_tree excludes the reserved path, delivery_tree adds one renderer-owned
REPORT, all repos are bound, and every extra/missing/changed path or sha fails LG-3.
"""
from __future__ import annotations

import hashlib

import pytest

from core.run_protocol import tree

R1 = "1" * 40
R2 = "2" * 40
D1 = "a" * 40
D2 = "b" * 40
REPORT = b"# Delivery\n"
SHA = hashlib.sha256(REPORT).hexdigest()
REPOS = {"api": "/work/api", "web": "/work/web"}


class Writer:
    def __init__(self):
        self.calls = []

    def __call__(self, root, excludes, overlays):
        self.calls.append((root, excludes, dict(overlays)))
        if overlays:
            return D1 if root.endswith("api") else D2
        return R1 if root.endswith("api") else R2


def test_report_path_is_run_scoped_and_rejects_traversal():
    assert tree.report_path("run_abc") == ".aidlc/deliverables/run_abc/REPORT.md"
    for bad in ("", "../x", "a/b", "a\\b"):
        with pytest.raises(tree.TreeError):
            tree.report_path(bad)


def test_code_trees_exclude_the_complete_reserved_directory():
    writer = Writer()
    out = tree.build_code_trees(REPOS, "run_abc", writer, lambda *_: [])
    assert out == {"api": R1, "web": R2}
    assert [call[0] for call in writer.calls] == ["/work/api", "/work/web"]
    assert all(call[1] == (".aidlc/deliverables/run_abc/",) for call in writer.calls)
    assert all(call[2] == {} for call in writer.calls)


def test_delivery_trees_overlay_identical_report_in_every_repo():
    writer = Writer()
    out = tree.build_delivery_trees(REPOS, "run_abc", {"api": R1, "web": R2},
                                    REPORT, SHA, writer, lambda *_: [])
    assert out["api"]["delivery_tree"] == D1
    assert out["web"]["delivery_tree"] == D2
    assert all(c[2] == {tree.report_path("run_abc"): REPORT} for c in writer.calls)
    assert {x["report_sha"] for x in out.values()} == {SHA}


def test_tree_builders_reject_missing_repos_bad_ids_and_report_mismatch():
    writer = Writer()
    with pytest.raises(tree.TreeError, match="non-empty"):
        tree.build_code_trees({}, "run_x", writer, lambda *_: [])
    with pytest.raises(tree.TreeError, match="exactly"):
        tree.build_delivery_trees(REPOS, "run_x", {"api": R1}, REPORT, SHA, writer, lambda *_: [])
    with pytest.raises(tree.TreeError, match="render event"):
        tree.build_delivery_trees(REPOS, "run_x", {"api": R1, "web": R2},
                                  REPORT, "0" * 64, writer, lambda *_: [])

    def bad(*_args):
        return "not-a-tree"
    with pytest.raises(tree.TreeError, match="invalid git tree"):
        tree.build_code_trees({"api": "/work/api"}, "run_x", bad, lambda *_: [])


def _binding():
    return {"code_tree": R1, "delivery_tree": D1,
            "report_path": tree.report_path("run_x"), "report_sha": SHA}


def test_report_path_rejects_dot_dot_controls_and_non_run_ids():
    for bad in (".", "..", "run_../x", "run_x\x00y", "run_x\ny", "other"):
        with pytest.raises(tree.TreeError):
            tree.report_path(bad)


def test_tree_builders_reject_any_preexisting_reserved_path_content():
    foreign = lambda _root, _reserved: [".aidlc/deliverables/run_x/foreign.txt"]
    writer = Writer()
    with pytest.raises(tree.TreeError, match="not empty"):
        tree.build_code_trees(REPOS, "run_x", writer, foreign)
    with pytest.raises(tree.TreeError, match="not empty"):
        tree.build_delivery_trees(REPOS, "run_x", {"api": R1, "web": R2},
                                  REPORT, SHA, writer, foreign)
    assert writer.calls == []  # provenance check runs before exclusion can hide it


def _diff(entries):
    return lambda _repo, _code, _delivery: entries


def test_lg3_accepts_exactly_one_added_rendered_report():
    ok, reason = tree.check_lg3(
        repo="/work/api", run_id="run_x", binding=_binding(),
        rendered_report_sha=SHA,
        diff_trees=_diff([{"status": "A", "path": tree.report_path("run_x"),
                           "blob_sha256": SHA}]),
    )
    assert ok and "exactly" in reason


@pytest.mark.parametrize("entries,part", [
    ([], "got 0"),
    ([{"status": "A", "path": ".aidlc/deliverables/run_x/REPORT.md",
       "blob_sha256": SHA}, {"status": "A", "path": "extra.py",
                             "blob_sha256": SHA}], "got 2"),
    ([{"status": "M", "path": ".aidlc/deliverables/run_x/REPORT.md",
       "blob_sha256": SHA}], "other than"),
    ([{"status": "A", "path": "README.md", "blob_sha256": SHA}], "other than"),
    ([{"status": "A", "path": ".aidlc/deliverables/run_x/REPORT.md",
       "blob_sha256": "0" * 64}], "blob differs"),
])
def test_lg3_rejects_every_non_report_only_difference(entries, part):
    ok, reason = tree.check_lg3(
        repo="/work/api", run_id="run_x", binding=_binding(),
        rendered_report_sha=SHA, diff_trees=_diff(entries),
    )
    assert not ok and part in reason


def test_lg3_rejects_incomplete_wrong_run_or_wrong_render_binding():
    cases = [
        ({}, SHA, "incomplete"),
        ({**_binding(), "report_path": tree.report_path("run_other")}, SHA,
         "not this run"),
        ({**_binding(), "report_sha": "0" * 64}, SHA, "differs from the render"),
    ]
    for binding, render_sha, part in cases:
        ok, reason = tree.check_lg3(
            repo="/work/api", run_id="run_x", binding=binding,
            rendered_report_sha=render_sha, diff_trees=_diff([]),
        )
        assert not ok and part in reason


def test_lg3_fails_closed_when_the_tree_adapter_fails_or_returns_bad_shape():
    def boom(*_args):
        raise OSError("git died")
    ok, reason = tree.check_lg3(repo="/work/api", run_id="run_x", binding=_binding(),
                                rendered_report_sha=SHA, diff_trees=boom)
    assert not ok and "OSError" in reason
    ok, reason = tree.check_lg3(repo="/work/api", run_id="run_x", binding=_binding(),
                                rendered_report_sha=SHA, diff_trees=lambda *_: "bad")
    assert not ok and "invalid" in reason


def test_all_lg3_requires_and_checks_every_repo():
    bindings = {
        "api": _binding(),
        "web": {**_binding(), "code_tree": R2, "delivery_tree": D2},
    }

    def differ(repo, _code, _delivery):
        return [{"status": "A", "path": tree.report_path("run_x"),
                 "blob_sha256": SHA}] if repo.endswith("api") else [
            {"status": "A", "path": "extra", "blob_sha256": SHA}
        ]

    ok, details = tree.check_all_lg3(
        repos=REPOS, run_id="run_x", bindings=bindings,
        rendered_report_sha=SHA, diff_trees=differ,
    )
    assert not ok and "exactly" in details["api"] and "other than" in details["web"]
    ok, details = tree.check_all_lg3(
        repos=REPOS, run_id="run_x", bindings={"api": _binding()},
        rendered_report_sha=SHA, diff_trees=differ,
    )
    assert not ok and set(details) == {"*"}
