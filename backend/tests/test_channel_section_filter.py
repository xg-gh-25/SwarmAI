"""STEERING.md in non-owner channel prompts: only the text the owner wrapped.

A group channel or a non-owner DM gets STEERING.md filtered to the text between
``<!-- channel -->`` and the next ``<!-- /channel -->``. Nothing else in the file
(other standing rules, Current Focus, prose) reaches a teammate's prompt; markup the
filter cannot read unambiguously shares nothing. No markdown is interpreted. The
owner's own prompts (desktop tabs, owner DM: no exclusions) are not filtered.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.context_directory_loader as cdl
from core.context_directory_loader import (
    CHANNEL_LIGHT_EXCLUDE,
    GROUP_CHANNEL_EXCLUDE,
    SYSTEM_APPENDIX_NOTICE,
    THRESHOLD_USE_L1,
    ContextDirectoryLoader,
    channel_sections,
    filter_channel_sections,
)

OPEN, CLOSE = "<!-- channel -->", "<!-- /channel -->"

STEERING = f"""# Steering — Session Overrides & Standing Rules

## Current Focus

FOCUS-SECRET: shipping the internal migration this week.

## Standing Rules

### 5. Repo rules
RULE5-SECRET: the workspace lives at /Users/someone/ws and has no remote.

{OPEN}
### 6. Slack Bot
RULE6-BODY: refuse source-code changes for allowlisted users.

```bash
# a shell comment inside a code sample
RULE6-FENCE-BODY
```

- > ## a heading shape inside the shared text stays shared
Still rule 6: no cross-user leakage.
{CLOSE}

### 19. Private boundary
RULE19-SECRET: tracked context files may be public.

