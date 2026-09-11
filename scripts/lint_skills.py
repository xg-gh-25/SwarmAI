#!/usr/bin/env python3
"""Lint the shipped skill corpus: SKILL.md metadata, and body content policy.

Two independent passes.

METADATA (every ``s_*/SKILL.md``)
    Uses ``parse_frontmatter()`` from ``core.skill_manager`` — the same parser the
    runtime uses — so format changes never cause linter/runtime disagreement.
    Catches a name that does not match its folder, a name that is not lowercase
    (the SDK matches case-sensitively), a missing required field, malformed YAML.

BODY (every tracked file in the corpus)
    Rejects content that resolves only on the authoring machine:

      * a pipeline run id, or one of the small register prefixes this project uses
        for its private cognitive stores;
      * an absolute path naming a specific machine owner.

    This corpus is published. A rule whose stated justification is a token the
    reader cannot resolve loses its authority at the moment it is enforced — and
    the authoring machine cannot resolve most of them either, once the referenced
    run directories are cleaned up. Write the lesson so the sentence stands on its
    own; an identifier adds nothing a reader can use.

    **Where the patterns live.** The run-id pattern is IMPORTED from
    ``core.ingestion_gate``, which is its single source of truth — the same shape
    the cognitive-store ingestion door judges, so the two cannot drift. The
    register and owner-path patterns have no counterpart there, so **this module is
    their single source of truth**; a consumer that needs them should import from
    here rather than re-deriving them.

    **Why there is no trailing-provenance exemption here.** The cognitive-store
    rule deliberately permits an identifier in a trailing ``(…, run_xxx)``
    parenthetical, because that store has exactly one reader and they can resolve
    it. A published artifact has neither property, so the exemption does not carry
    over. The scope difference is READER scope, not an inconsistency to unify.

    **Enforcement scope.** This pass covers the skill corpus. Occurrences elsewhere
    in the backend are not cleaned yet; the governance rule covers them, and this
    gate keeps the corpus from regrowing.

Run from repo root (CI runs after backend deps are installed):
    python scripts/lint_skills.py

Exit code 0 = clean, 1 = at least one finding.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# Add backend/ to sys.path so we can import core.skill_manager
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from core.ingestion_gate import _SHAPE_BODY_RUNID_RE  # noqa: E402
from core.skill_manager import SkillParseError, parse_frontmatter  # noqa: E402

# Every tree of skills this repo PUBLISHES. The gate is only as wide as the trees
# it reads, and that width is invisible from a green run — so a tree that ships must
# be listed here, not assumed covered.
#
# `templates/ddd-skills` is not an afterthought: `swarm_workspace_manager` provisions
# it into every user DDD and exports it to Kiro / Claude Code, so it lands on other
# people's machines even more directly than `backend/skills` does. Its own source
# comment classifies it "EXTERNAL (tracked, public)". Scanning only `backend/skills`
# left 33 unresolvable identifiers shipping from the template tree, unflagged, while
# the gate reported a clean corpus. (36 tokens matched; three were the entry-id
# format specimens exempted below, so 33 were real leaks.)
#
# A directory absent from a given checkout is a clean no-op (see `corpus_files`), so
# listing a tree here never blocks a consumer that does not carry it.
SKILL_DIRS = [
    Path("backend/skills"),
    Path("backend/templates/ddd-skills"),
]

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The run-id shape, shared with the cognitive-store door so the two cannot drift.
RUNID_RE = _SHAPE_BODY_RUNID_RE

# Register prefixes used by this project's private stores. Every prefix that
# appears in a gitignored store belongs here — a missing one is not a narrower
# check, it is a hole the corpus regrows through, and three of them (GUI, OT, KD)
# shipped published and unflagged before this list was completed.
#
# Every width below is MEASURED against the live stores, not guessed, because both
# directions of error are real. Too narrow and a private identifier ships (three
# prefixes did). Too wide and the gate rejects correct documentation — and a gate
# too strict to adopt gets deleted, which protects nothing.
#
# Measured: C/O/GS are zero-padded three digits; PIT and GUI run two to three; the
# rest are two. A single-letter prefix gets NO two-digit floor, because that
# collides with an ordinary spreadsheet cell reference (``Sheet1!C10``) and this
# corpus documents spreadsheets. Nothing gets four digits — that only ever matched
# a year (``COE2024``) or a large cell.
#
# ``SP`` and ``COR`` are deliberately ABSENT. Neither appears anywhere in the live
# stores, so including them bought no coverage while flagging ``SP800-53`` and
# ``SP500`` — a security corpus cites NIST publications in exactly that shape.
#
# The three-digit prefixes are anchored to a leading 0 or 1. Every value in the
# live stores is zero-padded (highest is in the 0xx range), so this keeps headroom
# to 199 — far past the current counters — while no longer matching a spreadsheet
# range like ``A1:C200``, which an adversary found flagged in a corpus that
# documents spreadsheets.
#
# One false positive remains, accepted rather than fixed: ``GS001`` (a GS1 barcode
# family id) is character-for-character the shape of a real identifier, so no
# pattern can separate them; narrowing further would drop live coverage to buy
# nothing, and the string occurs nowhere in the corpus. If it ever legitimately
# appears, the per-site allowlist below is the escape hatch — which is exactly what
# it exists for. ``O365`` no longer matches, as a side effect of the 0-or-1 anchor.
#
# Package-local registers (``RP``-numbered rows that ship inside a skill and
# resolve within it) are NOT here: they resolve for every reader of the package
# and must stay.
REGISTER_RE = re.compile(
    r"\b(?:(?:C|O|GS)[01][0-9]{2}"
    r"|(?:PIT|GUI)[0-9]{2,3}"
    r"|(?:LL|COE|GC|DEC|OT|KD|MOD)[0-9]{2})\b"
)

# An absolute home path naming a specific person, on either platform. Case
# matters: an account name is just as exposing capitalised, and anchoring to
# lowercase left ``/Users/Gawan`` passing. ``/home/`` is the Linux twin of the
# same exposure.
OWNER_PATH_RE = re.compile(r"/(?:Users|home)/[A-Za-z][A-Za-z0-9_.-]*")

# A neutral placeholder is documentation, not an exposure. Compared
# case-insensitively, since the pattern above accepts either case.
#
# Two groups, both non-exposing. The first is sample names a doc uses to show a
# plausible path. The second is CI-runner and system accounts, which are NOT a
# person's home directory: a corpus that documents its own CI writes
# ``/home/runner/work`` as a fact about the platform, and flagging that would block
# adoption for exactly the repositories most likely to install this gate — the
# un-adoptability this module warns about, arrived at from the strict side.
_PLACEHOLDER_OWNERS = frozenset({
    # sample names
    "jdoe", "someone", "user", "dev", "x", "you", "me", "specific",
    # CI runners and system accounts
    "runner", "circleci", "ubuntu", "node", "jenkins", "travis", "buildkite",
    "vsts", "gitpod", "codespace", "vscode", "shared", "library", "root",
})

# Binary payloads carry no prose to judge and would produce garbage matches.
_BINARY_SUFFIXES = frozenset({
    ".mp3", ".mp4", ".wav", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf", ".otf", ".pyc", ".so",
})

# Narrow, reviewed exemptions. Keyed on BOTH the repo-relative path AND the exact
# matched text, so an entry can never widen to a whole file or a whole pattern
# class. Every entry states why. ``test_every_allowlist_entry_still_matches_something``
# fails when an entry goes stale, so a deleted site cannot leave a permanent hole.
# Every entry here is a FORMAT SPECIMEN, not a citation: the skill's job is to
# parse or write entries of that exact shape, so the literal in its comment IS the
# contract. Rewriting it would make the comment disagree with the format the code
# actually matches — the lying-comment failure this project already has a rule for.
BODY_ALLOWLIST: dict[str, set[str]] = {
    # A CLI help string documenting an entry-id FORMAT. The value is a sample for
    # the user to imitate, not a citation.
    "backend/skills/s_persist/scripts/locked_write.py": {"O001"},
    # Comments quoting the on-disk entry shapes this checker parses.
    "backend/skills/s_loops-health/scripts/loops_health_check.py": {"C037", "DEC01"},
    # Parser comments quoting the header shape and the cross-reference phrasings
    # the regexes below them match.
    "backend/skills/s_steeringify/steeringify.py": {"C007", "C008", "C012"},
    # The same phrasings, documented for the reader of the skill.
    "backend/skills/s_steeringify/INSTRUCTIONS.md": {"C005", "C007"},
    # The portable twin of s_persist above, provisioned into user DDDs. Same
    # reason and same shape: an entry-id sample its ``### {entry_id} |`` parser
    # matches and its CLI help tells the user to imitate. Only ``O001`` is listed
    # even though the same lines also read "E001, F001": those two match no pattern
    # here, so exempting them would exempt nothing while reading as though it did.
    "backend/templates/ddd-skills/s_ddd-persist/scripts/locked_write.py": {"O001"},
}


def _allow_key(path: Path) -> str:
    """Repo-relative POSIX path — the allowlist key.

    Deliberately does NOT ``resolve()``: a corpus entry can be a symlink pointing
    outside the corpus, and resolving would key the exemption to the link's target
    instead of the path a reviewer sees in the tree. The enumerated paths already
    come from git, so they are inside the repo by construction.
    """
    try:
        return path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


class CorpusEnumerationError(RuntimeError):
    """The gate could not determine its own scope, so it must not report a verdict.

    Raised rather than swallowed on purpose. Silently continuing yields an empty
    file list, and an empty list reads as a clean corpus — the check "could not
    run" collapsed into the check "found nothing", which is the exact pattern this
    repository's own review checklist rejects. `git` missing from the image, a
    `dubious ownership` refusal on a mounted checkout, and a timeout are all real
    CI conditions, and each one previously printed a tick and exited 0.
    """


def _is_really_binary(path: Path) -> bool:
    """Confirm by CONTENT that a binary-suffixed file carries no prose to judge.

    Trusting the extension alone is an escape hatch: a text file named ``.pdf``
    would be skipped unread, so an identifier could ship inside one.

    The discriminator is DECODABILITY, not a NUL byte. A NUL sniff is wrong in both
    directions and was measured wrong in both: UTF-16 prose is NUL-dense, so it was
    declared binary and skipped unread — reopening the very hole this check closes —
    while a JPEG whose header happens to be NUL-free was scanned as prose and emitted
    garbage findings. Text is text if some text codec decodes it; that is the property
    the body pass actually depends on.

    Only regular files are sniffed, and the read is bounded. A FIFO would otherwise
    block forever on ``open`` with no writer, which is worse than a wrong verdict
    because CI hangs instead of failing. Anything unopenable or unreadable is NOT
    declared binary: it returns False and reaches ``lint_body``, which reports a
    read failure and so exits non-zero rather than passing silently.
    """
    if not path.is_file():
        return False
    try:
        head = path.open("rb").read(8192)
    except OSError:
        return False
    if not head:
        return False
    # Decode with a BOM-aware codec first. UTF-16/32 prose is NUL-dense, so a bare
    # NUL sniff calls it binary and skips it unread; but a short binary header also
    # happens to decode under utf-16, so decodability alone calls a real PNG text.
    # The BOM is what separates them: declared-encoding text carries one, arbitrary
    # binary does not.
    for bom, codec in ((b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"),
                       (b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32")):
        if head.startswith(bom):
            try:
                head.decode(codec, errors="strict")
            except UnicodeDecodeError:
                # A truncated 8KB window can split a surrogate pair; the BOM already
                # settled that this is text.
                pass
            return False
    if b"\x00" in head:
        return True  # NUL outside declared-encoding text is the binary tell
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return True  # neither BOM-declared text nor valid UTF-8
    return False


def corpus_files() -> list[Path]:
    """Every tracked corpus file worth reading, enumerated by git.

    Git rather than a filesystem walk, for two measured reasons: vendored
    dependencies under the corpus are untracked, and a walk would scan them and
    redden the build on code this repository does not own; and an extension
    allowlist would have missed real findings in shell scripts, letting a green
    build ship a dirty corpus.

    Two zero-file outcomes are deliberately NOT the same thing. A declared scope
    that is ABSENT is a legitimate no-op — a consumer repository need not carry a
    skill corpus. A declared scope that is PRESENT but enumerates to nothing is a
    broken enumeration, and blocks. Collapsing the two into one boolean would
    either strand honest consumers or wave through a blind gate.

    Raises:
        CorpusEnumerationError: git could not be consulted, or a present scope
            yielded no files.
    """
    out: list[Path] = []
    for skill_dir in SKILL_DIRS:
        root = skill_dir if skill_dir.is_absolute() else _REPO_ROOT / skill_dir
        if not root.exists():
            continue  # nothing in scope here — a real no-op, not a blind pass
        try:
            if os.environ.get("SWARM_LINT_FORCE_ENUM_FAILURE"):
                # Test seam: the fail-closed path needs an execution test, and a
                # mocked raise cannot drive the real process end to end.
                raise subprocess.SubprocessError("forced enumeration failure")
            listed = subprocess.run(
                ["git", "-C", str(_REPO_ROOT), "ls-files", "-z", "--", skill_dir.as_posix()],
                capture_output=True, text=True, timeout=60, check=True,
            ).stdout
        except (subprocess.SubprocessError, OSError) as e:
            raise CorpusEnumerationError(
                f"cannot enumerate {skill_dir.as_posix()} via git: {e}"
            ) from e
        # Every listed path must end up in exactly ONE bucket, and the buckets are
        # reconciled below. Silently dropping a path is how a violation hidden
        # behind it is never read and never reported.
        # Every listed path lands in exactly one bucket, so the three counts are
        # exhaustive by construction and no separate reconciliation check is
        # possible — an earlier version had one, and it was a tautology that could
        # never fire, sitting behind a comment claiming it caught dropped paths.
        listed_count = 0
        skipped_binary = 0
        unreadable: list[str] = []
        for rel in listed.split("\0"):
            if not rel:
                continue
            p = _REPO_ROOT / rel
            listed_count += 1
            if p.suffix.lower() in _BINARY_SUFFIXES and _is_really_binary(p):
                skipped_binary += 1  # examined and correctly skipped: no prose
                continue
            if not p.is_file():
                # Absent from disk, or a directory / symlink-to-directory. git
                # tracks blobs, so either way the working tree disagrees with the
                # index and this path was NOT examined.
                unreadable.append(rel)
                continue
            out.append(p)
        if listed_count == 0:
            raise CorpusEnumerationError(
                f"{skill_dir.as_posix()} exists but git listed no files under it — "
                f"the scope is present and the enumeration is broken"
            )
        # PARTIAL blindness, not just total. An all-or-nothing guard is satisfied by
        # "listed three, read one" and then prints a nonzero count — the credible
        # -looking success line, worse than the total form which at least prints
        # zero. A sparse or partial checkout produces exactly this. Binary payloads
        # are NOT unread: they were examined and skipped by design.
        if unreadable:
            raise CorpusEnumerationError(
                f"{skill_dir.as_posix()}: git listed {listed_count} path(s) but "
                f"{len(unreadable)} could not be read as a file "
                f"(e.g. {unreadable[0]}) — the working tree does not match the index"
            )
    return out


def lint_body(path: Path) -> list[str]:
    """Return a finding per machine-local reference in one file (empty = clean).

    Reads BOM-declared UTF-16/32 in its own encoding. Forcing UTF-8 with
    ``errors="replace"`` turns such prose into mojibake, so every pattern misses and
    the file reports clean — enumeration would correctly hand the file over and the
    read would silently drop its content.
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        return [f"{path}: cannot read: {e}"]

    encoding = "utf-8"
    for bom, codec in ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
                       (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            encoding = codec
            break
    text = raw.decode(encoding, errors="replace")

    allowed = BODY_ALLOWLIST.get(_allow_key(path), frozenset())
    findings: list[str] = []

    for lineno, line in enumerate(text.splitlines(), 1):
        for m in RUNID_RE.finditer(line):
            if m.group() not in allowed:
                findings.append(
                    f"{path}:{lineno}: run id '{m.group()}' resolves only on the "
                    f"authoring machine — state the lesson so the sentence stands alone"
                )
        for m in REGISTER_RE.finditer(line):
            if m.group() not in allowed:
                findings.append(
                    f"{path}:{lineno}: register reference '{m.group()}' points at a "
                    f"private store a reader cannot open — restate the lesson inline"
                )
        for m in OWNER_PATH_RE.finditer(line):
            owner = m.group().rsplit("/", 1)[-1].lower()
            if owner in _PLACEHOLDER_OWNERS or m.group() in allowed:
                continue
            findings.append(
                f"{path}:{lineno}: path '{m.group()}' names a specific machine owner "
                f"— use a neutral placeholder or a repo-relative path"
            )
    return findings


def lint_skill(skill_md: Path) -> list[str]:
    """Return list of error strings for a single SKILL.md."""
    errors: list[str] = []
    folder = skill_md.parent.name  # e.g. "s_weather"

    try:
        meta, _body = parse_frontmatter(skill_md)
    except SkillParseError as e:
        return [f"{skill_md}: {e}"]
    except Exception as e:
        return [f"{skill_md}: cannot read: {e}"]

    name = meta.get("name")
    if not name:
        errors.append(f"{skill_md}: missing 'name:' field in frontmatter")
        return errors

    name = str(name)

    # Check lowercase
    if name != name.lower():
        errors.append(
            f"{skill_md}: name '{name}' must be lowercase ('{name.lower()}')"
        )

    # Check matches folder (strip s_ prefix)
    folder_base = folder.removeprefix("s_")
    if name.lower() != folder_base.lower():
        errors.append(
            f"{skill_md}: name '{name}' doesn't match folder '{folder}' "
            f"(expected '{folder_base}')"
        )

    # Check description exists
    if not meta.get("description"):
        errors.append(f"{skill_md}: missing 'description:' field in frontmatter")

    return errors


def main() -> int:
    all_errors: list[str] = []

    # Pass 1 — metadata, one SKILL.md per skill.
    #
    # This pass globs the FILESYSTEM while pass 2 enumerates via GIT, so the two
    # deliberately disagree about what "the corpus" is. An untracked skill is
    # metadata-linted (it must still load at runtime) but never body-linted, which
    # is correct: the body rule exists because the corpus is PUBLISHED, and a file
    # git does not track is never published. Stated here because a silent
    # disagreement between two passes over one tree otherwise reads as a bug.
    meta_count = 0
    scoped = False
    for skill_dir in SKILL_DIRS:
        root = skill_dir if skill_dir.is_absolute() else _REPO_ROOT / skill_dir
        if not root.exists():
            continue  # nothing in scope — a consumer need not carry a corpus
        scoped = True
        for skill_md in sorted(root.glob("s_*/SKILL.md")):
            meta_count += 1
            all_errors.extend(lint_skill(skill_md))
    if scoped and meta_count == 0:
        print("\nskill lint could not run: the corpus directory exists but holds no "
              "s_*/SKILL.md — the glob is broken, not the corpus empty.")
        print("  Blocking rather than reporting a clean corpus it never read.")
        return 1

    # Pass 2 — body content policy, every tracked corpus file. A gate that cannot
    # establish its scope reports that, and blocks; it never prints a verdict it
    # has no evidence for.
    try:
        body_files = corpus_files()
    except CorpusEnumerationError as e:
        print(f"\nskill lint could not run: {e}")
        print("  Blocking rather than reporting a clean corpus it never read.")
        return 1
    for path in body_files:
        all_errors.extend(lint_body(path))

    if all_errors:
        print(f"\n{len(all_errors)} skill lint error(s):")
        for err in all_errors:
            print(f"  ✗ {err}")
        return 1

    print(
        f"{meta_count} SKILL.md files pass metadata checks ✓  ·  "
        f"{len(body_files)} corpus files carry no machine-local references ✓"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
