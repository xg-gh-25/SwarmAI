"""Tests for the product-artifact body rule in ``scripts/lint_skills.py``.

WHAT IS TESTED
    The linter's body check, which rejects two content classes from any file the
    product ships under ``backend/skills/``:

      1. an identifier that resolves only on the authoring machine — a pipeline
         run id, or one of the small register prefixes used by this project's
         private cognitive stores;
      2. an absolute path naming a specific machine owner.

WHY IT MATTERS
    That corpus is published. A rule whose stated justification is a token the
    reader cannot resolve loses its authority at the moment it is enforced, and
    the authoring machine cannot resolve most of them either once the referenced
    run directories are cleaned up.

METHODOLOGY
    Each test drives the real linter functions against a temporary tree, never a
    reimplementation. Three properties carry the suite and each is mutation-proven
    in the run record:

      * the run-id pattern is IMPORTED from the ingestion gate, asserted by object
        identity, so replacing it with a local copy fails here;
      * file enumeration goes through git, so an untracked vendored file is not
        scanned and a shell script IS scanned;
      * the allowlist is keyed on both path and exact matched text, so it cannot
        silently exempt a whole file or a whole pattern class.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lint_skills  # noqa: E402


# ── the imported-pattern contract ─────────────────────────────────────────────

def test_runid_pattern_is_imported_from_the_ingestion_gate_not_recompiled():
    """The run-id pattern must come from the cognitive-store door, by import.

    An `is` check on the pattern objects would be VACUOUS: ``re.compile`` caches by
    pattern string, so a local ``re.compile`` of the same text returns the very same
    object and identity holds under the exact mutation this test exists to catch
    (confirmed empirically before this assertion was written).

    So assert the IMPORT instead, at the two places that can decay independently:
    the name must be bound in the module namespace by the import statement, and the
    source must not re-derive the shape locally. Together these fail when someone
    swaps the import for a copy — whatever text that copy happens to use.
    """
    from core import ingestion_gate

    assert lint_skills._SHAPE_BODY_RUNID_RE is ingestion_gate._SHAPE_BODY_RUNID_RE, (
        "the gate's pattern is not imported into the linter's namespace"
    )

    src = Path(lint_skills.__file__).read_text(encoding="utf-8")
    assert "from core.ingestion_gate import _SHAPE_BODY_RUNID_RE" in src
    binding = next(l for l in src.splitlines() if l.startswith("RUNID_RE"))
    assert "_SHAPE_BODY_RUNID_RE" in binding, f"RUNID_RE is not the imported name: {binding}"
    assert "re.compile" not in binding, f"RUNID_RE re-derives the shape locally: {binding}"


def test_register_and_ownerpath_patterns_are_local_because_upstream_has_neither():
    """The linter owns these two patterns because the ingestion gate has neither.

    Asserted as behaviour, not as prose. An earlier version of this test also
    checked that the module docstring contained the phrase "single source of
    truth" — an assertion whose operand is the docstring of the very module under
    test, so it passes with both patterns deleted. Reworded documentation is not a
    regression; a pattern that stops matching is.
    """
    import core.ingestion_gate as gate

    assert not hasattr(gate, "REGISTER_RE"), (
        "upstream grew a register pattern — import it rather than own a second copy"
    )
    assert not hasattr(gate, "OWNER_PATH_RE"), (
        "upstream grew an owner-path pattern — import it rather than own a second copy"
    )
    assert lint_skills.REGISTER_RE.search("C046")
    assert lint_skills.OWNER_PATH_RE.search("/Users/someone/Desktop")


# ── what the body rule flags ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "See the run_bd42b58f failure signature for why.",
        "C046 records the same class.",
        "This mirrors LL40 and PIT179.",
        "Recorded as O030 in the register.",
        "GS021 covers the shell case.",
        # A real owner name, not one of the neutral sample names.
        "cd /Users/gawan/Desktop/repo && ./run.sh",
    ],
)
def test_body_rule_flags_machine_local_content(tmp_path, text):
    f = tmp_path / "SKILL.md"
    f.write_text(text, encoding="utf-8")
    assert lint_skills.lint_body(f), f"expected a finding for: {text}"


@pytest.mark.parametrize(
    "text",
    [
        "Self-review found nothing; adversarial review found five, two of them HIGH.",
        "RP47 covers the vacuous-assertion tells.",
        "The pollinate register numbers its rows RP-V1 through RP-V12.",
        "Ranges such as RP1-RP81 name a package-local register.",
        "A neutral placeholder path such as /Users/jdoe/Desktop is fine in a sample.",
        "So is /Users/someone/Desktop — the point is not to publish whose machine it is.",
    ],
)
def test_body_rule_leaves_self_contained_and_package_local_text_alone(tmp_path, text):
    f = tmp_path / "SKILL.md"
    f.write_text(text, encoding="utf-8")
    assert lint_skills.lint_body(f) == [], f"unexpected finding for: {text}"


def test_no_provenance_exemption_a_trailing_footnote_is_still_flagged(tmp_path):
    """A trailing parenthetical is exempt at the private door, NOT at this one.

    The cognitive-store rule permits a trailing provenance identifier because that
    store has one reader who can resolve it. A published artifact has neither
    property, so the exemption is deliberately absent here.
    """
    f = tmp_path / "SKILL.md"
    f.write_text("Adversarial review beat self-review. (2026-05-30, run_bd42b58f)", encoding="utf-8")
    assert lint_skills.lint_body(f)


# ── the allowlist ─────────────────────────────────────────────────────────────

def test_allowlist_is_keyed_on_both_path_and_exact_text(tmp_path, monkeypatch):
    """An exemption may not widen to a whole file or a whole pattern class."""
    exempt = tmp_path / "ok.py"
    other = tmp_path / "other.py"
    payload = "\"Entry ID for field operations (e.g. 'E001', 'O001', 'F001')\""
    exempt.write_text(payload, encoding="utf-8")
    other.write_text(payload, encoding="utf-8")

    monkeypatch.setattr(lint_skills, "BODY_ALLOWLIST", {"ok.py": {"O001"}})
    monkeypatch.setattr(lint_skills, "_allow_key", lambda p: p.name)

    assert lint_skills.lint_body(exempt) == []
    assert lint_skills.lint_body(other), "same text elsewhere must still fail"


def test_every_allowlist_entry_still_matches_something():
    """A stale exemption for a deleted or edited site must not linger silently."""
    stale = []
    for rel, texts in lint_skills.BODY_ALLOWLIST.items():
        target = _REPO_ROOT / rel
        if not target.is_file():
            stale.append(f"{rel}: file is gone")
            continue
        body = target.read_text(encoding="utf-8", errors="replace")
        for t in texts:
            if t not in body:
                stale.append(f"{rel}: {t!r} no longer present")
            elif not (lint_skills.RUNID_RE.search(t) or lint_skills.REGISTER_RE.search(t)):
                # Present in the file but not a token any pattern flags, so the
                # exemption suppresses nothing. An inert entry is worse than a
                # missing one: it reads as a reviewed decision and would sit here
                # forever, since a text-presence check alone can never retire it.
                stale.append(f"{rel}: {t!r} matches no pattern — exemption is inert")
    assert stale == [], f"stale allowlist entries: {stale}"


# ── file enumeration ──────────────────────────────────────────────────────────

def test_enumeration_uses_git_so_vendored_files_are_not_scanned():
    """238 vendored node_modules files sit under the corpus and are untracked.

    A filesystem walk would scan them and a dependency refresh would redden CI on
    code this repository does not own.
    """
    files = lint_skills.corpus_files()
    assert files, "enumeration returned nothing"
    assert not [f for f in files if "node_modules" in str(f)]


def test_enumeration_covers_shell_scripts_not_only_markdown_and_python():
    """Shell scripts carried real violations that a two-extension allowlist missed."""
    suffixes = {f.suffix for f in lint_skills.corpus_files()}
    assert ".sh" in suffixes
    assert {".md", ".py"} <= suffixes


def test_enumeration_skips_binary_payloads():
    assert not [f for f in lint_skills.corpus_files() if f.suffix in {".mp3", ".png", ".jpg"}]


# ── the corpus itself, and the frontmatter pass ───────────────────────────────

def test_the_shipped_corpus_is_clean():
    findings: list[str] = []
    for f in lint_skills.corpus_files():
        findings.extend(lint_skills.lint_body(f))
    assert findings == [], f"{len(findings)} machine-local reference(s) still shipped:\n" + "\n".join(
        findings[:25]
    )


def test_frontmatter_checks_still_run_and_still_pass():
    """The body rule is additive — it must not disturb the existing metadata pass."""
    errors: list[str] = []
    for skill_dir in lint_skills.SKILL_DIRS:
        root = skill_dir if skill_dir.is_absolute() else _REPO_ROOT / skill_dir
        for skill_md in sorted(root.glob("s_*/SKILL.md")):
            errors.extend(lint_skills.lint_skill(skill_md))
    assert errors == [], f"frontmatter regressions: {errors[:10]}"


# ── the real process, the way CI runs it ──────────────────────────────────────

def test_cli_exits_zero_on_the_clean_corpus():
    r = subprocess.run(
        [sys.executable, "scripts/lint_skills.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"stdout:\n{r.stdout[-3000:]}"


def test_cli_exits_nonzero_when_an_identifier_is_reintroduced():
    """Proves the check runs over the widened file set, not just one SKILL.md."""
    target = _REPO_ROOT / "backend/skills/s_autonomous-pipeline/stages/reflect.md"
    original = target.read_text(encoding="utf-8")
    try:
        target.write_text(original + "\n\nReintroduced: run_deadbeef.\n", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "scripts/lint_skills.py"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=180,
        )
        assert r.returncode != 0
        assert "run_deadbeef" in r.stdout
    finally:
        target.write_text(original, encoding="utf-8")


# ── the gate must not read as passed when it could not run ────────────────────

def test_enumeration_failure_is_fail_closed_not_a_silent_pass(monkeypatch):
    """A gate that cannot enumerate its own scope must BLOCK, never print a tick.

    The corpus this linter guards ships the pattern itself: a verification whose
    "the check couldn't run" outcome is collapsed into a negative result
    reintroduces the very failure the check exists to catch. `git` being absent,
    a `dubious ownership` refusal, and a timeout are all real CI conditions, and
    each one previously yielded "0 corpus files carry no machine-local
    references" plus exit 0.
    """
    def boom(*a, **k):
        raise subprocess.SubprocessError("git unavailable")

    monkeypatch.setattr(lint_skills.subprocess, "run", boom)
    with pytest.raises(lint_skills.CorpusEnumerationError):
        lint_skills.corpus_files()


def test_enumeration_failure_makes_the_cli_exit_nonzero():
    """End to end: the real process must fail, not congratulate itself."""
    env = dict(os.environ, SWARM_LINT_FORCE_ENUM_FAILURE="1")
    r = subprocess.run(
        [sys.executable, "scripts/lint_skills.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=180, env=env,
    )
    assert r.returncode != 0, f"fail-open: exit 0 with stdout:\n{r.stdout[-1500:]}"
    assert "carry no machine-local references" not in r.stdout, (
        f"claimed a clean corpus while blind:\n{r.stdout[-1500:]}"
    )


def test_a_present_scope_that_enumerates_to_zero_files_is_a_scope_bug():
    """In-scope-but-nothing-scanned must not read as clean.

    Distinct from "nothing in scope at all": a consumer whose corpus directory is
    absent is a legitimate no-op, while a present directory that enumerates to
    zero files means the enumeration is broken.
    """
    assert lint_skills.SKILL_DIRS, "no corpus scope declared"
    root = lint_skills.SKILL_DIRS[0]
    root = root if root.is_absolute() else lint_skills._REPO_ROOT / root
    assert root.exists(), "precondition: the declared scope is present"
    assert lint_skills.corpus_files(), "present scope enumerated to zero files"


# ── the pattern set must not leave a same-class escape hatch ──────────────────

@pytest.mark.parametrize("token", [
    "GUI21",   # a private-store prefix that shipped in the corpus unflagged
    "GUI122",  # the widest value the live store actually holds
    "OT01",
    "KD06",
    "MOD01",
    "PIT197",
    "C100",    # the counter climbs past 099; a fixed two-digit tail expires silently
    "C151",
    "O100",
])
def test_register_shapes_that_previously_escaped_are_caught(token):
    """Every prefix in the private stores, at every width they actually reach.

    Each of these resolves only in a gitignored store, so each is the same failure
    as the ones already rejected. Three prefixes shipped in the published corpus
    unflagged, and the digit-width caps would have expired the check as the live
    counters advanced — an escape the corpus could regrow through.
    """
    assert lint_skills.REGISTER_RE.search(token), f"{token} escapes the register check"


@pytest.mark.parametrize("token", [
    "SP800-53",   # a NIST publication; this corpus carries security content
    "SP500",
    "C10",        # an ordinary spreadsheet cell, and this corpus documents spreadsheets
    "B5",
    "A1",
    "COE2024",    # a year, not a register entry
    "DEC2024",
    "RP47",       # package-local, resolvable by every reader — must stay
    "RP81",
    "OP1",
])
def test_ordinary_documentation_tokens_are_not_flagged(token):
    """The other direction, and the one that decides whether the gate survives.

    A gate that rejects correct documentation gets removed by whoever needs to ship,
    and a removed gate protects nothing. So over-reach is not a cosmetic problem —
    it is the same fail-open as a missing pattern, reached from the other side.
    """
    m = lint_skills.REGISTER_RE.search(token)
    assert not m, f"{token} is ordinary documentation but was flagged as {m.group() if m else ''}"


@pytest.mark.parametrize("path", [
    "/Users/Gawan/Desktop/x",
    "/Users/Alice/repo",
    "/home/gawan/.swarm-ai",
])
def test_owner_paths_are_caught_regardless_of_case_or_platform(path):
    """A person's home directory is the exposure, not the spelling of it."""
    assert lint_skills.OWNER_PATH_RE.search(path), f"{path} escapes the owner-path check"