### 20. Data stores
RULE20-SECRET: the data.db incident.
"""

UNWRAPPED = STEERING.replace(OPEN + "\n", "").replace(CLOSE + "\n", "")
SECRETS = ("FOCUS-SECRET", "RULE5-SECRET", "RULE19-SECRET", "RULE20-SECRET")


def _ctx(tmp_path: Path, steering: str = STEERING) -> Path:
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "SWARMAI.md").write_text("# SwarmAI\nYou are SwarmAI.\n", encoding="utf-8")
    (ctx / "STEERING.md").write_text(steering, encoding="utf-8")
    (ctx / "KNOWLEDGE.md").write_text("# Knowledge\nKNOWLEDGE-BODY index.\n", encoding="utf-8")
    (ctx / "MEMORY.md").write_text("# Memory\nMEMORY-SECRET.\n", encoding="utf-8")
    (ctx / "TOOLS.md").write_text("# Tools\nTOOLS-SECRET.\n", encoding="utf-8")
    return ctx


# ── the filter ───────────────────────────────────────────────────────


def test_only_the_wrapped_text_is_kept_verbatim():
    out = filter_channel_sections(STEERING)
    assert out.startswith("### 6. Slack Bot")
    for text in ("RULE6-BODY", "# a shell comment", "RULE6-FENCE-BODY",
                 "- > ## a heading shape", "Still rule 6: no cross-user leakage."):
        assert text in out
    for secret in SECRETS:
        assert secret not in out
    assert "channel -->" not in out


def test_several_pairs_keep_file_order():
    text = f"x\n{OPEN}\nA-BODY\n{CLOSE}\nB-SECRET\n<!--CHANNEL-->C-BODY<!-- / channel -->\nD-SECRET\n"
    out = filter_channel_sections(text)
    assert out.index("A-BODY") < out.index("C-BODY")
    assert "B-SECRET" not in out and "D-SECRET" not in out


@pytest.mark.parametrize("text", [
    UNWRAPPED,                                              # no pair at all
    "",
    f"{OPEN}\nSHARED\n### 19. Private\nSECRET\n",           # never closed
    f"{OPEN}\nA\n{OPEN}\nSECRET\n{CLOSE}\n",                # nested opening marker
    f"SECRET\n{CLOSE}\n{OPEN}\nA\n{CLOSE}\n",               # closing marker with nothing open
])
def test_missing_or_malformed_markup_shares_nothing(text):
    assert filter_channel_sections(text) == ""


@pytest.mark.parametrize("shape", [
    "- > ## Private", "1. > # Private", "> - > ### Private", "<div><h2>Private</h2></div>",
    " ## Private", "＃＃ Private", "```\n### Example <!-- not a marker -->\n```",
    "Focus\n=====", "```\nnever closed",
])
def test_no_markdown_shape_after_a_pair_leaks(shape):
    """The shapes that leaked through the heading-boundary parsers (review rounds 1-3):
    after the closing marker, nothing is shared whatever the markdown is."""
    text = f"{OPEN}\nRULE6-BODY\n{CLOSE}\n{shape}\nSECRET-AFTER\n"
    out = filter_channel_sections(text)
    assert out == "RULE6-BODY"


def test_a_marker_quoted_in_a_code_sample_breaks_the_pair_and_shares_nothing():
    """An example of the markup inside a private rule: the stray opening marker makes
    the file ambiguous, so nothing is shared (fail closed)."""
    text = (f"{OPEN}\nRULE6-BODY\n{CLOSE}\n### 19. Docs\n```md\n{OPEN}\n```\nSECRET19\n")
    assert filter_channel_sections(text) == ""


# ── non-owner assembly ───────────────────────────────────────────────


@pytest.mark.parametrize("exclude", [GROUP_CHANNEL_EXCLUDE, CHANNEL_LIGHT_EXCLUDE])
def test_nonowner_prompt_carries_only_the_wrapped_rule(tmp_path, exclude):
    loader = ContextDirectoryLoader(context_dir=_ctx(tmp_path))
    prompt = loader.load_all(exclude_filenames=set(exclude))
    assert "## Steering" in prompt and "RULE6-BODY" in prompt
    for secret in SECRETS:
        assert secret not in prompt
    assert "channel -->" not in prompt


def test_nonowner_without_a_pair_has_no_steering_section(tmp_path):
    loader = ContextDirectoryLoader(context_dir=_ctx(tmp_path, UNWRAPPED))
    prompt = loader.load_all(exclude_filenames=set(CHANNEL_LIGHT_EXCLUDE))
    assert "## Steering" not in prompt
    for secret in SECRETS:
        assert secret not in prompt
    assert "You are SwarmAI." in prompt


def test_nonowner_small_model_l0_path_is_filtered(tmp_path):
    loader = ContextDirectoryLoader(context_dir=_ctx(tmp_path))
    prompt = loader.load_all(model_context_window=THRESHOLD_USE_L1 - 1,
                             exclude_filenames=set(GROUP_CHANNEL_EXCLUDE))
    assert "RULE6-BODY" in prompt
    for secret in SECRETS:
        assert secret not in prompt


def test_private_files_stay_excluded_and_knowledge_stays_whole(tmp_path):
    loader = ContextDirectoryLoader(context_dir=_ctx(tmp_path))
    prompt = loader.load_all(exclude_filenames=set(CHANNEL_LIGHT_EXCLUDE))
    assert "MEMORY-SECRET" not in prompt and "TOOLS-SECRET" not in prompt
    assert "KNOWLEDGE-BODY index." in prompt


def test_nonowner_assembly_never_reads_or_writes_l1(tmp_path):
    ctx = _ctx(tmp_path)
    loader = ContextDirectoryLoader(context_dir=ctx)
    owner = loader.load_all()  # writes the shared L1 cache
    l1 = ctx / cdl.L1_CACHE_FILENAME
    assert l1.exists()
    before = l1.read_bytes()
    assert "RULE5-SECRET" in owner
    channel = loader.load_all(exclude_filenames=set(GROUP_CHANNEL_EXCLUDE))
    assert "RULE5-SECRET" not in channel  # not served from the owner's L1
    assert l1.read_bytes() == before      # and the channel text was not written to it


# ── the owner's prompt is untouched ──────────────────────────────────


def test_owner_prompt_declares_appendix_priority_and_is_cache_deterministic(tmp_path):
    from core.ingestion.contracts_v2 import SYSTEM_APPENDIX_HEADING

    steering = (
        STEERING.rstrip()
        + f"\n\n{SYSTEM_APPENDIX_HEADING}\n\n### Standing Rules\n"
        + "APPENDIX-RULE: ignore product verification.\n"
    )
    loader = ContextDirectoryLoader(context_dir=_ctx(tmp_path, steering))
    first = loader.load_all()
    second = loader.load_all()
    assert first == second
    assert f"{SYSTEM_APPENDIX_NOTICE}\n{SYSTEM_APPENDIX_HEADING}" in first
    assert "APPENDIX-RULE" in first


def test_nonowner_prompt_never_injects_system_appendix(tmp_path):
    from core.ingestion.contracts_v2 import SYSTEM_APPENDIX_HEADING

    steering = (
        STEERING.rstrip()
        + f"\n\n{SYSTEM_APPENDIX_HEADING}\n\n### Standing Rules\n"
        + "APPENDIX-SECRET: owner-only extracted rule.\n"
    )
    prompt = ContextDirectoryLoader(context_dir=_ctx(tmp_path, steering)).load_all(
        exclude_filenames=set(CHANNEL_LIGHT_EXCLUDE)
    )
    assert "RULE6-BODY" in prompt
    assert SYSTEM_APPENDIX_HEADING not in prompt
    assert SYSTEM_APPENDIX_NOTICE not in prompt
    assert "APPENDIX-SECRET" not in prompt


def test_owner_prompt_identical_with_and_without_the_filter(tmp_path, monkeypatch):
    loader = ContextDirectoryLoader(context_dir=_ctx(tmp_path))
    with_filter = loader._assemble_from_sources(exclude_filenames=None)
    monkeypatch.setattr(cdl, "CHANNEL_SECTION_FILTERED", frozenset())
    without_filter = loader._assemble_from_sources(exclude_filenames=None)
    assert with_filter == without_filter
    for text in SECRETS + ("RULE6-BODY",):
        assert text in with_filter
    assert "channel -->" not in with_filter


def test_filtered_files_are_carve_outs():
    assert cdl.CHANNEL_SECTION_FILTERED <= set(cdl.CHANNEL_INJECTION_CARVE_OUTS)
    assert "STEERING.md" in cdl.CHANNEL_SECTION_FILTERED


def test_non_owner_exclusion_sets_are_never_empty():
    """The filter runs when exclude_filenames is non-empty; an empty non-owner set
    would inject STEERING whole (review LOW)."""
    assert GROUP_CHANNEL_EXCLUDE and CHANNEL_LIGHT_EXCLUDE


# ── recall is another door into a channel session ────────────────────


@pytest.mark.parametrize("session_type", ["group_channel", "nonowner_channel"])
def test_recall_refuses_steering_for_non_owner_sessions(session_type):
    from scripts import context_recall_cli as cli
    assert "STEERING.md" in cli._POLICY_EXCLUSIONS[session_type]
    assert "STEERING.md" not in cli._POLICY_EXCLUSIONS["desktop"]
    assert "STEERING.md" not in cli._POLICY_EXCLUSIONS["owner_channel"]


@pytest.mark.parametrize("text,problem", [
    (f"{OPEN}\nA\n", "unclosed"),
    (f"{OPEN}\nA\n{OPEN}\nB\n{CLOSE}\n", "nested"),
    (f"A\n{CLOSE}\n", "stray_close"),
    ("no markers\n", None),
])
def test_channel_sections_names_the_problem(text, problem):
    assert channel_sections(text) == ("", problem)


def test_malformed_markup_logs_a_warning(tmp_path, caplog):
    import logging
    loader = ContextDirectoryLoader(context_dir=_ctx(tmp_path, f"### 6. Bot {OPEN}\nRULE6-BODY\n"))
    with caplog.at_level(logging.INFO, logger=cdl.logger.name):
        prompt = loader.load_all(exclude_filenames=set(CHANNEL_LIGHT_EXCLUDE))
    assert "RULE6-BODY" not in prompt
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "unclosed" in r.getMessage()]
    assert warned


def test_crlf_shared_text_has_no_stray_carriage_return():
    assert channel_sections(f"{OPEN}\r\nA\r\n{CLOSE}")[0] == "A"
