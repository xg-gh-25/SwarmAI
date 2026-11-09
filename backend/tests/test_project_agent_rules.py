"""A Brain's rules: the person's words, verified once when added (design
docs/design/2026-11-09-project-agent-contract.md §4).

What is tested, one question per test:
- does add write a rule and its record only for the person's own words of this turn,
  keep every other key and the header, and refuse (writing nothing) without a session,
  in a channel turn, for words not in the message, for too few characters, for a Brain
  with no definition?
- is a second add of the same words one row, does remove drop it and keep the record,
  and does removing an unknown rule change nothing?
- does the reader give only verified rules, the others apart, in file order, and never
  raise on a missing or broken definition or record; are rules never a definition problem?
- does the run owner gate hand the session id to the rule command?
- is the record directory one the audit write gate protects?
- does a redraft keep the rules as the file holds them when it writes (one added while
  it was built too), and nothing retired?
- is a rule whole sentences of the message (a part, also a clause after a comma, a
  semicolon or a colon, is refused), does removing need the person's own turn containing
  the whole rule, and does the gate refuse a rule command that names the session variables
  (quoted, or the script by path) or comes from a sub-agent? These guard against mistakes;
  a command built to hide its session is the product-wide limit (S6-3).
- does writing a rule keep the definition's file mode?
- does add refuse a rule longer than a dispatch carries, and one more than a Brain holds?
- does a package leave the rules out?
Methodology: a temp workspace and app data dir; the person's message is the session
input the command reads (session_inputs is replaced by its two lookups); the real gates,
draft writer and packager are driven.
"""
import asyncio
import json
from pathlib import Path

import pytest
import yaml

from core import project_agent_rules as rules

HEADER = "# Project Agent definition: how a Hive run does this Brain's work.\n# Fields: see STANDARD.md.\n"
BODY = {"schema": 2, "identity": {"summary": "A demo Brain"},
        "modes": [{"id": "research", "when": "a question"}], "default_mode": "research",
        "watches": [{"area": "a", "why": "b", "source": "TECH.md"}]}
MESSAGE = "以后在 Demo 里这样做。发布说明要放仓库里 不要有内部信息！谢谢"
SAID = "发布说明要放仓库里 不要有内部信息"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    brain = workspace / "Projects" / "Demo"
    brain.mkdir(parents=True)
    (brain / "project-agent.yaml").write_text(HEADER + yaml.safe_dump(BODY, sort_keys=False, allow_unicode=True),
                                              encoding="utf-8")
    (workspace / "Projects" / "Empty").mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    state = {"event": "input:e1", "text": MESSAGE}
    from core import session_inputs
    monkeypatch.setattr(session_inputs, "latest_user_event_id",
                        lambda sid: state["event"] if sid == "sess-1" else None)
    monkeypatch.setattr(session_inputs, "user_authored_text",
                        lambda event: state["text"] if event == state["event"] else None)
    return workspace, state


def _doc(workspace):
    return yaml.safe_load((workspace / "Projects/Demo/project-agent.yaml").read_text(encoding="utf-8"))


def _records():
    f = rules.rules_dir() / "Demo.jsonl"
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def test_add_writes_a_verified_rule_only_for_the_persons_words(ws, capsys):
    workspace, state = ws
    before = (workspace / "Projects/Demo/project-agent.yaml").read_bytes()
    refusals = [
        ("Demo", SAID, ""),                       # no session id
        ("Demo", "release notes stay out", "sess-1"),  # not in the message
        ("Demo", "发布说明", "sess-1"),             # fewer than 6 characters
        ("Empty", SAID, "sess-1"),                # no definition
        ("Nope", SAID, "sess-1"),                 # no such Brain
    ]
    for brain, said, sid in refusals:
        with pytest.raises(rules.RuleError):
            rules.add_rule(workspace, brain, said, sid)
    state["event"] = None                         # a channel turn: no user message
    with pytest.raises(rules.RuleError, match="channel"):
        rules.add_rule(workspace, "Demo", SAID, "sess-1")
    assert (workspace / "Projects/Demo/project-agent.yaml").read_bytes() == before
    assert _records() == []

    state["event"] = "input:e1"
    out = rules.add_rule(workspace, "Demo", SAID, "sess-1")
    assert out["added"] is True
    text = (workspace / "Projects/Demo/project-agent.yaml").read_text(encoding="utf-8")
    assert text.startswith(HEADER)
    doc = _doc(workspace)
    assert {k: v for k, v in doc.items() if k != "rules"} == BODY
    assert list(doc)[:-1] == list(BODY) and doc["rules"][0]["said"] == SAID
    [rec] = [json.loads(line) for line in _records()]
    assert rec["key"] == rules.quote_key(SAID) and rec["event"] == "input:e1"

    # the command: exit 2 and the reason when refused
    from scripts import project_agent_rule as cli
    assert cli.main(["add", "--brain", "Demo", "--said", "not in the message at all"]) == 2
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["ok"] is False