@pytest.mark.parametrize("path", [
    "/Users/jdoe/project",
    "/Users/someone/repo",
    "/home/user/app",
    "/Users/JDOE/project",   # the same placeholder, capitalised
    "/Users/Someone/repo",
])
def test_neutral_placeholder_paths_stay_allowed(tmp_path, path):
    """A sample command needs a plausible path; a placeholder is documentation.

    Paired with the test above on purpose: widening the pattern is only correct if
    it did not also start rejecting the practice it is meant to encourage.

    Drives ``lint_body`` on a real file rather than re-applying the exemption
    logic here. An earlier version compared ``owner.lower()`` itself, which meant
    deleting the very ``.lower()`` under test left the suite green — the test
    reimplemented the behaviour instead of observing it.
    """
    f = tmp_path / "sample.md"
    f.write_text(f"Run it like: `mount --path {path}`\n", encoding="utf-8")
    assert lint_skills.OWNER_PATH_RE.search(path), "precondition: the shape is recognised"
    assert lint_skills.lint_body(f) == [], f"{path} is a placeholder and must not be flagged"


# ── the cleanup must have rewritten, not deleted ──────────────────────────────

def test_the_pattern_library_still_carries_a_lesson_in_every_row():
    """Anti-gutting: the cheapest way to pass this linter is to delete the evidence.

    The pattern library is the densest carrier of provenance in the corpus, so it
    is where a silent gutting would hide. Every ``| RP`` row must still end in a
    non-empty cell.
    """
    lib = lint_skills._REPO_ROOT / "backend/skills/s_autonomous-pipeline/REVIEW_PATTERNS.md"
    rows = [l for l in lib.read_text(encoding="utf-8").splitlines() if l.startswith("| RP")]
    assert len(rows) >= 80, f"pattern rows dropped to {len(rows)}"
    hollow = [r[:60] for r in rows if not r.rstrip().rstrip("|").rsplit("|", 1)[-1].strip()]
    assert hollow == [], f"rows left with an empty final cell: {hollow}"


