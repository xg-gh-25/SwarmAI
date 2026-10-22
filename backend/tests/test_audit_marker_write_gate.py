"""Tests for audit_marker_write_gate (core.security_hooks).

The gate denies writes, moves and deletes in state/pipeline_agent_audit, the store
whose markers prove review sub-agents ran (D7, Red Team, Gate-0/1, the adversarial
commit gate). Methodology: HOME and security_hooks._AGENT_AUDIT_DIR point at a temp
tree, the real async hook is driven with asyncio.run, and every forgery form named in
the gate's section comment is a parametrized deny case next to a read that must pass.

Invariants:
  • every write form (redirect, write/copy verbs, interpreter code, ancestor rm, cd
    into the dir, xargs/read loops, command substitution) is denied, by any spelling
    of the path (~, $HOME, absolute, glob, variable, differently cased);
  • reads by absolute path pass, and so do unrelated commands and paths;
  • Write/Edit/MultiEdit/NotebookEdit deny by resolved path, including via symlink;
  • it cannot be forced (gate_force.UNFORCEABLE); an internal error fails open;
  • build_hooks registers the gate for Bash (ahead of session_link_gate and the HITL
    gate) and for the file tools.
"""
import asyncio
import os

import pytest

from core import hook_builder, security_hooks

D = "~/.swarm-ai/state/pipeline_agent_audit"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temp HOME holding the audit dir; the gate's dir constant points into it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    audit = tmp_path / ".swarm-ai" / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True)
    monkeypatch.setattr(security_hooks, "_AGENT_AUDIT_DIR", audit)
    monkeypatch.delenv("SWARM_GATE_FORCE", raising=False)
    return tmp_path


def _run(tool, tool_input, cwd=None):
    gate = security_hooks.create_audit_marker_write_gate({"sdk_session_id": "s1"})
    data = {"tool_name": tool, "tool_input": tool_input}
    if cwd:
        data["cwd"] = cwd
    return asyncio.run(gate(data, "tu1", None))