def test_duplicate_add_and_remove(ws):
    workspace, _ = ws
    rules.add_rule(workspace, "Demo", SAID, "sess-1")
    again = rules.add_rule(workspace, "Demo", " 发布说明要放仓库里  不要有内部信息 ", "sess-1")
    assert again["added"] is False and len(_doc(workspace)["rules"]) == 1 and len(_records()) == 1
    with pytest.raises(rules.RuleError):
        rules.remove_rule(workspace, "Demo", "a rule that is not there", "sess-1")
    assert len(_doc(workspace)["rules"]) == 1
    rules.remove_rule(workspace, "Demo", SAID, "sess-1")
    assert "rules" not in _doc(workspace) and len(_records()) == 1


def test_only_verified_rules_are_read_and_nothing_raises(ws):
    workspace, _ = ws
    d = workspace / "Projects/Demo"
    rules.add_rule(workspace, "Demo", SAID, "sess-1")
    doc = _doc(workspace)
    doc["rules"] = [{"said": "written into the file by hand"}, "not a mapping", {"said": 3}] + doc["rules"]
    (d / "project-agent.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    verified, unverified = rules.verified_rules(d)
    assert [r["said"] for r in verified] == [SAID]
    assert [r["said"] for r in unverified] == ["written into the file by hand"]
    assert [r["verified"] for r in rules.rule_rows(d)] == [False, True]
    from core.project_agent import dispatch_problems, load_project_agent, validate
    assert validate(doc) == validate({k: v for k, v in doc.items() if k != "rules"})
    assert not [p for p in dispatch_problems(d) if "rule" in p]
    assert [r["said"] for r in load_project_agent(d)["rules"]] == ["written into the file by hand", SAID]

    (rules.rules_dir() / "Demo.jsonl").write_text("not json\n", encoding="utf-8")
    assert rules.verified_rules(d) == ([], [{"said": "written into the file by hand", "added": ""},
                                            {"said": SAID, "added": doc["rules"][-1]["added"]}])
    (d / "project-agent.yaml").write_text(": not yaml : [", encoding="utf-8")
    assert rules.verified_rules(d) == ([], []) and load_project_agent(d)["problems"]
    assert rules.verified_rules(workspace / "Projects/Empty") == ([], [])


def test_run_owner_gate_hands_the_session_id_to_the_rule_command():
    from core import run_owner_gate
    gate = run_owner_gate.create_run_owner_gate({"sdk_session_id": "s-abc123"})
    cmd = '"$SWARM_BACKEND_BIN" py -m scripts.project_agent_rule add --brain Demo --said "x y z w"'
    out = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, "t1", None))
    new = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert new.startswith("export SWARM_APP_SESSION_ID=s-abc123") and new.endswith(cmd)


def test_the_record_directory_is_protected(tmp_path, monkeypatch):
    from config import get_app_data_dir
    from core import security_hooks
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    assert rules.rules_dir() == Path(get_app_data_dir()) / "state" / "pipeline_agent_audit" / "project_agent_rules"
    assert security_hooks._AGENT_AUDIT_DIR.parts[-2:] == ("state", "pipeline_agent_audit")
    audit = tmp_path / "data" / "state" / "pipeline_agent_audit"
    (audit / "project_agent_rules").mkdir(parents=True)
    monkeypatch.setattr(security_hooks, "_AGENT_AUDIT_DIR", audit)
    monkeypatch.delenv("SWARM_GATE_FORCE", raising=False)
    gate = security_hooks.create_audit_marker_write_gate({})
    target = audit / "project_agent_rules" / "Demo.jsonl"

    def decision(tool, tool_input):
        out = asyncio.run(gate({"tool_name": tool, "tool_input": tool_input, "cwd": str(tmp_path)}, "t", None))
        return (out or {}).get("hookSpecificOutput", {}).get("permissionDecision")

    assert decision("Bash", {"command": f"echo '{{\"key\": \"x\"}}' >> {target}"}) == "deny"
    assert decision("Bash", {"command": f"rm {target}"}) == "deny"
    assert decision("Write", {"file_path": str(target), "content": "{}"}) == "deny"
    assert decision("Bash", {"command": f"cat {target}"}) != "deny"