def test_no_row_substitutes_a_hollow_word_for_its_evidence():
    """A placeholder in an evidence column asserts provenance while carrying none.

    Worse than an empty cell, which is at least honest about being empty. This is
    the shape the cleanup itself first produced: stripping an identifier out of an
    evidence column left the column with nothing to say, and filling it with a
    reassuring word made the emptiness invisible. The correct move was to drop the
    column.

    Scoped to that specific failure. ``N/A`` is deliberately NOT flagged — it is
    real content in a criteria table ("no scripts", "not a correction"), and
    rejecting it would fail correct documentation.
    """
    hollow = {"measured", "see above", "internal", "as noted"}
    offenders = []
    for path in lint_skills.corpus_files():
        if path.suffix.lower() != ".md":
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for lineno, line in enumerate(lines, 1):
            if not line.lstrip().startswith("|"):
                continue
            # A header naming a column "Measured" is a label, not a cell asserting
            # its own evidence. Judge body rows only, identified by the separator
            # row that follows every markdown header. Skipping this filter would
            # redden the build on the first correctly-named column.
            def _is_separator(s: str) -> bool:
                body = s.strip().strip("|").replace("|", "").strip()
                # An empty string is not a separator. Treating it as one would make
                # the last row of every table read as a header.
                return bool(body) and set(body) <= set("-: ")

            following = lines[lineno] if lineno < len(lines) else ""
            if _is_separator(following):
                continue  # this line is a header
            if _is_separator(line):
                continue  # this line is the separator itself
            cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
            if any(c in hollow for c in cells):
                offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], f"hollow evidence cells: {offenders[:10]}"