def _denied(result):
    return (result.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


DENY_COMMANDS = [
    f"echo x > {D}/a.marker",
    'echo x >"$HOME/.swarm-ai/state/pipeline_agent_audit/a.marker"',
    "echo x > ${HOME}/.swarm-ai/state/pipeline_agent_audit/a.marker",
    f"x=1;echo hi>{D}/m",
    f"printf x | tee -a {D}/a.marker",
    f"cat > {D}/m <<'EOF'\nhi\nEOF",
    f"cp /tmp/m {D}/",
    f"cp -t {D} /tmp/m",
    f"rsync -a /tmp/m/ {D}",
    f"mv {D} /tmp/x",
    f"rm -rf {D}",
    "rm -rf ~/.swarm-ai/state",
    "rm -rf ~/.swarm-ai/state/*",
    f"ln -s {D} /tmp/l",
    f"mkdir -p {D}",
    f"dd if=/dev/zero of={D}/m count=1",
    f"tar -xf /tmp/a.tar -C {D}",
    "chmod -R 000 ~/.swarm-ai",
    f"sed -i '' 's/a/b/' {D}/m",
    f"perl -pi -e 's/a/b/' {D}/m",
    f"find {D} -name '*.marker' -delete",
    "find ~/.swarm-ai -name '*.marker' -delete",
    f"find /tmp -name m -exec cp {{}} {D}/ ';'",
    f"D={D}; echo x > $D/m.marker",
    f'export D={D} && touch "$D/m"',
    f'for f in {D}/*; do rm "$f"; done',
    f"ls {D} | xargs rm",
    f"ls -d {D}/* | while read f; do rm $f; done",
    f"xargs -I{{}} cp {{}} {D} < /tmp/list",
    f"sudo -n rm -rf {D}",
    f"env -i rm {D}/m",
    f"timeout 10 rm {D}/m",
    f"python3 - <<'PY'\nimport pathlib\npathlib.Path('{D}').joinpath('m').write_text('x')\nPY",
    f"python3 -c \"open('{D}/m','w').write('x')\"",
    f"python3 -c 'import sys; open(sys.argv[1], \"w\")' {D}/m",
    f"node -e \"require('fs').writeFileSync('{D}/m','x')\"",
    f"bash -c 'echo x > {D}/m'",
    f"bash -o pipefail -c 'rm {D}/m'",
    f'eval "rm -rf {D}"',
    f"echo 'rm -rf {D}' | bash",
    f"echo $(rm -rf {D})",
    f"echo `rm {D}/m`",
    f"cat <(rm {D}/m)",
    f"D={D}; echo $(touch $D/m)",
    f"(cd {D} && touch m)",
    f"cd {D} && ls",
    "cd ~/.swarm-ai && rm -rf state",
    "cd ~/.swarm-ai/state && rm -rf *",
    "echo hi > ~/.swarm-ai/state/PIPELINE_AGENT_AUDIT/m",
]


@pytest.mark.parametrize("command", DENY_COMMANDS)
def test_bash_write_forms_are_denied(home, command):
    result = _run("Bash", {"command": command})
    assert _denied(result), command
    assert "no bypass" in result["hookSpecificOutput"]["permissionDecisionReason"]


ALLOW_COMMANDS = [
    f"cat {D}/*.marker",
    f"ls -la {D}",
    f"jq . {D}/x.marker",
    f"grep -l gate1 {D}/*.marker | head",
    f"wc -l {D}/* > /tmp/count",
    f"cp {D}/x.marker /tmp/",
    "git commit -m 'block writes to pipeline_agent_audit'",
    "python3 backend/scripts/artifact_cli.py run-update --notes 'pipeline_agent_audit gate'",
    "rm -rf /tmp/foo build/*",
    "rm ~/.swarm-ai/state/*.json",
    "echo hi > /tmp/x 2>&1",
    "echo $((1+2)) > /tmp/n",
    "cd ~/.swarm-ai/state && ls && cat x.json > /tmp/y",
    "cd backend && python -m pytest tests/x.py",
    "echo 'unbalanced",
    "",
]


@pytest.mark.parametrize("command", ALLOW_COMMANDS)
def test_reads_and_unrelated_commands_pass(home, command):
    assert not _denied(_run("Bash", {"command": command})), command


@pytest.mark.parametrize("command,rel_cwd", [
    ("echo x > m.marker", ".swarm-ai/state/pipeline_agent_audit"),
    ("rm -rf state", ".swarm-ai"),
    ("cp /tmp/m pipeline_agent_audit/", ".swarm-ai/state"),
])
def test_relative_writes_resolve_against_the_hook_cwd(home, command, rel_cwd):
    assert _denied(_run("Bash", {"command": command}, cwd=str(home / rel_cwd)))


@pytest.mark.parametrize("command,rel_cwd", [
    ("rm -rf build", "proj"),
    ("echo x > out.txt", ".swarm-ai/state"),
])
def test_relative_writes_elsewhere_pass(home, command, rel_cwd):
    assert not _denied(_run("Bash", {"command": command}, cwd=str(home / rel_cwd)))


@pytest.mark.parametrize("tool,key", [
    ("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path"),
    ("NotebookEdit", "notebook_path"),
])
def test_file_tools_into_the_dir_are_denied(home, tool, key):
    target = home / ".swarm-ai" / "state" / "pipeline_agent_audit" / "x.marker"
    assert _denied(_run(tool, {key: str(target)}))
    assert _denied(_run(tool, {key: "~/.swarm-ai/state/pipeline_agent_audit/y.marker"}))


def test_file_tool_through_a_symlink_is_denied(home):
    link = home / "link"
    os.symlink(home / ".swarm-ai" / "state" / "pipeline_agent_audit", link)
    assert _denied(_run("Write", {"file_path": str(link / "x.marker")}))


@pytest.mark.parametrize("path", [
    ".swarm-ai/state/other.json", ".swarm-ai/state/pipeline_agent_audit_notes.md",
])
def test_file_tools_elsewhere_pass(home, path):
    assert not _denied(_run("Write", {"file_path": str(home / path)}))


def test_other_tools_pass(home):
    assert not _denied(_run("Read", {"file_path": f"{D}/x.marker"}))


def test_cannot_be_forced(home, monkeypatch):
    monkeypatch.setenv("SWARM_GATE_FORCE", "audit_marker_write_gate")
    assert _denied(_run("Bash", {"command": f"rm -rf {D}"}))


def test_internal_error_fails_open(home, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("classifier bug")

    monkeypatch.setattr(security_hooks, "_audit_bash_hit", boom)
    monkeypatch.setattr(security_hooks, "_audit_file_tool_hit", boom)
    assert _run("Bash", {"command": f"rm -rf {D}"}) == {"decision": "approve"}
    assert _run("Write", {"file_path": f"{D}/x"}) == {"decision": "approve"}


def test_build_hooks_registers_the_gate(monkeypatch):
    from core.permission_manager import permission_manager

    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        recorded.append((event, name, matcher))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="test-sess", session_context={"sdk_session_id": "test-sess"},
        permission_manager=permission_manager,
    ))
    mine = [(e, m) for e, n, m in recorded if n == "audit_marker_write_gate"]
    assert ("PreToolUse", "Bash") in mine
    assert ("PreToolUse", "Write|Edit|MultiEdit|NotebookEdit") in mine
    bash = [n for e, n, m in recorded if e == "PreToolUse" and m == "Bash"]
    assert bash.index("audit_marker_write_gate") < bash.index("session_link_gate")
    assert bash.index("audit_marker_write_gate") < bash.index("dangerous_command_gate")