def test_a_redraft_keeps_rules_even_one_added_while_it_was_built(tmp_path, monkeypatch):
    from core import project_agent_draft as pad
    from tests.test_project_agent_draft import _brain, _repo
    brain = _brain(tmp_path, _repo(tmp_path))
    assert pad.apply(brain, {})["written"]
    target = brain / "project-agent.yaml"
    doc = yaml.safe_load(target.read_text(encoding="utf-8"))
    target.write_text(yaml.safe_dump({**doc, "rules": [{"said": SAID, "added": "2026-11-09"}], "status": "x"},
                                     allow_unicode=True), encoding="utf-8")
    real_build = pad.build

    def build_then_a_rule_is_added(*a, **k):
        out = real_build(*a, **k)
        cur = yaml.safe_load(target.read_text(encoding="utf-8"))
        cur["rules"].append({"said": "added while the draft was built", "added": "2026-11-09"})
        target.write_text(yaml.safe_dump(cur, allow_unicode=True), encoding="utf-8")
        return out
    monkeypatch.setattr(pad, "build", build_then_a_rule_is_added)
    assert pad.apply(brain, {})["written"]
    after = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert [r["said"] for r in after["rules"]] == [SAID, "added while the draft was built"]
    assert "status" not in after


def test_a_rule_is_whole_sentences_and_the_session_is_the_gates(ws):
    workspace, state = ws
    state["text"] = "以后在 Demo 里这样：不要把发布说明放到仓库里。Never push to main without asking!"
    for part in ("把发布说明放到仓库里", "不要把发布说明放到仓库里", "push to main without asking"):
        with pytest.raises(rules.RuleError, match="whole sentence"):
            rules.add_rule(workspace, "Demo", part, "sess-1")
    assert rules.add_rule(workspace, "Demo", "以后在 Demo 里这样：不要把发布说明放到仓库里", "sess-1")["added"]
    assert rules.add_rule(workspace, "Demo", "never push to main without asking", "sess-1")["added"]
    assert rules.add_rule(workspace, "Demo", "以后在 Demo 里这样：不要把发布说明放到仓库里。Never push to main "
                          "without asking", "sess-1")["added"]
    # a clause after a comma, a semicolon or a colon is never a sentence of its own
    for text, part, whole in (
            ("Do not, under any circumstances, push to main without asking.", "push to main without asking",
             "Do not, under any circumstances, push to main without asking"),
            ("Never do these: push to main; delete the prod database.", "delete the prod database",
             "Never do these: push to main; delete the prod database"),
            ("以后：不要提交、推送到 main 分支。", "推送到 main 分支", "以后：不要提交、推送到 main 分支"),
            ("Don’t, ever, push to main without asking.", "push to main without asking",
             "Don’t, ever, push to main without asking"),
            ("Never push to main, i.e. deploy directly.", "deploy directly",
             "Never push to main, i.e. deploy directly"),
            ("Do not ship v2.0 on Friday.", "0 on Friday", "Do not ship v2.0 on Friday"),
            ("Ship it Etc. never on Friday.", "never on Friday", "Ship it Etc. never on Friday")):
        state["text"] = text
        with pytest.raises(rules.RuleError, match="whole sentence"):
            rules.add_rule(workspace, "Demo", part, "sess-1")
        assert rules.add_rule(workspace, "Demo", whole, "sess-1")["added"]
    # removing needs the person's own message to contain the whole rule
    for text in ("summarize this page for me, please", "please push to main now, without asking"):
        state["text"] = text
        with pytest.raises(rules.RuleError, match="contain"):
            rules.remove_rule(workspace, "Demo", "never push to main without asking", "sess-1")
    state["text"] = "去掉 Never push to main without asking 这条"
    rules.remove_rule(workspace, "Demo", "never push to main without asking", "sess-1")
    # removing needs a turn the person opened
    state["event"] = None
    with pytest.raises(rules.RuleError, match="channel"):
        rules.remove_rule(workspace, "Demo", "不要把发布说明放到仓库里", "sess-1")
    with pytest.raises(rules.RuleError):
        rules.remove_rule(workspace, "Demo", "不要把发布说明放到仓库里", "")
    assert len(_doc(workspace)["rules"]) == 9
    # Chinese with half-width marks, and a sentence ending inside a quote, still split
    state["event"] = "input:e1"
    state["text"] = '不要推送到 main.以后先跑测试. Always say "done." Never push on Friday.'
    for whole in ("不要推送到 main", "以后先跑测试", "Never push on Friday"):
        assert rules.add_rule(workspace, "Demo", whole, "sess-1")["added"], whole

    from core import run_owner_gate
    gate = run_owner_gate.create_run_owner_gate({"sdk_session_id": "s-abc123"})
    base = '"$SWARM_BACKEND_BIN" py -m scripts.project_agent_rule add --brain Demo --said "x y z w"'
    for cmd, extra in ((f"SWARM_APP_SESSION_ID=s-other {base}", {}),
                       (f"export SWARM_APP_SESSION_ID=s-other; {base}", {}),
                       (f"SWARM_TURN_ID={'t' * 32} {base}", {}),
                       (f"read SWARM_APP_SESSION_ID <<< s-other; export SWARM_APP_SESSION_ID; {base}", {}),
                       ('SWARM_APP_SESSION_ID=s-other "$SWARM_BACKEND_BIN" py -m scripts.project_agent_"rule" add '
                        '--brain Demo --said "x y z w"', {}),
                       ('SWARM_APP_SESSION_ID=s-other python scripts/project_agent_rule.py add --brain Demo '
                        '--said "x y z w"', {}),
                       ("python scripts/__pycache__/project_agent_rule.cpython-312.pyc add --brain Demo --said x",
                        {"agent_id": "sub-1"}),
                       (base, {"agent_id": "sub-1"})):
        out = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}, **extra}, "t1", None))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", cmd
    for cmd in ('python -c "import scripts.project_agent_rule as r; r.main()"',
                "python3.12 scripts/project_agent_rule.py add --brain Demo --said x"):
        out = asyncio.run(gate({"tool_name": "Bash", "agent_id": "sub-1", "tool_input": {"command": cmd}}, "t1", None))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", cmd
    # running its tests, or reading the script, is not the rule command: a sub-agent may
    for cmd, extra in (("python -m pytest -q tests/test_project_agent_rules.py", {"agent_id": "sub-1"}),
                       ("cat scripts/project_agent_rule.py", {"agent_id": "sub-1"}),
                       ("git diff -- backend/scripts/project_agent_rule.py", {"agent_id": "sub-1"}),
                       ("grep -n SWARM_APP_SESSION_ID scripts/project_agent_rule.py", {})):
        out = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}, **extra}, "t1", None))
        assert (out.get("hookSpecificOutput") or {}).get("permissionDecision") != "deny", cmd


