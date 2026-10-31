"""The Swarm Out / Hive operation table and the gate generated from it
(core/swarm_out_ops.py; docs/design/2026-10-31-swarm-out-ops-registry.md §5, §8).

Questions answered (design §8 invariants 1-4):
1. is every route the app serves under /api/swarm-out and /api/hive, and every route
   the Host proxy forwards, a row of the table, and every row a real route?
2. is every person-only op's path (every proxy write among them) denied by the gate in its plain, query, encoded,
   ``..``, quote-split and trailing-slash spellings, called by curl, a variable, Python or
   Node, while the owner agent's ops are not, and a command that only names a route (a
   search, a file being written) is not judged?
3. is any command naming Swarm Out or the Hive routes denied in a non-owner channel?
4. does the ``scripts.swarm_out`` CLI call only ops the owner agent may call?
"""
from __future__ import annotations

import re

import pytest

from core import swarm_out_ops
from core.run_owner_gate import _decide, _user_only_deny
from core.swarm_out_ops import OWNER, PERSON

_SKIP_METHODS = {"HEAD", "OPTIONS"}
_PREFIXES = ("/api/swarm-out", "/api/hive")


def _app_routes() -> "set[tuple[str, str]]":
    from main import app
    out = set()
    for r in app.routes:
        path = getattr(r, "path", "")
        if path.startswith(_PREFIXES):
            for m in sorted(getattr(r, "methods", None) or ()):
                if m not in _SKIP_METHODS:
                    out.add((m, path))
    return out


def test_every_route_is_a_row_and_every_row_a_route():
    routes = _app_routes()
    assert routes, "no routes found under the prefixes"
    rows = {(op.method, op.path) for op in swarm_out_ops.ops()}
    for method, path in routes:
        if path in swarm_out_ops.WILDCARD_ROUTES:
            prefix = swarm_out_ops.WILDCARD_ROUTES[path]
            assert any(op.name.startswith(prefix) and op.method == method
                       for op in swarm_out_ops.ops()), (method, path)
            continue
        assert (method, path) in rows, f"{method} {path} has no row in core/swarm_out_ops.py"
    # no stale rows: every hand-written row is served
    from core.swarm_out_remote import ROUTES
    proxied = {(r.method, f"/api/swarm-out/remote/{{sid}}/{r.template}") for r in ROUTES}
    for op in swarm_out_ops.ops():
        assert (op.method, op.path) in routes | proxied, f"stale row {op.name}"


def test_proxy_writes_are_the_persons_and_reads_the_owner_agents_too():
    from core.swarm_out_remote import ROUTES
    rows = {(op.method, op.path): op for op in swarm_out_ops.ops()}
    for r in ROUTES:
        op = rows[(r.method, f"/api/swarm-out/remote/{{sid}}/{r.template}")]
        want = {PERSON, OWNER} if r.method == "GET" else {PERSON}
        assert op.callers == want, op.name


def test_names_are_unique_and_rows_well_formed():
    names = [op.name for op in swarm_out_ops.ops()]
    assert len(names) == len(set(names))
    for op in swarm_out_ops.ops():
        assert op.callers and "restricted_agent" not in op.callers, op.name
        assert op.weight in {"read", "change", "irreversible", "off_machine"}, op.name
        if op.weight in {"irreversible", "off_machine"} and op.name != "task.dispatch":
            # the design's rule: what is irreversible or leaves the machine is the person's
            # (dispatching a task is the one owner-agent exception, design §5.1)
            assert OWNER not in op.callers, op.name


_SAMPLE = {"dispatch_id": "so_0123456789ab", "sid": "s-abc123", "hive_id": "hv1",
           "session_id": "s-abc123", "request_id": "req1", "account_id": "acc1",
           "instance_id": "inst1", "brain": "SwarmAI", "work_id": "w1", "key": "k1",
           "seq": "3", "client_id": "c1", "task_id": "t1"}


def _concrete(template: str) -> str:
    return re.sub(r"\{([^}:]+)(?::[^}]+)?\}", lambda m: _SAMPLE[m.group(1)], template)


