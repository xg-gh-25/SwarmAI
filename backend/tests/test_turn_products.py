"""core/turn_products — which calls are products and the blocks they make
(design docs/design/2026-10-26-session-products.md §2).

Properties: a product file event (written, product kind) → one file block per path;
source / deleted / pipeline-doc-only events → none; a listed MCP tool (and a listed
action of a dispatch tool) → a remote block with a redacted target; unlisted tools,
unlisted actions, read tools, built-ins → none; Bash recognised through the shared C041
splitter; a URL only from gh pr create's own last line.
"""
from core.turn_products import blocks_for_result, remote_action, remote_candidate


def _fc(ap, kind="external-diff", op="written"):
    return {"type": "file_changed", "path": ap, "absolutePath": ap, "kind": kind, "operation": op}


def test_product_files_one_block_per_path_in_order():
    evs = [_fc("/a/plan.md"), _fc("/a/plan.md"), _fc("/k/n.md", "knowledge"), _fc("/r/x.py", "source"),
           _fc("/d/old.md", "content", "deleted")]
    blocks = blocks_for_result("tu1", evs, None, parent="ag1")
    assert [(b["product"], b["absolutePath"], b["fileKind"]) for b in blocks] == [
        ("file", "/a/plan.md", "external-diff"), ("file", "/k/n.md", "knowledge")]
    assert all(b["type"] == "turn_product" and b["toolUseId"] == "tu1" and b["parentToolUseId"] == "ag1"
               for b in blocks)


def test_listed_mcp_tools_and_actions_are_remote_products():
    c = remote_candidate("mcp__slack-mcp__post_message", {"channel": "#team", "text": "hi"})
    assert c == {"tool": "mcp__slack-mcp__post_message", "action": "Posted to Slack", "target": "#team"}
    t = remote_candidate("mcp__builder-mcp__TicketingWriteActions", {"action": "create-ticket", "title": "Fix it\nmore"})
    assert t["action"] == "Created a ticket" and t["target"] == "Fix it"
    b = blocks_for_result("tu2", [], c)
    assert b == [{"type": "turn_product", "toolUseId": "tu2", "product": "remote",
                  "tool": "mcp__slack-mcp__post_message", "action": "Posted to Slack", "target": "#team"}]


def test_unlisted_reads_builtins_and_actions_are_not_products():
    assert remote_candidate("mcp__slack-mcp__get_thread", {"channel": "#x"}) is None
    assert remote_candidate("mcp__builder-mcp__TicketingWriteActions", {"action": "help"}) is None
    assert remote_candidate("mcp__other-mcp__post_message", {"channel": "#x"}) is None
    assert remote_candidate("Write", {"file_path": "/a"}) is None
    assert remote_candidate("Agent", {"prompt": "x"}) is None


def test_target_is_redacted():
    c = remote_candidate("mcp__slack-mcp__post_message", {"channel": "token=ghp_" + "a" * 36})
    assert "a" * 36 not in c["target"]


def test_bash_remote_actions_use_the_shared_splitter():
    assert remote_action("cd repo && git -C . push origin feat")["action"] == "Pushed"
    assert remote_action("GH_TOKEN=x gh pr create --fill")["action"] == "Opened a pull request"
    assert remote_action("gh issue create -t T")["action"] == "Opened an issue"
    for cmd in ("git status", 'echo "git push"', "gh pr view 3", "git log --oneline", "echo 'x"):
        assert remote_action(cmd) is None, cmd
    assert remote_candidate("Bash", {"command": "git push origin x"})["action"] == "Pushed"


def test_url_only_from_gh_pr_create_last_line():
    c = remote_action("gh pr create --fill")
    out = "Creating pull request\nhttps://github.com/o/r/pull/12\n"
    assert blocks_for_result("t", [], c, out)[0]["url"] == "https://github.com/o/r/pull/12"
    assert "url" not in blocks_for_result("t", [], c, "see https://evil.example/pull/1")[0]
    s = remote_candidate("mcp__slack-mcp__post_message", {"channel": "#x"})
    assert "url" not in blocks_for_result("t", [], s, "https://github.com/o/r/pull/1")[0]