# ── regression cases from the two-expert review ──────────────────────────────

REVIEW_DENY = [
    # correctness HIGH: a comment with an apostrophe must not fail the whole command open
    "rm -f ~/.swarm-ai/state/pipeline_agent_audit/*.marker  # don't keep stale ones",
    "rm -rf ~/.swarm-ai/state/pipeline_agent_audit; echo $'it\\'s done'",
    # MEDIUM: a recursive copy / extract whose destination CONTAINS the dir
    "cp -R /tmp/pipeline_agent_audit ~/.swarm-ai/state/",
    "cp -R /tmp/stage/. ~/.swarm-ai/state/",
    "rsync -a --delete /tmp/empty/ ~/.swarm-ai/state/",
    "tar -xzf /tmp/b.tgz -C ~/.swarm-ai",
    "unzip -o /tmp/b.zip -d ~/.swarm-ai/state",
    "ditto /tmp/stage ~/.swarm-ai/state",
    # MEDIUM: interpreter options and venv runners
    f"python3 -Ic \"open('{D}/m','w').write('x')\"",
    f"python3 -X utf8 -c \"open('{D}/m','w')\"",
    f"python3 -W ignore -c \"open('{D}/m','w')\"",
    f"ruby -I lib -e \"File.write('{D}/m','x')\"",
    f"perl -I lib -e 'open F,\">{D}/m\"'",
    f"node --require ./x.js -e \"require('fs').writeFileSync('{D}/m','x')\"",
    f"uv run python -c \"open('{D}/m','w')\"",
    f"poetry run python -c \"open('{D}/m','w')\"",
    f"npx -y node -e \"require('fs').writeFileSync('{D}/m','x')\"",
    f"bash -O extglob -c 'rm {D}/m'",
    # LOW: write-capable verbs with their own output flag
    f"sort -o {D}/m /tmp/x",
    f"mktemp {D}/session_abc_adv_XXXX",
    f"echo x | sponge {D}/m",
    f"dd if=/dev/zero of={D}/m count=1",
    # assignment whose ~ must expand to taint the variable
    f"D={D}; echo x > $D/m",
    f'export AUD={D} && touch "$AUD/m"',
]

REVIEW_ALLOW = [
    # review finding 9: an unanchored substring must not deny a same-named sibling file/dir
    "touch backend/tests/test_pipeline_agent_audit_reader.py",
    "rm backend/tests/test_pipeline_agent_audit_reader.py",
    "mkdir -p /tmp/pipeline_agent_audit_test",
    "rm -rf /tmp/pytest-of-gawan/x/pipeline_agent_audit",
    # review finding 6: sed/perl refactors that mention the NAME in the script are reads/edits elsewhere
    "sed -n '/pipeline_agent_audit/p' backend/core/security_hooks.py",
    "sed 's/pipeline_agent_audit/X/' backend/core/security_hooks.py | head",
    "sed -i '' 's/pipeline_agent_audit/agent_audit/' backend/scripts/artifact_cli.py",
    "perl -pi -e 's/pipeline_agent_audit/agent_audit/' backend/scripts/artifact_cli.py",
    "grep -rl pipeline_agent_audit backend | xargs sed -i '' 's/foo/bar/'",
    "grep -rl pipeline_agent_audit backend | xargs wc -l",
    # review finding 8: a find whose -exec is a READER is not a write
    "find ~/.swarm-ai -name '*.json' -exec python3 -m json.tool {} \\;",
    # review finding 9: perl -Mstrict is a module, not -i
    "perl -Mstrict -e 'print 1'",
]


