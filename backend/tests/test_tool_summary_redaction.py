"""Credential redaction of every displayed tool string (chat tool rows, pill, previews).

What is tested: the three display exits of ``core.tool_summarizer`` —
``summarize_tool_use`` (summary, any tool), ``summarize_tool_result`` (outcome line)
and ``truncate_tool_result`` (content preview) — mask every credential shape that
``core.credential_patterns`` knows, plus ``curl``/``wget -u user:pass``.

Properties pinned:
- AC1: each shape is masked at each exit, for Bash and non-Bash tools alike.
- AC2: ordinary text (commands, paths, plain URLs, SHAs, ``docker -u 1000:1000``,
  grep patterns naming ``token``) comes back byte-identical.
- AC3: redaction runs before the cut on a bounded window — a secret across the
  200 / 120 / 500 cut never shows its first part, a value cut at the window edge
  is not pulled into view when earlier redactions shrink the text, and a
  multi-megabyte result stays fast.
- AC4: one authority — channel egress (one-shot and streaming) and the summarizer
  share ``core.credential_patterns``, and importing the summarizer does not load
  the channel gateway / DB stack.
- Sub-agent summary / description on the Agent row go through the same redaction.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.tool_summarizer import (
    MAX_SUMMARY_LENGTH,
    redact_display,
    summarize_tool_result,
    summarize_tool_use,
    truncate_tool_result,
)

GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
AKIA = "AKIAIOSFODNN7EXAMPLE"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
BACKEND = Path(__file__).resolve().parents[1]


# --- AC1: every shape, every exit -------------------------------------------

@pytest.mark.parametrize(
    "name,data,secret",
    [
        ("Bash", {"command": f"curl -H 'Authorization: Bearer {GHP}' https://api.x.com"}, GHP),
        ("Bash", {"command": "curl -u admin:hunter2secret https://api.x.com"}, "hunter2secret"),
        ("Bash", {"command": "wget --user=bob:pa55w0rdz https://x.com/f"}, "pa55w0rdz"),
        ("Bash", {"command": f"export AWS_ACCESS_KEY_ID={AKIA} && aws s3 ls"}, AKIA),
        ("WebFetch", {"url": "https://bob:s3cretpass@github.com/x/y"}, "s3cretpass"),
        ("WebFetch", {"url": "https://api.x.com/v1?token=abcdef1234567890"}, "abcdef1234567890"),
        ("Grep", {"pattern": AKIA}, AKIA),
        ("Bash", {"command": "curl -H 'Authorization: Basic YWRtaW46aHVudGVyMg==' https://x"}, "YWRtaW46aHVudGVyMg=="),
        ("Bash", {"command": "deploy --token=ab!cd#efghij"}, "ab!cd#efghij"),
        # *_TOKEN env vars: '_' is a word char, so a leading \b would miss them.
        ("Bash", {"command": "export AWS_SESSION_TOKEN=IQoJb3JpZ2luX2VjEJr//wEaCX && aws s3 ls"}, "IQoJb3JpZ2luX2VjEJr"),
        ("Bash", {"command": "export HF_TOKEN=hf_AbCdEfGhIjKlMn"}, "hf_AbCdEfGhIjKlMn"),
        ("Bash", {"command": "npm config set //registry.npmjs.org/:_authToken=npm_abcdef123456"}, "npm_abcdef123456"),
        ("Bash", {"command": "curl -d refresh_token=1//0gAbcdefghij https://oauth2"}, "1//0gAbcdefghij"),
        ("Bash", {"command": "curl -s \\\n  -u admin:hunter2secret https://x"}, "hunter2secret"),
        ("Bash", {"command": "curl --user admin:'hunter2secret' https://x"}, "hunter2secret"),
        ("Bash", {"command": 'curl --user "admin:p@ss w0rd" https://x'}, "w0rd"),
        # '&' / ';' inside a quoted URL or header do not end the curl command.
        ("Bash", {"command": 'curl "https://api.x.com/v1?a=1&b=2" -u admin:hunter2secret'}, "hunter2secret"),
        ("Bash", {"command": 'curl -H "X: a;b" -u admin:hunter2secret https://x'}, "hunter2secret"),
        ("Bash", {"command": "export DB_PWD=hunter2secret"}, "hunter2secret"),
        # The flag shape needs no curl word next to it: subshells, quoted remote
        # commands, a path-invoked binary, combined short flags, prose apostrophes.
        ("Bash", {"command": "TOKEN=$(curl -s -u admin:hunter2secret https://x/api)"}, "hunter2secret"),
        ("Bash", {"command": "echo `curl -u admin:hunter2secret x`"}, "hunter2secret"),
        ("Bash", {"command": 'ssh host "curl -u admin:hunter2secret https://x"'}, "hunter2secret"),
        ("Bash", {"command": "bash -c 'curl -u admin:hunter2secret https://x'"}, "hunter2secret"),
        ("Bash", {"command": "/usr/bin/curl -u admin:hunter2secret x"}, "hunter2secret"),
        ("Bash", {"command": "curl -su admin:hunter2secret https://x"}, "hunter2secret"),
        ("Bash", {"command": "curl --proxy-user admin:hunter2secret https://x"}, "hunter2secret"),
        ("Bash", {"command": "curl -U proxyu:hunter2secret https://x"}, "hunter2secret"),
        ("Bash", {"command": "curl -u admin:84920175 https://router"}, "84920175"),
        ("Bash", {"command": "curl -u 'admin':hunter2secret x"}, "hunter2secret"),
        ("Bash", {"command": "curl -uadmin:hunter2secret x"}, "hunter2secret"),
        # short secrets: no length floor (HEAD masked these; a floor unmasked them)
        ("Bash", {"command": "mysql --password=hunter2"}, "hunter2"),
        ("Bash", {"command": "PGPASSWORD=abc123 psql"}, "abc123"),
        ("Bash", {"command": "export GITHUB_TOKEN=abc1234"}, "abc1234"),
        ("Bash", {"command": "MYSQL_PWD=p4ss mysql"}, "p4ss"),
        ("Bash", {"command": "curl -s -u \\\n  admin:hunter2secret https://x"}, "hunter2secret"),
        ("mcp__slack__post", {"query": "Here's how: curl -u admin:hunter2secret https://x and that's it"}, "hunter2secret"),
        ("mcp__srv__call_api", {"query": f"auth {JWT}"}, JWT),
    ],
)
def test_summary_masks_shape_for_any_tool(name, data, secret):
    out = summarize_tool_use(name, data)
    assert secret not in out
    assert "[REDACTED]" in out


def test_summary_keeps_user_name_and_command_readable():
    out = summarize_tool_use("Bash", {"command": "curl -u admin:hunter2secret https://api.x.com"})
    assert out == "Running: curl -u admin:[REDACTED] https://api.x.com"


@pytest.mark.parametrize("is_error", [False, True])
def test_outcome_line_masks_bearer(is_error):
    # Bash success shows the LAST line, an error the FIRST: one line carries the
    # header so each branch reads it.
    text = f"Authorization: Bearer {GHP}\n" if is_error else f"HTTP/1.1 200 OK\nAuthorization: Bearer {GHP}\n"
    out = summarize_tool_result("Bash", text, is_error)
    assert GHP not in out
    assert "[REDACTED]" in out


def test_outcome_line_masks_non_bash_fallback():
    out = summarize_tool_result("mcp__srv__get", f"token={GHP} ok", False)
    assert GHP not in out


def test_preview_masks_shapes():
    content = f"config:\n  url: https://bob:s3cretpass@host/x\n  key: {AKIA}\n  jwt: {JWT}\n"
    out, truncated = truncate_tool_result(content, limit=500)
    for secret in ("s3cretpass", AKIA, JWT):
        assert secret not in out
    assert truncated is False


# --- AC2: ordinary text is untouched ----------------------------------------

BENIGN = [
    "git log -6 --format='%h %ad %s'",
    "docker run -u 1000:1000 -v /tmp:/tmp img",
    'grep -n "token: " backend/core/a.py',
    "echo token=$TOKEN",
    "ls -la ~/.swarm-ai/SwarmWS/Projects",
    "curl https://docs.python.org/3/library/re.html",
    "git show 3f915c52a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "echo aGVsbG8gd29ybGQ= | base64 -d",
    "echo 'Basic configuration is in settings.yaml'",
    "docker run -v $PWD:/app img",
]

# Paths, variable references and commands that must stay as written. (Code that
# assigns a non-trivial value to a secret-named key is masked on purpose: see
# test_code_preview_masks_value_keeps_key.)
BENIGN_PREVIEW = [
    "self.password = password",
    "connect(password=password)",
    "PWD=/Users/x/proj",
    "curl --user-agent=Foo:Bar https://x",
    "sort -u a.txt",
    "rsync -avu deploy@web1:/var/www/app/ ./backup/",
    "rsync -au host:/data/logs /tmp/logs",
    "docker run -u $(id -u):$(id -g) img",
    "curl -u user:$PASS https://x",
    "git log -u HEAD~1:file.py",
    "curl -u admin:`cat /tmp/pw` x",
]


@pytest.mark.parametrize(
    "text,secret",
    [
        ('SLACK_TOKEN = "abcdefgh12345678"', "abcdefgh12345678"),
        ("DB_PASSWORD = 'hunter2hunter2'", "hunter2hunter2"),
        ('password = "hunter22"', "hunter22"),
        ("token = abcdef123456", "abcdef123456"),
    ],
)
def test_quoted_or_digit_literal_masked_even_with_spaces(text, secret):
    out, _ = truncate_tool_result(text, limit=500)
    assert secret not in out


@pytest.mark.parametrize("text", BENIGN_PREVIEW)
def test_benign_code_preview_unchanged(text):
    assert truncate_tool_result(text, limit=500) == (text, False)


def test_code_preview_masks_value_keeps_key():
    # Decision: no "is this code?" guessing (every such heuristic unmasked a real
    # secret shape). A masked value in a code preview is the accepted cost.
    out, _ = truncate_tool_result("def f(password: Optional[str] = None):", limit=500)
    assert out == "def f(password: [REDACTED] = None):"
    out, _ = truncate_tool_result("connect(password=pw)", limit=500)
    assert out == "connect(password=[REDACTED])"


@pytest.mark.parametrize(
    "cmd,secret",
    [
        ("mysql -u root --password='P@ss(w0rd)' db", "P@ss(w0rd)"),
        ("export DB_PASSWORD='Tr0ub4dor&{3}'", "Tr0ub4dor"),
        ("mysql --password='$ecret99'", "ecret99"),
        ("psql 'password=p<ss1 host=x'", "p<ss1"),
        ("echo password = hunter", "hunter"),
        ("curl -d 'user=bob,password=hunter'", "hunter"),
    ],
)
def test_symbol_and_spaced_secrets_masked(cmd, secret):
    assert secret not in summarize_tool_use("Bash", {"command": cmd})


def test_ini_outcome_line_masked():
    out = summarize_tool_result("Bash", "[client]\nuser = root\npassword = changeme", False)
    assert "changeme" not in out and out.startswith("password = ")


def test_path_with_gh_prefix_word_unchanged():
    path = "/Users/x/code/ghost-blog-theme-starter/src/index.js"
    assert summarize_tool_use("Read", {"file_path": path}) == f"Reading {path}"


def test_key_name_stays_readable():
    out = summarize_tool_use("Bash", {"command": "curl -H 'X-Api-Key: abcd1234efgh5678' https://x"})
    assert out == "Running: curl -H 'X-Api-Key: [REDACTED]' https://x"


def test_json_credentials_masked_in_preview():
    content = (
        '{"Credentials": {"AccessKeyId": "ASIAABCDEFGHIJKLMNOP", '
        '"SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", '
        '"SessionToken": "IQoJb3JpZ2luX2VjEJrabc"}}'
    )
    out, _ = truncate_tool_result(content, limit=500)
    for secret in ("ASIAABCDEFGHIJKLMNOP", "wJalrXUtnFEMI", "IQoJb3JpZ2lu"):
        assert secret not in out
    assert '"SecretAccessKey": "[REDACTED]"' in out


@pytest.mark.parametrize("cmd", BENIGN)
def test_benign_command_unchanged(cmd):
    assert summarize_tool_use("Bash", {"command": cmd}) == f"Running: {cmd}"


@pytest.mark.parametrize("text", BENIGN)
def test_benign_preview_unchanged(text):
    assert truncate_tool_result(text, limit=500) == (text, False)


def test_counts_outcomes_unchanged():
    assert summarize_tool_result("Bash", "collecting\n\n12 passed in 3.1s\n", False) == "12 passed in 3.1s"
    assert summarize_tool_result("Grep", "a.py:1:x\nb.py:9:y\n", False) == "2 matches"


# --- AC3: redact before the cut, bounded window ------------------------------

def test_summary_secret_across_cut_never_half_shown():
    cmd = "echo " + "x" * (MAX_SUMMARY_LENGTH - 20) + " " + GHP
    out = summarize_tool_use("Bash", {"command": cmd})
    assert len(out) <= MAX_SUMMARY_LENGTH
    assert "ghp_" not in out and "A1b2" not in out


def test_outcome_secret_across_cut_never_half_shown():
    line = "y" * 110 + " " + GHP
    out = summarize_tool_result("Bash", line, False)
    assert len(out) <= 120
    assert "ghp_" not in out


def test_preview_secret_across_cut_never_half_shown():
    content = "z" * 490 + " " + GHP + " tail"
    out, truncated = truncate_tool_result(content, limit=500)
    assert truncated is True and len(out) <= 500
    assert "ghp_" not in out


def test_window_edge_value_not_pulled_into_view_by_shrink():
    # Seven long tokens shrink by ~660 chars when redacted; a token cut at the
    # window edge (only 12 chars inside, below the 16-char minimum) would then
    # land inside the 500-char preview, half visible.
    limit, slack = 500, 512
    head = "a " + " ".join("ghp_" + "b" * 100 for _ in range(7)) + " "
    edge_value = "ghp_" + "c" * 40
    pad = "p " * ((limit + slack - 12 - len(head)) // 2)
    content = head + pad + edge_value + " more text after"
    assert content.index(edge_value) + 12 <= limit + slack
    out, _ = truncate_tool_result(content, limit=limit)
    assert "ghp_c" not in out and "cccc" not in out


def test_unterminated_private_key_masked():
    body = "\n".join("MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" * 2 for _ in range(60))
    content = "-----BEGIN RSA PRIVATE KEY-----\n" + body + "\n-----END RSA PRIVATE KEY-----\n"
    out, truncated = truncate_tool_result(content, limit=500)
    assert truncated is True
    assert "MIIEvQ" not in out


def test_multi_megabyte_result_fast_and_masked():
    content = (f"line {GHP}\n") * 160_000  # ~5 MB
    start = time.perf_counter()
    out, truncated = truncate_tool_result(content, limit=500)
    outcome = summarize_tool_result("Bash", content, False)
    elapsed = time.perf_counter() - start
    assert truncated is True
    assert GHP not in out and GHP not in outcome
    assert elapsed < 1.0, elapsed


def test_redact_display_empty_and_short():
    assert redact_display("", 10) == ""
    assert redact_display("abc", 10) == "abc"


# --- AC4: one authority, no channel stack in core ----------------------------

def test_channel_egress_shares_the_user_pass_shape():
    from channels.egress_redactor import redact_credentials as egress_redact, redact_text
    from core.credential_patterns import redact_credentials

    assert egress_redact is redact_credentials
    assert redact_text("run: curl -u admin:hunter2secret https://x") == (
        "run: curl -u admin:[REDACTED] https://x"
    )
    assert redact_text("docker run -u 1000:1000 img") == "docker run -u 1000:1000 img"


@pytest.mark.parametrize(
    "chunks",
    [
        ["Run: curl -u ", "admin:hunter2secret https://x.com done ", "more"],
        ["Ran: curl -u ", "admin:", "hunter2", "secret", " https://x\n"],
        ["curl -u admin", ":hunter2", "secret https://x\nnext line"],
        ["curl -s \\", "\n  -u admin:hunter2secret https://x\n"],
        ['curl "https://a?b=1&c=2" -u ', "admin:hunter2secret x\n"],
        # the password as the LAST token of its line / of the stream
        ['curl "https://a.com/x" -u ', "admin:hunter2", "secret\n"],
        ["curl https://x -u admin:hunter", "2secret"],
        # a quoted password with a space, split inside the quotes
        ['curl --user "admin:hun', 'ter2 secret" https://x\n'],
        # the quote opens AFTER user: and the password has a space
        ["Run: curl -u admin:'hun", "ter2 secret' https://x\n"],
        ["curl -u admin:'hunter2 ", "secret' https://x\n"],
        # a backslash-newline between the flag and the value (split on and after it)
        ["a curl -u \\", "\nadmin:hunter2secret y"],
        ["curl --user \\\n", " admin:hunter2secret y\n"],
    ],
)
def test_stream_redactor_masks_user_pass_split_across_chunks(chunks):
    from channels.egress_redactor import StreamRedactor

    r = StreamRedactor()
    out = "".join(r.feed(c) for c in chunks) + r.flush()
    assert "hunter2" not in out and "secret\"" not in out
    assert "admin:[REDACTED]" in out


@pytest.mark.parametrize("gap", [280, 295, 298, 300, 305, 320])
def test_stream_equals_one_shot_around_long_curl_lines(gap):
    from channels.egress_redactor import StreamRedactor, redact_text

    full = "curl " + "x" * gap + " -u admin:hunter2secret https://x\nnext"
    want = redact_text(full)
    assert "hunter2secret" not in want
    for size in (1, 3, 7):
        r = StreamRedactor()
        got = "".join(r.feed(full[i:i + size]) for i in range(0, len(full), size)) + r.flush()
        assert got == want, (gap, size)


def test_curl_dense_text_is_linear():
    # A curl-word-anchored gap regex was quadratic here (40 KB of "curl " took
    # 0.7 s on the synchronous egress path); the flag shape is linear.
    from channels.egress_redactor import redact_text

    for text in ("curl " * 80_000, "-u a:b " * 60_000, "-u '" * 60_000, "-uuuuuuu" * 50_000,
                 "--user=" * 40_000, "/-u" * 40_000, "-U=" * 40_000):
        start = time.perf_counter()
        redact_text(text)
        assert time.perf_counter() - start < 1.0


def test_stream_long_flag_token_stays_fast():
    # A withheld no-whitespace token full of flag starts: the user:pass opener and
    # its pull-back scan only a bounded window. (The older per-feed scans of
    # StreamRedactor stay O(buffer); this pins that the -u shape adds no quadratic.)
    from channels.egress_redactor import StreamRedactor

    text = "--user=" * 2000
    r = StreamRedactor()
    start = time.perf_counter()
    for i in range(0, len(text), 40):
        r.feed(text[i:i + 40])
    r.flush()
    assert time.perf_counter() - start < 3.0


def test_agent_prompt_secret_across_cut_never_half_shown():
    out = summarize_tool_use("Agent", {"prompt": "x" * 110 + f" {GHP} tail"})
    assert "ghp_" not in out and "A1b2" not in out


def test_subagent_summary_and_description_redacted():
    from claude_agent_sdk import TaskNotificationMessage, TaskStartedMessage
    from core.streaming_orchestrator import StreamingOrchestrator

    parents: dict = {}
    started = TaskStartedMessage(
        subtype="TaskStartedMessage", data={}, uuid="u", session_id="s", task_id="t1",
        description=f"deploy with token={GHP}", tool_use_id="toolu_agent",
    )
    done = TaskNotificationMessage(
        subtype="TaskNotificationMessage", data={}, uuid="u", session_id="s", task_id="t1",
        status="completed", output_file="", tool_use_id="toolu_agent",
        summary=f"used Authorization: Bearer {GHP}",
    )
    ev1 = StreamingOrchestrator._subagent_progress_event(started, parents)
    ev2 = StreamingOrchestrator._subagent_progress_event(done, parents)
    assert GHP not in ev1["description"]
    assert GHP not in ev2["summary"] and "[REDACTED]" in ev2["summary"]


def test_importing_summarizer_does_not_load_channel_stack():
    probe = (
        "import sys; import core.tool_summarizer; "
        "print(any(m == 'channels' or m.startswith('channels.') for m in sys.modules))"
    )
    res = subprocess.run(
        [sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True, timeout=30
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "False"
