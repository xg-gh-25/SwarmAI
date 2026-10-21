"""strip_injected_blocks: hooks see only what the user typed (run_1ec5ea9d, AC5).

The shapes stripped are the strings the builders emit (session_router resume
header/footer, the recall block, the UI-state block, task notifications), so a
builder change cannot silently desync the stripper.
"""
from core.injected_blocks import (
    RECALL_HEADER,
    RECALL_HINT,
    RECALL_PROVENANCE,
    RESUME_FOOTER,
    RESUME_HEADER,
    strip_injected_blocks,
)


def _resume(body="## Session Resume\nwe decided X earlier"):
    return f"{RESUME_HEADER}\n\n{body}\n\n{RESUME_FOOTER}"


def _recall(body="### Memory\n- [decision] we decided to always use Y"):
    return f"{RECALL_HEADER}\n{RECALL_PROVENANCE}{body}{RECALL_HINT}"


UI = ("## Current UI State\nThis is what you are currently showing the user in the app "
      "(a request-time snapshot — it may change as they interact):\n- Open file: `a.md`")


def test_builder_blocks_are_removed_user_line_kept():
    prompt = f"{_resume()}\n\n{_recall()}\n\n{UI}\n\n继续"
    assert strip_injected_blocks(prompt) == "继续"


def test_session_router_builders_use_the_same_shapes():
    from core import session_router as sr
    assert sr._RESUME_QUERY_HEADER == RESUME_HEADER
    assert sr._RESUME_QUERY_FOOTER == RESUME_FOOTER


def test_task_notification_removed():
    p = "<task-notification>\n<task-id>x</task-id>\n</task-notification>\nplease check the result"
    assert strip_injected_blocks(p) == "please check the result"


def test_only_system_blocks_leaves_nothing():
    assert strip_injected_blocks(f"{_resume()}\n\n{UI}") == ""


def test_user_text_that_merely_mentions_a_header_is_kept():
    p = "why does the ## Current UI State block show twice?"
    assert strip_injected_blocks(p) == p


def test_plain_prompt_unchanged():
    assert strip_injected_blocks("I decided to use rebase") == "I decided to use rebase"


def test_terminal_block_is_removed_with_what_follows_it():
    from core.prompt_builder import render_terminal_data_block
    from core.injected_blocks import RESUME_FOOTER, RESUME_HEADER
    term = render_terminal_data_block("$ make\nerror: that is wrong ```x```\n")
    resume = f"{RESUME_HEADER}\n\nUser: that is wrong\n\n{RESUME_FOOTER}"
    assert strip_injected_blocks(f"{term}\n\n{resume}\n\nhello there") == "hello there"


def test_keyword_miss_recall_block_is_removed():
    from core.injected_blocks import RECALL_MISS
    assert strip_injected_blocks(f"{RECALL_MISS}\n\n{UI}\n\n继续做") == "继续做"