def test_listed_but_unreadable_files_block_rather_than_report_clean(monkeypatch):
    """Counting LISTINGS instead of READS reopens the hole from the other side.

    git can legitimately list a path that is not on disk — ``--skip-worktree``, a
    non-cone sparse checkout, a partially materialised clone. If the zero-file
    guard counts what git listed rather than what was actually read, then "listed
    two, read none" satisfies the guard and prints a clean verdict over a corpus
    nobody opened. This is the same collapse the guard exists to prevent, reached
    from the other direction, and the partial form is worse than the total one: a
    nonzero count makes the success line look credible.
    """
    class _Listed:
        stdout = "backend/skills/s_absent/SKILL.md\0backend/skills/s_absent/other.md\0"

    monkeypatch.setattr(lint_skills.subprocess, "run", lambda *a, **k: _Listed())
    with pytest.raises(lint_skills.CorpusEnumerationError) as exc:
        lint_skills.corpus_files()
    assert "could not be read as a file" in str(exc.value), (
        f"a different guard fired: {exc.value}"
    )


def test_a_corpus_of_only_binary_payloads_is_not_a_scope_bug(tmp_path, monkeypatch):
    """The mirror case, which must NOT block: nothing readable, but nothing wrong.

    Binary payloads are skipped by design because they carry no prose to judge. A
    scope containing only those has been fully examined, so it is a clean no-op —
    blocking here would make the gate impossible to adopt for such a consumer.
    """
    skill = tmp_path / "backend/skills/s_bin"
    skill.mkdir(parents=True)
    (skill / "logo.png").write_bytes(b"\x89PNG\r\n\x00\x00binary")

    class _Listed:
        stdout = "backend/skills/s_bin/logo.png\0"

    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills.subprocess, "run", lambda *a, **k: _Listed())
    assert lint_skills.corpus_files() == []


