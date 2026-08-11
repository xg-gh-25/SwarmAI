"""Structural tests for the context-file templates in ``backend/context/``.

Scope is deliberately narrow: these tests guard the templates' EXISTENCE and
their ownership labelling, plus the memory-distill skill's machine-read
frontmatter. They do NOT assert prose wording.

Why no prose assertions (2026-08-11)
------------------------------------
This module used to assert ~70 exact substrings lifted from the templates
("## CRITICAL: Continuity", "Files > Brain", "You're not a chatbot", "Good:",
"getting to know a person", ...). Not one of those strings is read by anything
in production — grepping ``backend/**/*.py`` for them returns only this test
file. The templates are prose the AGENT reads, and they are rewritten
constantly, so every edit broke a test that had never caught a defect. The
dependency ran backwards: the document served the test.

The same reasoning already retired the hardcoded token-budget tests that used to
live here (2026-03-23: "System-default files evolve frequently; static
thresholds produced false failures without catching real regressions"). It
applies verbatim to prose substrings; it just was not generalised at the time.

Worse, several substring tests had rotted into tautologies. When content moved
out of STEERING.md into AGENT.md, five tests named ``test_*steering*`` were
re-pointed at AGENT.md and weakened until they passed — ending up asserting
that a governance document contains the string "MEMORY.md". They could not
fail, and their names misreported what they covered.

Where the real invariant lives
------------------------------
"The agent never boots without its constitution" is enforced where it belongs:
``context_directory_loader.required_prompt_sections()`` (the SSOT) feeding
``prompt_builder.assert_core_sections()`` — a line-anchored completeness gate
that runs against the ASSEMBLED prompt at runtime and fails loud. It is driven
by the spec list rather than by wording, so prose edits cannot break it and a
genuinely missing section cannot slip past it.

Testing methodology: direct file reads, parametrized off the ``CONTEXT_FILES``
SSOT so adding or re-classifying a context file is covered automatically.

Key invariants:
- Every file in ``CONTEXT_FILES`` ships a readable, non-empty template.
- Ownership labelling never LIES: a system-owned template (edits destroyed on
  next startup) says so; a runtime-owned one never claims to be a system default.
- The memory-distill skill exists and declares the name its loader resolves.
"""
import pytest
from pathlib import Path

from core.context_directory_loader import CONTEXT_FILES

TEMPLATES_DIR = Path(__file__).parent.parent / "context"
SKILLS_DIR = Path(__file__).parent.parent / "skills"

# The only marker with a real consequence attached: it declares that edits to
# this file are destroyed on the next startup.
SYSTEM_DEFAULT_MARKER = "⚙️ SYSTEM DEFAULT"

_SYSTEM_OWNED = [s.filename for s in CONTEXT_FILES if s.user_customized is False]
_RUNTIME_OWNED = [s.filename for s in CONTEXT_FILES if s.user_customized is True]


def _read_template(filename: str) -> str:
    """Read a template file and return its content."""
    path = TEMPLATES_DIR / filename
    assert path.is_file(), f"Template {filename} not found at {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Templates exist and are not empty
# ---------------------------------------------------------------------------

class TestTemplatesExist:
    """A file listed in the SSOT must ship a real template behind it."""

    @pytest.mark.parametrize("spec", CONTEXT_FILES, ids=lambda s: s.filename)
    def test_template_is_readable_and_not_empty(self, spec):
        """An emptied or deleted template is the one failure at this layer with
        real blast radius: ``ensure_directory()`` would provision a blank file
        and the agent would boot missing that slice of its context, silently.

        Deliberately a non-empty check — not a size, section, or wording check.
        The templates are meant to be rewritten freely; the only thing that must
        never happen is one going to zero.
        """
        content = _read_template(spec.filename)
        assert content.strip(), (
            f"{spec.filename} (priority {spec.priority}, section "
            f"'{spec.section_name}') has an empty template — the agent would "
            "boot with this part of its context blank"
        )


# ---------------------------------------------------------------------------
# Ownership labelling must not contradict the spec
# ---------------------------------------------------------------------------

class TestOwnershipLabelling:
    """The marker is the only in-file signal telling a reader whether their edits
    survive a restart. Nothing in production parses it — ``ensure_directory()``
    decides overwrite-vs-preserve from ``CONTEXT_FILES``, and
    ``context_directory_loader`` says at :710 that the markers "are useful for
    human editors". So presence for its own sake is not the invariant worth
    testing; what matters is that a marker never CONTRADICTS the spec, because a
    mislabelled file either destroys edits the reader believed were safe or
    invites edits into a file that gets overwritten on the next startup.

    Note: a MISSING marker is tolerated (SELF.md carries none today). A missing
    label is a documentation gap; a wrong label is a lie, and only the lie can
    cost someone their work.
    """

    @pytest.mark.parametrize("filename", _SYSTEM_OWNED)
    def test_system_owned_declares_itself(self, filename: str):
        content = _read_template(filename)
        assert SYSTEM_DEFAULT_MARKER in content, (
            f"{filename} is user_customized=False — always overwritten from this "
            f"template and chmod 0o444 — but carries no {SYSTEM_DEFAULT_MARKER} "
            "marker, so a reader has no way to know their edits will be lost"
        )

    @pytest.mark.parametrize("filename", _RUNTIME_OWNED)
    def test_runtime_owned_does_not_claim_system_default(self, filename: str):
        content = _read_template(filename)
        assert SYSTEM_DEFAULT_MARKER not in content, (
            f"{filename} is user_customized=True — copy-only-if-missing, the "
            f"workspace copy is the source of truth — but carries the "
            f"{SYSTEM_DEFAULT_MARKER} marker, telling the reader their edits "
            "will be destroyed when they will actually persist"
        )


# ---------------------------------------------------------------------------
# memory-distill skill: only what the loader machine-reads
# ---------------------------------------------------------------------------

class TestDistillationSkill:
    """MEMORY.md distillation is invoked as a named skill, so exactly two things
    here are machine-read and worth asserting: the file sits at the path the
    skill loader scans, and its frontmatter declares the name that resolves.

    The body is instructions for the agent (detection thresholds, archiving
    windows, the locked_write requirement) and is NOT asserted. Those rules are
    enforced by the code that implements them — asserting that the words appear
    in a markdown file proves nothing about whether the behaviour holds.
    """

    SKILL_PATH = SKILLS_DIR / "s_memory-distill" / "SKILL.md"

    def test_skill_file_exists(self):
        assert self.SKILL_PATH.is_file(), (
            f"memory-distill skill not found at {self.SKILL_PATH} — the skill "
            "loader scans this path, so a move or rename silently disables it"
        )

    def test_frontmatter_declares_loader_name(self):
        content = self.SKILL_PATH.read_text(encoding="utf-8")
        assert content.startswith("---"), (
            "SKILL.md must open with YAML frontmatter — the loader parses it"
        )
        assert "name: memory-distill" in content, (
            "frontmatter must declare the name the skill loader resolves; "
            "changing it unregisters the skill without any other error"
        )
