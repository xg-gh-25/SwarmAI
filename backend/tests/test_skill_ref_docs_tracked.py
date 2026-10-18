"""Every ``ref/`` doc a skill doc points at must ship: present and not gitignored.

Why this exists: the stage-doc split moved BUILD's rules into
``stages/ref/build/*.md``, and the repo's Python ``build/`` ignore rule silently
kept that whole directory out of git. Every on-disk check passed on the author's
machine (the files were there), while a fresh clone or a release got a BUILD card
that points at six files that do not exist.

The test reads the references from TRACKED skill docs only and asks git, with
``check-ignore --no-index``, whether each referenced path would be ignored. That
question is answered by the ignore rules alone, so it gives the same answer on a
dev machine and in a bare CI checkout: in CI an ignored target is also missing,
and on a dev machine it is present but ignored. Both fail here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILLS = "backend/skills"

# `stages/ref/<dir>/<file>.md` or the stage-relative `ref/<dir>/<file>.md`.
_REF = re.compile(r"(?<![\w/.-])((?:stages/)?ref/[\w-]+/[\w.-]+\.md)\b")
# A folder mention such as "under `stages/ref/evaluate/`" followed by bare names.
_REF_DIR = re.compile(r"(?<![\w/.-])((?:stages/)?ref/[\w-]+)/(?![\w.-])")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def _referenced_ref_docs(dirs: bool = False) -> set[str]:
    """Referenced ref doc paths; with ``dirs=True``, a probe file per referenced folder."""
    tracked = _git("ls-files", "-z", "--", f"{SKILLS}/*.md").stdout.split("\0")
    refs: set[str] = set()
    for rel in filter(None, tracked):
        parts = Path(rel).parts  # backend/skills/<skill>/...
        if len(parts) < 4:
            continue
        skill_root = Path(*parts[:3])
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        pattern, suffix = (_REF_DIR, "/__probe__.md") if dirs else (_REF, "")
        for m in pattern.finditer(text):
            ref = m.group(1) + suffix
            if not ref.startswith("stages/"):
                ref = "stages/" + ref
            refs.add(str(skill_root / ref))
    return refs


def _ignored(paths: set[str]) -> set[str]:
    r = subprocess.run(
        ["git", "-C", str(REPO), "check-ignore", "--no-index", "--stdin"],
        input="\n".join(sorted(paths)) + "\n", capture_output=True, text=True,
    )
    assert r.returncode in (0, 1), f"git check-ignore failed: {r.stderr}"
    return {line for line in r.stdout.splitlines() if line}


def test_references_are_found():
    """Guard against a vacuous pass: the scan must see the real BUILD references."""
    refs = _referenced_ref_docs()
    assert f"{SKILLS}/s_autonomous-pipeline/stages/ref/build/gate1.md" in refs
    assert len(refs) > 20


def test_no_referenced_ref_doc_is_gitignored():
    ignored = _ignored(_referenced_ref_docs())
    assert not ignored, (
        "skill docs point at ref docs that .gitignore keeps out of git (a fresh "
        "clone or release will not have them):\n" + "\n".join(sorted(ignored))
    )


def test_no_referenced_ref_folder_is_gitignored():
    """Folder headings ("under stages/ref/evaluate/") name files only by bare name."""
    probes = _referenced_ref_docs(dirs=True)
    assert f"{SKILLS}/s_autonomous-pipeline/stages/ref/evaluate/__probe__.md" in probes
    ignored = _ignored(probes)
    assert not ignored, "referenced ref folders are gitignored:\n" + "\n".join(sorted(ignored))


def test_no_ref_doc_on_disk_is_gitignored():
    """Dev-machine check over every file actually present under stages/ref/."""
    on_disk = {str(p.relative_to(REPO)) for p in (REPO / SKILLS).glob("*/stages/ref/**/*.md")}
    ignored = _ignored(on_disk)
    assert not ignored, "ref docs on disk are gitignored:\n" + "\n".join(sorted(ignored))


def test_ignore_check_detects_the_build_rule():
    """The check itself must be live: a path under a plain build/ dir is ignored."""
    assert _ignored({"backend/somepkg/build/x.md"}) == {"backend/somepkg/build/x.md"}


def test_every_referenced_ref_doc_is_tracked_or_staged():
    """Present on disk is not enough: the file must be tracked, or untracked and addable.

    In a bare CI checkout only tracked files exist, so this is the index check there.
    """
    refs = _referenced_ref_docs()
    known = set(filter(None, _git("ls-files", "-z", "--cached", "--others",
                                  "--exclude-standard", "--", SKILLS).stdout.split("\0")))
    missing = sorted(r for r in refs if r not in known)
    assert not missing, "referenced ref docs not in git:\n" + "\n".join(missing)