def _spellings(path: str) -> "list[str]":
    url = f"http://127.0.0.1:18321{path}"
    half = len(url) // 2
    return [
        f"curl -s -X POST {url}",
        f"curl -s '{url}?x=1' -H 'X-Swarm-User-Action: 1'",
        f"curl -s {url}/",
        f'curl -s "{url}"',
        f"curl -s {url.replace('/api/', '/%61pi/', 1)}",
        f"curl -s {url.replace('/api/', '/api/zz/../', 1)}",
        f"curl -s {url.replace('/api/', '/api/./', 1)}",
        f"curl -s \"{url[:half]}\"'{url[half:]}'",
        f"curl -sL {url}//",
        f"curl -sL {url}%2F%2F",
        f"curl -s {url.replace('/api/', '//api//', 1)}",
        f"curl -s {url.replace('/api/swarm-out/remote/s-abc123/', '/api/swarm-out/remote/s-abc123//', 1)}",
        f"for u in {url},x; do curl $u; done",
        f"curl -s {url}.",
        f"curl -s {url}:",
        f"X=({url}]; curl $X",
        f"curl -s {{u:{url}}}",
        f"C=curl; $C {url}",
        f"cu\\rl -s {url}",
        f"python3 - <<'EOF'\nimport urllib.request as u\nu.urlopen('{url}')\nEOF",
        f"node -e \"fetch('{url}', {{method: 'POST'}})\"",
        f"python3 -c \"import urllib.request as u; u.urlopen('{url}')\"",
    ]


@pytest.mark.parametrize("op", [o for o in swarm_out_ops.ops() if OWNER not in o.callers],
                         ids=lambda o: o.name)
def test_person_only_ops_are_denied_in_every_spelling(op):
    path = _concrete(op.path)
    for cmd in _spellings(path):
        d = _user_only_deny(cmd)
        assert d is not None, cmd
        assert d["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("op", [o for o in swarm_out_ops.ops() if OWNER in o.callers],
                         ids=lambda o: o.name)
def test_owner_agent_ops_are_not_denied(op):
    url = f"http://127.0.0.1:18321{_concrete(op.path)}"
    # an id written as a shell variable, a Python f-string field, or with a dot
    as_var = re.sub(r"\{([^}:]+)(?::[^}]+)?\}", lambda m: "${" + m.group(1).upper() + "}", op.path)
    as_field = re.sub(r"\{([^}:]+)(?::[^}]+)?\}", lambda m: "{" + m.group(1) + "}", op.path)
    as_dot = re.sub(r"\{([^}:]+)(?::[^}]+)?\}", "i.1", op.path)
    for cmd in (f"curl -s {url}", f"curl -s '{url}?x=1'", f'curl -s -X {op.method} "{url}"',
                f'curl -s "http://127.0.0.1:18321{as_var}"',
                f"python3 -c \"import requests; requests.get(f'{{B}}{as_field}')\"",
                f"curl -s http://127.0.0.1:18321{as_dot}"):
        assert _user_only_deny(cmd) is None, cmd


def test_the_deny_names_the_op_and_reaches_the_hook_decision():
    cmd = "curl -s http://127.0.0.1:18321/api/hive/instances/inst1/credentials"
    d = _decide("sess-1", cmd, {"command": cmd})
    assert d is not None
    assert "hive.credentials" in d["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("cmd", [
    "rg -n '/api/hive/instances' desktop/src",
    "grep -rn /api/swarm-out/runs/x/discard docs/",
    "cat docs/design/x.md | grep /api/ingestion/redact",
    "sed -n 1,40p core/x.py  # /api/pipelines/run_1/decision",
    # a file being written whose text names the route and even a curl line
    "cat > /tmp/notes.md <<'EOF'\nrun: curl -X POST http://127.0.0.1:18321/api/swarm-out/runs/so_1/discard\nEOF",
    "python3 - <<'EOF'\np='t.py'; s=open(p).read(); s=s.replace('a', 'curl http://h/api/hive/instances/i/credentials'); open(p,'w').write(s)\nEOF",
])
def test_naming_a_route_without_calling_it_is_not_judged(cmd):
    assert _user_only_deny(cmd) is None, cmd


def test_a_path_continuing_past_the_op_is_not_the_op():
    assert _user_only_deny("curl http://127.0.0.1:18321/api/swarm-out/runs/x/stop-all") is None
    assert _user_only_deny("curl http://127.0.0.1:18321/api/swarm-out/runs/x/stopx") is None
    assert _user_only_deny("curl http://127.0.0.1:18321/api/hive/instances//i1/brains") is None


