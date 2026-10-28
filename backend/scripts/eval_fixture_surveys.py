"""Generate (or check) the survey.json of each New Brain offline-test fixture brain.

The New Brain offline cases (golden set ``OFF_BRAIN_FIRST_ENTRIES_*``) give the agent
what the real flow gives it after a bind: ``Projects/<Brain>/.artifacts/bootstrap/
survey.json``, written by the real survey code over the fixture repo under
``templates/eval/fixtures/workspace/repos/``. Two fields differ from a live survey so
the file can ship with the product and stay identical on every machine:

- ``root`` is the repo's path relative to the workspace (a live survey holds the
  absolute path), which is also how the case prompt names the repo;
- ``generated_at`` is a fixed value.

The fixture repos are not git repos of their own, so the survey carries no git facts
(no HEAD, history or hot spots) and no code graph (``code_shape`` says unavailable).

Usage, from ``backend/``::

    "$SWARM_BACKEND_BIN" py -m scripts.eval_fixture_surveys --check   # exit 1 on drift
    "$SWARM_BACKEND_BIN" py -m scripts.eval_fixture_surveys --write   # regenerate

Run ``--write`` after changing a fixture repo or the survey code; the test
``tests/test_eval_bootstrap_entries.py`` fails until the shipped files match.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.brain_bootstrap import survey

WORKSPACE = Path(__file__).resolve().parent.parent / "templates" / "eval" / "fixtures" / "workspace"

# brain name -> repo path relative to the workspace
BRAINS: dict[str, str] = {
    "Tokensign": "repos/tokensign",
    "Ledgerd": "repos/ledgerd",
}

GENERATED_AT = "fixture"


def build(workspace: Path = WORKSPACE) -> dict[str, str]:
    """``{path relative to the workspace: file text}`` for every fixture brain."""
    out: dict[str, str] = {}
    for brain, rel in BRAINS.items():
        repo = survey.survey_repo(Path(rel).name, workspace / rel)
        repo["root"] = rel
        result = {
            "schema": survey.SCHEMA, "version": survey.VERSION,
            "generated_at": GENERATED_AT, "project": brain, "repos": [repo],
        }
        path = (Path("Projects") / brain / survey.SURVEY_REL).as_posix()
        out[path] = json.dumps(result, ensure_ascii=False, indent=1) + "\n"
    return out


def drift(workspace: Path = WORKSPACE) -> list[str]:
    """The fixture survey files that are missing or differ from a fresh build."""
    bad = []
    for rel, text in build(workspace).items():
        p = workspace / rel
        if not p.is_file() or p.read_text(encoding="utf-8") != text:
            bad.append(rel)
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    if args.write:
        for rel, text in build().items():
            p = WORKSPACE / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            print(f"wrote {rel}")
        return 0
    bad = drift()
    for rel in bad:
        print(f"out of date: {rel} (run with --write)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