def test_metadata_pass_blocks_when_its_glob_finds_nothing(tmp_path, monkeypatch):
    """The metadata pass has the same two zero-outcomes, and must split them too.

    Pass 1 globs the filesystem while pass 2 enumerates via git. A present corpus
    directory holding no ``s_*/SKILL.md`` means the glob is broken, not that the
    corpus is empty — and printing a clean verdict there is the same collapse the
    body pass already guards.

    Pass 2 is stubbed to succeed. Without that stub this test passed for the WRONG
    reason: a bare tmp directory is not a git repository, so pass 2 raised first and
    returned 1 no matter what pass 1 did, and the assertion held with the metadata
    guard deleted. Verified by mutation, not assumed.
    """
    (tmp_path / "backend/skills").mkdir(parents=True)
    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills, "corpus_files", lambda: [])
    assert lint_skills.main() == 1


def test_an_absent_corpus_directory_is_a_clean_no_op(tmp_path, monkeypatch):
    """The adoptability direction: a consumer need not carry a skill corpus.

    Blocking here would make the gate impossible to adopt for any repository that
    has no corpus, and a gate too strict to adopt gets deleted — which protects
    nothing. Paired with the test above on purpose: the two zero-outcomes must
    route differently, and asserting only one of them proves neither.
    """
    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills, "corpus_files", lambda: [])
    assert lint_skills.main() == 0


