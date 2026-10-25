"""Lint Pipeline v1 stage cards against protocol symbols.

Methodology: scan only the nine v1 cards; every named check and command must exist.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.run_protocol.checks import CHECKS

ROOT = Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline" / "stages"
EXPECTED = {"evaluate", "think", "plan", "build", "goal_cycle", "review", "test", "deliver", "reflect"}


def test_v1_cards_are_complete_small_and_reference_real_checks():
    cards = {name: ROOT / f"{name}.md" for name in EXPECTED}
    for name, path in cards.items():
        text = path.read_text(encoding="utf-8")
        assert len(text) < 2000, name
        for check_id in re.findall(r"\b(?:RQ|DS|BX|RV|DC|FX|TS|DV|LG|QX|MD|RF)-\d+[a-z]?\b", text):
            assert check_id in CHECKS, (name, check_id)
        assert "pipeline-v1" in text or name in {"build"}, name


def test_cards_never_mention_deleted_reviewers_or_legacy_commands():
    text = "\n".join((ROOT / f"{name}.md").read_text(encoding="utf-8")
                     for name in EXPECTED)
    for deleted in ("gate1-skeptic", "meta-review", "fixspec-skeptic", "root-cause",
                    "run-update --stage-json", "advance --run-id", "probe:", " -- sh"):
        assert deleted not in text


# ── E: commands and field names the cards name must exist ───────────────────

SKILL_DIR = ROOT.parent
DOCS = [ROOT / f"{name}.md" for name in sorted(EXPECTED)] + [
    SKILL_DIR / "INSTRUCTIONS.md", SKILL_DIR / "SKILL.md"]
_FILL = (("<P>", "P"), ("<R>", "run_x"), ("<project>", "P"), ("<run_id>", "run_x"),
         ("<message>", "m"), ("<json>", "{}"), ("<request-id>", "r1"), ("<id>", "r1"),
         ("<canonical-id>", "F1"), ("<repo>", "repo"), ("<name>=<path>", "repo=/tmp/r"),
         ("<mode>", "tdd"), ("<files>", "a.py"), ("<profile>", "trivial"),
         ("<name>=<absolute-main-repo>", "repo=/tmp/r"), ("\"<the user's request>\"", "r"),
         ("...", ""))


def _code_spans(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", text)


def test_every_pipeline_v1_command_in_the_cards_parses():
    import shlex
    import sys
    from core.run_protocol import cli_main as pipeline_v1
    parser = pipeline_v1.parser()
    seen = 0
    for doc in DOCS:
        for span in _code_spans(doc.read_text(encoding="utf-8")):
            word = "pipeline-v1 " if "pipeline-v1 " in span else None
            if word is None:
                continue
            argv = span.split(word, 1)[1]
            for old, new in _FILL:
                argv = argv.replace(old, new)
            argv = re.sub(r"\[([^\]]*)\]", r"\1", argv)     # [--optional] shown as optional
            words = shlex.split(argv)
            if not any(w.startswith("--") for w in words):
                # a bare subcommand mention: it must be a real subcommand
                assert words and words[0] in parser._subparsers._group_actions[0].choices, (
                    doc.name, span)
                continue
            try:
                parser.parse_args(words)
            except SystemExit:
                raise AssertionError(f"{doc.name}: `{span}` does not parse")
            seen += 1
    assert seen >= 5


def test_every_field_name_in_the_cards_exists_in_the_protocol():
    """A backticked snake_case name (`budget_seconds`, `reflect: degraded`'s key) must
    appear in the protocol code: a renamed field leaves no stale card behind."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in
                       [*(ROOT.parents[2] / "core" / "run_protocol").glob("*.py"),
                        ROOT.parents[2] / "core" / "pipeline_profiles.py"])
    missing = []
    for doc in DOCS:
        for span in _code_spans(doc.read_text(encoding="utf-8")):
            key = span.split(":", 1)[0].strip()
            if re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", key) and key not in source:
                missing.append((doc.name, key))
    assert missing == []


def _options(parser) -> set[str]:
    import argparse
    out = {o for a in parser._actions for o in a.option_strings}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                out |= _options(sub)
    return out


def test_every_option_in_the_cards_exists_and_no_card_names_the_runner():
    """E (review): a standalone `--flag` span names an option of the pipeline CLI; a
    direct create always carries --requirement. S6-1: the pipeline no longer runs tests,
    so no card or skill doc names the old runner or its modes."""
    import sys
    from core.run_protocol import cli_main as pipeline_v1
    known = _options(pipeline_v1.parser())
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        assert "pipeline_test" not in text and "--mode" not in text, doc.name
        for span in _code_spans(text):
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", span):
                assert flag in known, (doc.name, span, flag)
            if "pipeline-v1 create" in span and "--profile direct" in span:
                assert "--requirement" in span, (doc.name, span)


# ── moved from the retired test_advance_continuation.py (R6) ─────────────────

def test_reflect_card_gives_no_completion_command():
    """REFLECT is the daemon's bounded background work (D1): the card gives the
    agent no completion command, only how to see the result."""
    stages = Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline" / "stages"
    text = (stages / "reflect.md").read_text(encoding="utf-8")
    assert "reflect-complete" not in text and "reflect-exhausted" not in text
    assert "pipeline-v1 status" in text and "degraded" in text


def test_the_stop_hook_stays_passive():
    """No end_turn interception: the Stop hook's input carries only
    ``stop_hook_active``, so it cannot tell a stall from a real stop."""
    src = (Path(__file__).resolve().parents[1] / "core" / "hook_builder.py").read_text(
        encoding="utf-8")
    idx = src.index("def _stop_hook")
    body = src[idx: src.index('registry.register("Stop"', idx)]
    assert '"decision": "approve"' in body
    assert '"block"' not in body and "continue_" not in body
    reg = src[src.index('registry.register("Stop"', idx):][:200]
    assert "block" not in reg.lower()
