"""Tests for ``scripts/check_rule_preservation.py`` (design §4-C3).

Methodology: each test builds a throwaway git repo under ``tmp_path`` (commits
made with ``-c core.hooksPath=/dev/null`` so corporate hooks never run) that
mimics the pipeline skill layout, then drives the tool through ``main(argv)``
or its public functions. Covered properties:

* every marker shape found in the real docs is extracted ([GATE], [GATE·x],
  backticked tags, [MUST], BLOCKING in all its spellings, ⛔, table cells),
  while excluded shapes (lowercase blocking, [GUIDE]) are not;
* extraction is deterministic: same ref -> byte-identical manifest, IDs in
  document order, hash == sha256(text);
* ``extract`` refuses to overwrite without ``--force`` and honours ``--ref``;
* ``check`` passes when every ID is tagged in card/ref docs, and fails on
  missing, unknown and history-only IDs; multi-location is a warning only;
* ``index`` renders rule -> current file, flagging MISSING;
* one smoke test extracts the REAL repo HEAD and requires >0 rules per doc.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_rule_preservation.py"
_spec = importlib.util.spec_from_file_location("check_rule_preservation", _SCRIPT)
crp = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("check_rule_preservation", crp)  # dataclasses need it
_spec.loader.exec_module(crp)

SKILL = crp.SKILL_DIR

INSTR_DOC = """# Pipeline

## Core loop `[MUST]`

Plain intro paragraph, non-blocking advice only.

**[GATE·cli] BLOCKING: Read the card first.**

- [MUST] Never loop forever.
- A plain bullet.

| Gate | Level |
|---|---|
| Push-ready | [GATE] |

> ⛔ ORDER: report before surface.

**Execution (BLOCKING):**

1. step one
2. step two

[GUIDE] optional hint, Blocking findings are prose here.
"""

BUILD_DOC = """# Build

## Step 1 (BLOCKING for handler code) `[GATE·validator]`

Body.

```bash
echo hi
```(stray prose [MUST] after a fence)