def test_an_empty_git_listing_over_a_present_scope_blocks(tmp_path, monkeypatch):
    """The total-blindness guard needs its own test, not a real-repo smoke check.

    An earlier version asserted only that ``corpus_files()`` is truthy against the
    REAL repository, which stays true whether the guard exists or not — the guard
    could be deleted and every test still passed. Verified by mutation, not assumed.
    """
    (tmp_path / "backend/skills").mkdir(parents=True)

    class _Empty:
        stdout = ""

    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills.subprocess, "run", lambda *a, **k: _Empty())
    with pytest.raises(lint_skills.CorpusEnumerationError):
        lint_skills.corpus_files()


def test_partial_blindness_blocks_not_only_total_blindness(tmp_path, monkeypatch):
    """Listed-many-read-few must block too, and for the reason the comment gives.

    A sparse or partial checkout materialises some of what git lists. An
    all-or-nothing guard is satisfied by "listed three, read one" and then prints a
    nonzero count, which is the credible-looking success line — worse than the total
    form, which at least prints zero.
    """
    skills = tmp_path / "backend/skills/s_one"
    skills.mkdir(parents=True)
    (skills / "present.md").write_text("clean prose\n", encoding="utf-8")

    class _Listed:
        stdout = ("backend/skills/s_one/present.md\0"
                  "backend/skills/s_one/absent_a.md\0"
                  "backend/skills/s_one/absent_b.md\0")

    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills.subprocess, "run", lambda *a, **k: _Listed())
    with pytest.raises(lint_skills.CorpusEnumerationError) as exc:
        lint_skills.corpus_files()
    # Pin WHICH guard fired. Without this the test passes on the reconciliation
    # check downstream, so deleting the partial-blindness guard left it green —
    # a test satisfied by a different code path than the one it names.
    assert "could not be read as a file" in str(exc.value), (
        f"a different guard fired: {exc.value}"
    )


def test_a_listed_path_that_is_not_a_regular_file_is_reported_not_skipped(tmp_path, monkeypatch):
    """A directory or a symlink-to-directory must not vanish from the scan silently.

    ``is_file()`` is false for both, and skipping without accounting means a
    violation hidden behind one is never read and never reported. Blocking is the
    only safe reading: git tracks blobs, so a listed path that is not a regular file
    means the working tree disagrees with the index.
    """
    skills = tmp_path / "backend/skills/s_one"
    skills.mkdir(parents=True)
    (skills / "adir").mkdir()
    (skills / "ok.md").write_text("clean\n", encoding="utf-8")

    class _Listed:
        stdout = "backend/skills/s_one/ok.md\0backend/skills/s_one/adir\0"

    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills.subprocess, "run", lambda *a, **k: _Listed())
    with pytest.raises(lint_skills.CorpusEnumerationError) as exc:
        lint_skills.corpus_files()
    assert "could not be read as a file" in str(exc.value), (
        f"a different guard fired: {exc.value}"
    )