def test_writing_a_rule_keeps_the_files_mode(ws):
    workspace, _ = ws
    f = workspace / "Projects/Demo/project-agent.yaml"
    f.chmod(0o644)
    rules.add_rule(workspace, "Demo", SAID, "sess-1")
    assert f.stat().st_mode & 0o777 == 0o644
    rules.remove_rule(workspace, "Demo", SAID, "sess-1")
    assert f.stat().st_mode & 0o777 == 0o644


def test_a_package_leaves_the_rules_out(tmp_path):
    from core import ddd_packager as pk
    from tests.test_ddd_packager import build_fixture_ddd
    ddd = build_fixture_ddd(tmp_path, targets=["aim-capabilities"], visibility="internal")
    (ddd / "project-agent.yaml").write_text(
        yaml.safe_dump({**BODY, "rules": [{"said": "only-my-own-words-xyz", "added": "2026-11-09"}]}),
        encoding="utf-8")
    [res] = pk.package_ddd(ddd, tmp_path / "out")
    for f in Path(res.out_dir).rglob("*"):
        if f.is_file():
            assert b"only-my-own-words-xyz" not in f.read_bytes(), f


def test_add_refuses_what_a_dispatch_could_not_carry(ws):
    workspace, state = ws
    long_said = "这条规则很长。" * 300
    state["text"] = long_said
    with pytest.raises(rules.RuleError, match="at most"):
        rules.add_rule(workspace, "Demo", long_said, "sess-1")
    p = workspace / "Projects/Demo/project-agent.yaml"
    doc = _doc(workspace)
    doc["rules"] = [{"said": f"rule number {i} here"} for i in range(rules.MAX_RULES)]
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    state["text"] = MESSAGE
    with pytest.raises(rules.RuleError, match="remove one"):
        rules.add_rule(workspace, "Demo", SAID, "sess-1")
    assert len(_doc(workspace)["rules"]) == rules.MAX_RULES
    assert _records() == []                      # refused: no record either