```
never closed

**VERIFY rules (BLOCKING):** `[MUST]`
"""

TEST_DOC = """# Test

Layer 4 runs `[GATE·artifact_cli]` when cross-boundary.

| Layer | Rule `[MUST]` |
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
         "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True, text=True, timeout=30,
    ).stdout


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / SKILL / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _commit(repo: Path, msg: str = "docs") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root, "INSTRUCTIONS.md", INSTR_DOC)
    _write(root, "stages/build.md", BUILD_DOC)
    _write(root, "stages/test.md", TEST_DOC)
    _write(root, "stages/specialists/sec.md", "- [MUST] specialist rule\n")
    _commit(root)
    return root


def _run(repo: Path, capsys, *argv: str) -> tuple[int, str]:
    code = crp.main([*argv, "--repo", str(repo)])
    return code, capsys.readouterr().out


def _manifest(repo: Path) -> dict:
    return json.loads((repo / crp.DEFAULT_MANIFEST).read_text(encoding="utf-8"))


def _by_text(rules: list[dict]) -> dict[str, dict]:
    return {r["text"]: r for r in rules}


def test_extract_covers_every_marker_shape(repo, capsys):
    code, out = _run(repo, capsys, "extract")
    assert code == 0, out
    m = _manifest(repo)
    instr = [r for r in m["rules"] if r["source"] == "instr"]
    texts = _by_text(instr)
    assert texts["## Core loop [MUST]"]["block"] == "heading"
    assert texts["[GATE·cli] BLOCKING: Read the card first."]["markers"] == ["BLOCKING", "GATE·cli"]
    assert texts["[MUST] Never loop forever."]["block"] == "list-item"
    assert texts["Push-ready | [GATE]"]["block"] == "table-row"
    assert texts["⛔ ORDER: report before surface."]["block"] == "quote-paragraph"
    # label paragraph absorbs the list under it
    assert "Execution (BLOCKING): step one step two" in texts
    assert len(instr) == 6  # plain prose / lowercase blocking / [GUIDE] excluded
    assert [r["id"] for r in instr] == [f"R-instr-{i:03d}" for i in range(1, 7)]


def test_extract_stage_docs_fences_tables_backticks(repo, capsys):
    _run(repo, capsys, "extract")
    m = _manifest(repo)
    build = _by_text([r for r in m["rules"] if r["source"] == "build"])
    assert set(build) == {
        "## Step 1 (BLOCKING for handler code) [GATE·validator]",
        "(stray prose [MUST] after a fence)",  # malformed fence closer kept
        "VERIFY rules (BLOCKING): [MUST]",  # after an unclosed fence
    }
    test = [r for r in m["rules"] if r["source"] == "test"]
    assert [r["markers"] for r in test] == [["GATE·artifact_cli"], ["MUST"]]
    assert "`[GATE·artifact_cli]`" in m["shape_counts"]
    assert not any(r["source"] == "sec" for r in m["rules"])  # specialists/ skipped
    assert m["excluded_shape_counts"]["[GUIDE]"] == 1
    assert {f["source"]: f["rules"] for f in m["files"]} == {"instr": 6, "build": 3, "test": 2}


def test_reextract_is_deterministic_and_hashed(repo, capsys):
    _run(repo, capsys, "extract")
    first = (repo / crp.DEFAULT_MANIFEST).read_bytes()
    assert _run(repo, capsys, "extract", "--force")[0] == 0
    assert (repo / crp.DEFAULT_MANIFEST).read_bytes() == first
    for r in json.loads(first)["rules"]:
        assert r["hash"] == hashlib.sha256(r["text"].encode()).hexdigest()[:12]


def test_extract_refuses_overwrite_without_force(repo, capsys):
    path = repo / crp.DEFAULT_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    code, _ = _run(repo, capsys, "extract")
    assert code == 2
    assert path.read_text(encoding="utf-8") == "{}"
    assert _run(repo, capsys, "extract", "--force")[0] == 0
    assert len(_manifest(repo)["rules"]) == 11


def test_extract_reads_ref_not_working_tree(repo, capsys, tmp_path):
    _write(repo, "stages/build.md", "# Build\n\nno rules left\n")
    _commit(repo, "split")
    _write(repo, "stages/test.md", "# dirty working tree, uncommitted\n")
    out = tmp_path / "old.json"
    assert _run(repo, capsys, "extract", "--ref", "HEAD~1", "--out", str(out))[0] == 0
    old = json.loads(out.read_text(encoding="utf-8"))
    assert len(old["rules"]) == 11 and old["ref"] == "HEAD~1"
    assert _run(repo, capsys, "extract", "--force")[0] == 0
    assert len(_manifest(repo)["rules"]) == 8  # HEAD: build has 0, test still 2


def test_extract_rejects_bad_ref(repo, capsys):
    assert _run(repo, capsys, "extract", "--ref=--upload-pack=x")[0] == 2
    assert _run(repo, capsys, "extract", "--ref", "no-such-ref")[0] == 2
    assert not (repo / crp.DEFAULT_MANIFEST).exists()


def _split(repo: Path, placement: dict[str, list[str]]) -> None:
    """Simulate the 3-layer split: rewrite the docs holding only the given tags."""
    for rel in ("INSTRUCTIONS.md", "stages/build.md", "stages/test.md"):
        _write(repo, rel, "# card\n")
    for rel, ids in placement.items():
        body = "".join(f"- rule text <!-- {rid} -->\n" for rid in ids)
        _write(repo, rel, f"# {rel}\n\n{body}")


def _ids(repo: Path) -> list[str]:
    return [r["id"] for r in _manifest(repo)["rules"]]


def _good_placement(ids: list[str]) -> dict[str, list[str]]:
    instr = [i for i in ids if i.startswith("R-instr")]
    build = [i for i in ids if i.startswith("R-build")]
    test = [i for i in ids if i.startswith("R-test")]
    return {
        "INSTRUCTIONS.md": instr[:3],
        "stages/ref/instructions/escalation.md": instr[3:],
        "stages/build.md": build[:1],
        "stages/ref/build/fences.md": build[1:],
        "stages/test.md": test,
    }


def test_check_passes_when_every_tag_moved_to_card_or_ref(repo, capsys):
    _run(repo, capsys, "extract")
    _split(repo, _good_placement(_ids(repo)))
    _write(repo, "stages/history/build.md", "why <!-- R-build-001 -->\n")  # extra copy ok
    code, out = _run(repo, capsys, "check")
    assert code == 0, out
    assert "PASS: 11/11 rules preserved" in out
    summary = json.loads(out.split("SUMMARY ", 1)[1])
    assert summary["ok"] is True and summary["missing"] == 0


def test_check_fails_on_missing_rule(repo, capsys):
    _run(repo, capsys, "extract")
    placement = _good_placement(_ids(repo))
    dropped = placement["stages/ref/build/fences.md"].pop()
    _split(repo, placement)
    code, out = _run(repo, capsys, "check")
    assert code == 1
    assert f"MISSING {dropped}" in out
    assert "VERIFY rules (BLOCKING): [MUST]" in out  # original text shown
    assert f"{SKILL}/stages/build.md:" in out  # original source line shown


def test_check_fails_on_unknown_tag(repo, capsys):
    _run(repo, capsys, "extract")
    placement = _good_placement(_ids(repo))
    placement["stages/build.md"].append("R-build-999")
    _split(repo, placement)
    code, out = _run(repo, capsys, "check")
    assert code == 1
    assert "UNKNOWN R-build-999" in out and "stages/build.md:" in out


def test_check_fails_when_rule_only_in_history(repo, capsys):
    _run(repo, capsys, "extract")
    placement = _good_placement(_ids(repo))
    moved = placement["stages/test.md"].pop(0)
    _split(repo, placement)
    _write(repo, "stages/history/test.md", f"incident <!-- {moved} -->\n")
    code, out = _run(repo, capsys, "check", "--json")
    assert code == 1
    result = json.loads(out)
    assert [e["id"] for e in result["history_only"]] == [moved]
    assert result["missing"] == [] and result["ok"] is False
    assert result["history_only"][0]["locations"][0]["file"].endswith("stages/history/test.md")


def test_multi_location_is_warning_only(repo, capsys):
    _run(repo, capsys, "extract")
    placement = _good_placement(_ids(repo))
    placement["stages/ref/build/fences.md"].append("R-build-001")
    _split(repo, placement)
    code, out = _run(repo, capsys, "check")
    assert code == 0, out
    assert "WARN multi-location R-build-001" in out


def test_one_comment_may_carry_several_ids_and_prose_comments_ignored(repo, capsys):
    _run(repo, capsys, "extract")
    ids = _ids(repo)
    _split(repo, {})
    _write(repo, "stages/build.md",
           "# card\n<!-- " + " ".join(ids) + " -->\n<!-- note: R-build-777 moved -->\n")
    code, out = _run(repo, capsys, "check")
    assert code == 0, out  # R-build-777 sits in a prose comment, not a tag


def test_also_glob_counts_extra_runtime_docs(repo, capsys):
    _run(repo, capsys, "extract")
    placement = _good_placement(_ids(repo))
    moved = placement["stages/test.md"].pop()
    _split(repo, placement)
    _write(repo, "data/gate-prompts/meta-review.md", f"prompt <!-- {moved} -->\n")
    assert _run(repo, capsys, "check")[0] == 1
    assert _run(repo, capsys, "check", "--also", "data/gate-prompts/*.md")[0] == 0
    assert _run(repo, capsys, "check", "--also", "../../x/*.md")[0] == 2


def test_index_maps_rule_to_current_file(repo, capsys):
    _run(repo, capsys, "extract")
    placement = _good_placement(_ids(repo))
    dropped = placement["stages/test.md"].pop()
    _split(repo, placement)
    code, out = _run(repo, capsys, "index")
    assert code == 0
    lines = out.splitlines()
    assert "| Rule | Markers | Lives in | Rule text |" in lines
    row = next(ln for ln in lines if ln.startswith("| R-build-002 |"))
    assert f"`{SKILL}/stages/ref/build/fences.md`" in row
    assert "**MISSING**" in next(ln for ln in lines if ln.startswith(f"| {dropped} |"))
    code, out = _run(repo, capsys, "index", "--json")
    rows = {r["id"]: r for r in json.loads(out)}
    assert rows[dropped]["status"] == "missing"
    assert rows["R-instr-001"]["files"] == [f"{SKILL}/INSTRUCTIONS.md"]


def test_check_without_manifest_is_usage_error(repo, capsys):
    assert _run(repo, capsys, "check")[0] == 2


def test_real_repo_head_has_rules_in_every_doc(tmp_path, capsys):
    root = crp.repo_root(_SCRIPT.parent)
    out = tmp_path / "rules.json"
    assert crp.main(["extract", "--repo", str(root), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    m = json.loads(out.read_text(encoding="utf-8"))
    per_file = {f["source"]: f["rules"] for f in m["files"]}
    with capsys.disabled():
        print(f"\n[real HEAD {m['commit'][:10]}] rules={len(m['rules'])} per_file={per_file}")
        print(f"markers={m['marker_counts']}")
    assert "instr" in per_file and len(per_file) > 1, printed
    assert all(n > 0 for n in per_file.values()), per_file
    ids = [r["id"] for r in m["rules"]]
    assert len(ids) == len(set(ids))
    assert crp.extract(root, m["commit"])["rules"] == m["rules"]  # deterministic