@pytest.mark.parametrize("token", ["A1:C200", "C200", "C300", "GS1", "O365"])
def test_spreadsheet_and_barcode_shapes_are_not_flagged(token):
    """Undisclosed false positives an adversary found, in a corpus about spreadsheets.

    A spreadsheet range and a short GS1 id are ordinary documentation. Rejecting them
    makes the gate un-adoptable for the very skills that need it, and an un-adoptable
    gate gets deleted.

    ``GS001`` is deliberately NOT here: it is character-for-character a real
    identifier's shape, so the module documents it as an accepted collision with the
    per-site allowlist as its escape hatch. Asserting it clean would contradict the
    code, which is its own failure class.
    """
    m = lint_skills.REGISTER_RE.search(token)
    assert not m, f"{token} is ordinary documentation but matched {m.group() if m else ''}"


def test_a_hollow_phrase_in_prose_is_caught_not_only_a_hollow_cell():
    """The hollow-evidence check must judge prose, not only whole table cells.

    Restating "a measured case" without saying WHAT was measured asserts provenance
    while carrying none — the same failure as the placeholder cell, just wearing a
    sentence. Cell-equality alone lets it through.
    """
    offenders = []
    for path in lint_skills.corpus_files():
        if path.suffix.lower() != ".md":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.lower()
            if "a measured case" in low and ":" not in low.split("a measured case")[1][:3]:
                offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], f"hollow provenance phrases: {offenders[:10]}"


def test_a_text_file_wearing_a_binary_suffix_is_still_scanned(tmp_path, monkeypatch):
    """Trusting the extension is an escape hatch; the skip must be content-verified.

    A file named ``.pdf`` that actually holds prose would otherwise be skipped
    unread, so an identifier could ship inside one. The skip is now decided by a NUL
    byte in the first block, not by the name.
    """
    skills = tmp_path / "backend/skills/s_one"
    skills.mkdir(parents=True)
    (skills / "notes.pdf").write_text("Really prose: run_deadbeef1 and C047.\n", encoding="utf-8")

    class _Listed:
        stdout = "backend/skills/s_one/notes.pdf\0"

    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills.subprocess, "run", lambda *a, **k: _Listed())
    files = lint_skills.corpus_files()
    assert files, "a text file with a binary suffix must reach the body pass"
    assert lint_skills.lint_body(files[0]), "its identifiers must be reported"


def test_a_real_binary_is_still_skipped(tmp_path, monkeypatch):
    """The paired direction: genuine binary payloads must stay out of the scan.

    Without this, the sniff could be tightened into scanning every asset and
    producing garbage matches — the over-reach that makes a gate un-adoptable.
    """
    skills = tmp_path / "backend/skills/s_one"
    skills.mkdir(parents=True)
    (skills / "logo.png").write_bytes(b"\x89PNG\r\n\x00\x00 run_deadbeef1")

    class _Listed:
        stdout = "backend/skills/s_one/logo.png\0"

    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills.subprocess, "run", lambda *a, **k: _Listed())
    assert lint_skills.corpus_files() == []


def test_utf16_prose_under_a_binary_suffix_is_still_scanned(tmp_path, monkeypatch):
    """A NUL byte is not the binary tell for UTF-16 — it is every other byte.

    Sniffing for NUL declares UTF-16 text binary and skips it unread, which is the
    exact escape the content check was added to close, reopened by the check itself.
    The ASCII case alone did not cover it.
    """
    skills = tmp_path / "backend/skills/s_one"
    skills.mkdir(parents=True)
    (skills / "notes.pdf").write_bytes("See run_bd42b58f and C047.".encode("utf-16"))

    class _Listed:
        stdout = "backend/skills/s_one/notes.pdf\0"

    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills.subprocess, "run", lambda *a, **k: _Listed())
    files = lint_skills.corpus_files()
    assert files, "UTF-16 prose wearing a binary suffix must reach the body pass"
    assert lint_skills.lint_body(files[0]), "its identifiers must be reported"