def test_the_proxy_routes_only_canonical_paths():
    from core.swarm_out_remote import RemoteError, match_route
    assert match_route("POST", "chat/stop/s1", "s1")[1] == "/api/chat/stop/s1"
    for bad in ("/chat/stop/s1", "chat/stop/s1/", "chat//stop/s1", "chat/stop/s1//"):
        with pytest.raises(RemoteError):
            match_route("POST", bad, "s1")


def test_a_longer_owner_path_is_not_taken_for_a_shorter_person_path():
    # /instances/{id} is the person's (GET detail, DELETE); /instances/{id}/brains is a read
    assert _user_only_deny("curl http://127.0.0.1:18321/api/hive/instances/inst1/brains") is None
    assert _user_only_deny("curl http://127.0.0.1:18321/api/hive/instances/inst1") is not None
    assert _user_only_deny("curl http://127.0.0.1:18321/api/hive/instances") is not None
    assert _user_only_deny("curl http://127.0.0.1:18321/api/swarm-out/runs") is None


def test_match_finds_the_op_of_a_concrete_request():
    assert swarm_out_ops.match("POST", "/api/swarm-out/runs/so_0123456789ab/discard").name == "run.discard"
    assert swarm_out_ops.match("GET", "/api/swarm-out/runs/so_0123456789ab/discard") is None
    assert swarm_out_ops.match("GET", "/api/hive/instances/i1/credentials").name == "hive.credentials"


# ── invariant 3: a non-owner channel agent calls none of it ──

@pytest.mark.parametrize("cmd", [
    "curl http://127.0.0.1:18321/api/swarm-out/runs",
    "\"$SWARM_BACKEND_BIN\" py -m scripts.swarm_out runs",
    "curl http://127.0.0.1:18321/api/hive/latest-version",
    "curl 'http://127.0.0.1:18321/api/'\"hive/instances\"",
    "curl http://127.0.0.1:18321/api//hive/instances",
    "curl http://127.0.0.1:18321/api/%68ive/instances",
    "curl http://127.0.0.1:18321/api/./hive/latest-version",
    "curl http://127.0.0.1:18321/api/x/../hive/latest-version",
    "curl http://127.0.0.1:18321/api/swarm%2Dout/runs",
])
def test_non_owner_channel_is_denied(cmd):
    from core.security_hooks import owner_command_denial
    assert owner_command_denial(cmd)


def test_non_owner_channel_keeps_unrelated_commands():
    from core.security_hooks import owner_command_denial
    assert owner_command_denial("ls -la ~/archive") is None
    assert owner_command_denial("curl http://127.0.0.1:18321/api/hivemind") is None


# ── invariant 4: the CLI calls only the owner agent's ops ──

_CLI_ARGS = {
    "route": ["--task", "x"],
    "spec": ["--task", "x", "--brain", "B", "--acceptance", "a", "--execution", "native",
             "--returns", "answer"],
    "dispatch": ["--task", "x", "--brain", "B", "--acceptance", "a", "--execution", "native",
                 "--returns", "answer", "--title", "t", "--revision", "r"],
    "runs": [],
    "hives": [],
}


def test_cli_calls_only_owner_agent_ops(monkeypatch):
    from scripts import swarm_out as cli
    from core.knowledge_cli import RESTRICTED_ENV
    monkeypatch.delenv(RESTRICTED_ENV, raising=False)
    sub = next(a for a in cli._parser()._actions if a.dest == "cmd")
    assert set(sub.choices) == set(_CLI_ARGS), "a new CLI subcommand: add it here"
    calls: list = []
    monkeypatch.setattr(cli, "_call", lambda method, path, *a, **k: calls.append((method, path)) or 0)
    for name, args in _CLI_ARGS.items():
        before = len(calls)
        assert cli.main([name, *args]) == 0
        assert len(calls) == before + 1, name
        method, path = calls[-1]
        op = swarm_out_ops.match(method, "/api/swarm-out" + path)
        assert op is not None, (name, method, path)
        assert OWNER in op.callers, (name, op.name)
