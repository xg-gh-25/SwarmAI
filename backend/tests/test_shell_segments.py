"""core/shell_segments — the C041 splitter, shared (design 2026-10-26-session-products.md §2.2).

Pins the splitting rules the irreversible-op gate already relies on, so a second
reader (turn_products.remote_action) and the gate cannot drift: separators and
newlines split, a glued ';' splits, quoted text stays one token, env assignments and
wrapper words are skipped, git global options are skipped, unparseable → None.
"""
from core.shell_segments import command_tokens, git_subcommand_index, split_segments


def test_separators_newlines_and_glued_semicolons_split():
    assert split_segments("git status && git push -f") == [["git", "status"], ["git", "push", "-f"]]
    assert split_segments("a\nb | c") == [["a"], ["b"], ["c"]]
    assert split_segments("git status; gh repo delete x") == [["git", "status"], ["gh", "repo", "delete", "x"]]


def test_quoted_text_keeps_spaces_but_a_quoted_semicolon_still_splits():
    # C041's existing rule (kept as is): shlex strips quotes before the glued-';'
    # pass, so a ';' inside a quoted message splits too. At worst the message is
    # judged as two harmless segments.
    assert split_segments('git commit -m "a b && c"') == [["git", "commit", "-m", "a b && c"]]
    assert split_segments('git commit -m "a; b"') == [["git", "commit", "-m", "a"], [" b"]]


def test_unparseable_is_none_and_empty_is_empty():
    assert split_segments("echo 'unterminated") is None
    assert split_segments("") == []


def test_command_tokens_skips_env_and_wrappers():
    assert command_tokens(["GH_TOKEN=x", "sudo", "gh", "pr", "create"]) == ["gh", "pr", "create"]
    assert command_tokens(["--flag=1", "x"]) == ["--flag=1", "x"]


def test_git_subcommand_index_skips_global_options():
    t = ["git", "-C", "/r", "-c", "k=v", "--git-dir=/g", "push", "origin"]
    assert t[git_subcommand_index(t)] == "push"
    assert git_subcommand_index(["git"]) >= 1