def test_a_nul_free_binary_is_still_skipped(tmp_path, monkeypatch):
    """The paired direction: not every real binary carries a NUL in its first block.

    A JPEG whose header happens to be NUL-free would be scanned as prose and emit
    garbage findings on compressed bytes — the over-reach that gets a gate deleted.
    """
    skills = tmp_path / "backend/skills/s_one"
    skills.mkdir(parents=True)
    (skills / "img.jpg").write_bytes(b"\xff\xd8\xff\xe0 run_deadbeef1 C047 no nul here")

    class _Listed:
        stdout = "backend/skills/s_one/img.jpg\0"

    monkeypatch.setattr(lint_skills, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(lint_skills, "SKILL_DIRS", [Path("backend/skills")])
    monkeypatch.setattr(lint_skills.subprocess, "run", lambda *a, **k: _Listed())
    assert lint_skills.corpus_files() == []


@pytest.mark.parametrize("path", [
    "/home/runner/work/repo/x",
    "/home/circleci/project",
    "/home/ubuntu/app",
    "/home/node/app",
    "/Users/Shared/data",
])
def test_ci_and_system_home_paths_are_not_flagged(tmp_path, path):
    """A published corpus that documents CI must not be blocked by documenting it.

    These are not a person's home directory — they are the standard runner and
    system accounts. Flagging them makes the gate un-adoptable for exactly the
    repositories most likely to install it, and an un-adoptable gate gets deleted.
    """
    f = tmp_path / "ci.md"
    f.write_text(f"The runner checks out to `{path}`.\n", encoding="utf-8")
    assert lint_skills.lint_body(f) == [], f"{path} is a runner/system account, not an owner"


def test_a_real_owner_home_is_still_flagged_on_linux(tmp_path):
    """The paired direction, so the CI exemption cannot swallow the real exposure."""
    f = tmp_path / "leak.md"
    f.write_text("cd /home/gawan/.swarm-ai && ./run.sh\n", encoding="utf-8")
    assert lint_skills.lint_body(f), "a named person's Linux home must still be flagged"

def test_shipped_ddd_skill_templates_are_in_scope():
    """The ddd-skills template tree ships MORE publicly than backend/skills.

    ``swarm_workspace_manager`` provisions ``backend/templates/ddd-skills/s_ddd-*``
    into every user DDD and exports it to Kiro / Claude Code, and its own comment
    block names it "EXTERNAL (tracked, public)". A tree that is copied onto other
    people's machines is exactly what the body rule exists to protect, so leaving
    it out of scope is a hole, not a narrower check: the first version of this gate
    scanned only ``backend/skills`` while 36 unresolvable identifiers sat in the
    template tree, unflagged.

    Asserts the scope declaration itself, because a gate is only as wide as the
    tree it reads, and that width is invisible from a green run.
    """
    declared = {d.as_posix() for d in lint_skills.SKILL_DIRS}
    assert "backend/templates/ddd-skills" in declared, (
        "the publicly-provisioned ddd-skills template tree is not in SKILL_DIRS — "
        f"declared scope is {sorted(declared)}"
    )


def test_ddd_skill_templates_carry_no_machine_local_identifiers():
    """End-to-end: the real template tree must pass the real body rule.

    Distinct from the scope assertion above — that one proves the tree is READ,
    this one proves what is read is CLEAN. Both are needed: a tree can be in scope
    and dirty, or clean and unscanned, and only one of those two failures is
    visible from the gate's exit code.
    """
    root = lint_skills._REPO_ROOT / "backend/templates/ddd-skills"
    if not root.exists():
        pytest.skip("template tree absent in this checkout")
    tracked = subprocess.run(
        ["git", "-C", str(lint_skills._REPO_ROOT), "ls-files", "-z",
         "--", "backend/templates/ddd-skills"],
        capture_output=True, text=True, check=True,
    ).stdout.split("\0")
    findings: list[str] = []
    for rel in tracked:
        if not rel:
            continue
        findings.extend(lint_skills.lint_body(lint_skills._REPO_ROOT / rel))
    assert not findings, "template tree carries machine-local identifiers:\n" + "\n".join(findings)