@pytest.mark.parametrize("command", REVIEW_DENY)
def test_review_bypasses_are_denied(home, command):
    assert _denied(_run("Bash", {"command": command})), command


@pytest.mark.parametrize("command", REVIEW_ALLOW)
def test_review_false_denials_now_pass(home, command):
    assert not _denied(_run("Bash", {"command": command})), command


def test_classification_is_bounded_on_pathological_input(home):
    # The gate is async-with-no-await; a slow classify would stall the event loop.
    import time
    for probe in ["echo " + "$(" * 4000, "x>" + "a" * 200_000,
                  "read " + " ".join(f"v{i}" for i in range(2000)) + f"; echo x>{D}/m"]:
        t = time.monotonic()
        _run("Bash", {"command": probe})
        assert time.monotonic() - t < 5.0, probe[:40]


def test_v1_authority_key_is_unreadable_to_agent_tools(home):
    key = home / ".swarm-ai/state/pipeline_agent_audit/runs/run_x/authority.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(b"secret")
    assert _denied(_run("Bash", {"command": f"cat {key}"}))
    assert _denied(_run("Read", {"file_path": str(key)}))
    assert _denied(_run("Grep", {"path": str(key), "pattern": "x"}))
    marker = key.parent / "events.jsonl"
    assert not _denied(_run("Read", {"file_path": str(marker)}))


def test_v1_authority_key_globs_and_variables_are_denied(home):
    run_dir = home / ".swarm-ai/state/pipeline_agent_audit/runs/run_x"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "authority.key").write_bytes(b"secret")
    commands = [
        f"cat {run_dir}/authority.*",
        f"cat {run_dir}/*.key",
        f"D={run_dir}; K='authority.*'; cat $D/$K",
        f"find {run_dir} -type f -print",
        f"tar -cf /tmp/a.tar {run_dir}",
        f"cp -R {run_dir} /tmp/copied-audit",
        "cat authority.*",
    ]
    for command in commands[:-1]:
        assert _denied(_run("Bash", {"command": command})), command
    assert _denied(_run("Bash", {"command": commands[-1]}, cwd=str(run_dir)))
    assert _denied(_run("Read", {"file_path": str(run_dir / "authority.*")}))
    assert _denied(_run("Grep", {"path": str(run_dir), "pattern": "secret"}))
    assert _denied(_run("Grep", {"path": str(home), "pattern": "secret"}))
    assert not _denied(_run("Grep", {"path": str(run_dir / "events.jsonl"),
                                      "pattern": "metric"}))


def test_v1_authority_key_direct_interpreter_script_is_denied(home, tmp_path):
    run_dir = home / ".swarm-ai/state/pipeline_agent_audit/runs/run_x"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "authority.key").write_bytes(b"secret")
    script = tmp_path / "reader.py"
    script.write_text(
        "from pathlib import Path\n"
        f"print((Path({str(run_dir)!r}) / 'authority.key').read_bytes())\n"
    )
    assert _denied(_run("Bash", {"command": f"python3 {script}"}))


def test_v1_authority_key_script_read_from_key_cwd_is_denied(home, tmp_path):
    run_dir = home / ".swarm-ai/state/pipeline_agent_audit/runs/run_x"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "authority.key").write_bytes(b"secret")
    script = tmp_path / "reader.py"
    script.write_text("from pathlib import Path\nprint(Path('authority.key').read_bytes())\n")
    assert _denied(_run("Bash", {"command": f"python3 {script}"}, cwd=str(run_dir)))
