"""s_frontend-design carries Modern Web Guidance pinned, offline and verified.

Questions this answers:
  - Do the vendored guides match the sha256 and commit PROVENANCE.json records, with no
    undeclared file (``core.app_provenance.load_verified_provenance``)?
  - Does search rank the guide a task names first for the queries INSTRUCTIONS.md teaches?
  - Does no printed text tell the agent to run the npm package (the guides' own
    cross-references name ``npx -y modern-web-guidance@latest retrieve``)?
  - Does every guide id INSTRUCTIONS.md cites exist?
  - Does retrieve fail loud on an unknown id instead of printing nothing?
  - Is no listed guide hidden by .gitignore (the root planning-note rule once hid
    guides/ui-components/progress.md, so a clean checkout lacked it), while the
    planning notes it was written for stay ignored?

Pure file and in-process checks (the ignore check asks the checkout's git): self-verifies
in a bare checkout (no workspace, no network, no Node).
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from core.app_provenance import load_verified_provenance

SKILL = Path(__file__).resolve().parents[1] / "skills" / "s_frontend-design"
DATA = SKILL / "data" / "modern-web-guidance"


def _script():
    spec = importlib.util.spec_from_file_location(
        "_web_guidance_under_test", SKILL / "scripts" / "web_guidance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_listed_guide_is_git_ignored():
    import subprocess
    repo = Path(__file__).resolve().parents[2]
    provenance = load_verified_provenance(DATA)
    listed = [str((DATA / f.local_path).relative_to(repo)) for f in provenance.files]

    def ignored(path: str) -> bool:
        # --no-index: the rules, whether or not the file is tracked. The repository's own
        # rules only: a developer's global excludes file must not change the answer.
        r = subprocess.run(["git", "-c", "core.excludesFile=/dev/null", "-C", str(repo),
                            "check-ignore", "-q", "--no-index", path],
                           capture_output=True, text=True, timeout=30)
        # 0 = ignored, 1 = not ignored; anything else (not a git work tree) answers nothing
        assert r.returncode in (0, 1), f"git check-ignore failed ({r.returncode}): {r.stderr}"
        return r.returncode == 0
    assert [p for p in listed if ignored(p)] == []
    # the planning-note names stay ignored outside a skill's data tree
    for name in ("progress.md", "task_plan.md", "findings.md"):
        assert ignored(name) and ignored(f"backend/{name}") and ignored(f"desktop/src/{name}")
        assert not ignored(f"backend/skills/s_x/data/guides/{name}")


def test_vendored_guides_match_their_provenance():
    provenance = load_verified_provenance(DATA)
    assert re.fullmatch(r"[0-9a-f]{40}", provenance.sources[0].commit)
    guides = [f for f in provenance.files if f.local_path.startswith("guides/")]
    assert len(guides) == len(list((DATA / "guides").rglob("*.md")))
    assert len(guides) > 100


@pytest.mark.parametrize("query,expected", [
    ("light dismiss dialog", "light-dismiss-a-dialog"),
    ("responsive data table", "responsive-table"),
    ("dark mode toggle", "dark-mode-toggle"),
    ("validate input after interaction", "validate-input-after-interaction"),
])
def test_search_ranks_the_named_guide_first(query, expected):
    assert _script().search(query)[0]["id"] == expected


def test_no_printed_text_runs_the_npm_package():
    web = _script()
    raw = [p.read_text(encoding="utf-8") for p in (DATA / "guides").rglob("*.md")]
    assert any("npx -y modern-web-guidance@latest retrieve" in t for t in raw)
    for guide in web.load():
        assert "npx" not in guide["text"], guide["id"]
        assert "npx" not in guide["summary"], guide["id"]
    text, missing = web.retrieve(["html"])
    assert not missing and "web_guidance.py retrieve" in text and "npx" not in text


def test_every_guide_id_the_instructions_cite_exists():
    instructions = (SKILL / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    start = instructions.index("## Modern web guidance")
    table = instructions[instructions.index("### Step 4: Interactive Elements"):]
    table = table[:table.index("### Step 5")]
    cited = set(re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", table))
    cited |= set(re.findall(r"retrieve ([a-z0-9-]+(?:,[a-z0-9-]+)*)",
                            instructions[start:start + 3000])[0].split(","))
    ids = {g["id"] for g in _script().load()}
    assert cited and cited <= ids, sorted(cited - ids)


def test_retrieve_of_an_unknown_id_fails(capsys):
    assert _script().main(["retrieve", "no-such-guide"]) == 1
    assert "no such guide: no-such-guide" in capsys.readouterr().err


def test_list_names_every_guide_once():
    web = _script()
    listed = [i for ids in web.listing().values() for i in ids]
    assert len(listed) == len(set(listed)) == len(web.load())
    json.dumps(web.listing())
