"""Intake: a task a Hive cannot do is refused before anything is asked or sent (design
docs/design/2026-10-30-dispatch-center.md §2, invariant 1).

Questions: (1) does each source of "needs a private network" refuse the spec — an
internal repository, a clone host in the user's private hosts, a private host named in
the task, a skill with ``network: private``, the agent's own reason — and does a task on
public repositories pass; (2) is a refusal recorded in ``refusals.jsonl`` and never in
the dispatch log, before a Hive is chosen or the Brain synced, answered 422 with
``refused``; (3) does the CLI pass the refusal on and the card show it with the task, so
it can be done here; (4) does ``/runs`` count refusals.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core import swarm_out as so, task_spec as ts
from tests.test_task_spec import _brain, _build, ws  # noqa: F401 — the fixture

INTERNAL = """\
bindings:
  - repo: svc
    kind: internal
    clone: "ssh://git.amazon.com/pkg/Svc"
    delivery_contract: {remote_kind: code-amazon-cr, branch: mainline, review_path: crux, auto_send: manual-push}
"""


def _private(r):
    return [x for x in ts.admit(r) if x.startswith(ts.PRIVATE_REFUSAL)]


def test_each_source_of_a_private_need_refuses_and_a_public_task_passes(ws, monkeypatch):
    monkeypatch.setattr(ts, "private_hosts", lambda: ["*.corp.example"])
    b = _brain(ws)
    assert _private(_build(ws, "Lib", "fix the signer", "tests pass")) == []        # public: taken
    # the agent's reason adds a refusal
    r = _build(ws, "Lib", "fix the signer", "tests pass", needs_private="it reads the corp ticket system")
    assert "the agent says: it reads the corp ticket system" in _private(r)[0]
    # a private host named in the task (under no Brain too)
    r = _build(ws, "General", "summarise https://wiki.corp.example/Page", "a summary", returns="answer")
    assert "wiki.corp.example" in _private(r)[0]
    # a skill of the mode that needs a private network
    (b / "4-capabilities" / "s_lib-release" / "SKILL.md").write_text("---\nname: s_lib-release\nnetwork: private\n---\nx\n")
    assert "skill s_lib-release" in _private(_build(ws, "Lib", "release it", "released"))[0]
    (b / "4-capabilities" / "s_lib-release" / "SKILL.md").write_text("---\nname: s_lib-release\n---\nx\n")
    # a bare pattern covers its subdomains; an IP in the task; a body line is not frontmatter
    monkeypatch.setattr(ts, "private_hosts", lambda: ["corp.example", "10.*"])
    assert _private(_build(ws, "Lib", "see https://git.corp.example/x", "ok"))
    assert "10.1.2.3" in _private(_build(ws, "Lib", "call http://10.1.2.3/api", "ok"))[0]
    (b / "4-capabilities" / "s_lib-release" / "SKILL.md").write_text(
        "---\nname: s_lib-release\n---\nx\n```\nnetwork: private\n```\n")
    assert _private(_build(ws, "Lib", "release it", "released")) == []
    monkeypatch.setattr(ts, "private_hosts", lambda: ["*.corp.example"])
    # an internal repository the mode does not list still goes to the Hive: refused
    doc = __import__("yaml").safe_load((b / "project-agent.yaml").read_text())
    doc["modes"][0]["repos"] = ["lib"]
    (b / "project-agent.yaml").write_text(__import__("yaml").safe_dump(doc))
    (b / "bindings.yaml").write_text((b / "bindings.yaml").read_text() + INTERNAL.split("bindings:\n", 1)[1])
    assert "svc is an internal repository" in _private(_build(ws, "Lib", "fix", "ok"))[0]
    (b / "bindings.yaml").write_text((b / "bindings.yaml").read_text().split("  - repo: svc")[0])
    # a repository cloned from a private host, and an internal one
    (b / "bindings.yaml").write_text((b / "bindings.yaml").read_text().replace("github.com", "git.corp.example"))
    assert "cloned from git.corp.example" in _private(_build(ws, "Lib", "fix", "ok"))[0]
    (b / "bindings.yaml").write_text(INTERNAL)
    assert "svc is an internal repository" in ts.private_needs(b, "code-change", {"repos": [], "skills": []}, "x", "y")
    # the product ships no private host: nothing named in a task is private by default
    monkeypatch.setattr(ts, "private_hosts", lambda: [])
    assert ts.private_needs(b.parent / "nope", "m", {"repos": [], "skills": []}, "see code.amazon.com", "") == []


def test_a_refusal_is_recorded_on_its_own_before_anything_is_sent(ws, monkeypatch, tmp_path):
    from core.project_agent import revision
    monkeypatch.setattr(so, "_state_dir", lambda: tmp_path)
    touched = []

    async def no_fleet():
        touched.append("fleet")
        return {"hives": []}
    monkeypatch.setattr(so, "fleet", no_fleet)
    b = _brain(ws)
    with pytest.raises(so.SwarmOutRefused) as e:
        asyncio.run(so.dispatch_spec(None, "T", "Lib", "fix", "ok", execution="native", returns="commits",
                                     seen_revision=revision(b), needs_private="it needs the corp VPN"))
    assert e.value.private and touched == []                          # no Hive chosen, nothing synced
    rows = [json.loads(x) for x in (tmp_path / "refusals.jsonl").read_text().splitlines()]
    assert rows[0]["brain"] == "Lib" and rows[0]["private"] is True and rows[0]["title"] == "T"
    assert not (tmp_path / "dispatches.jsonl").exists()
    assert so.refusal_counts() == {"total": 1, "private": 1}
    # a refusal whose record cannot be written is still the refusal
    monkeypatch.setattr(so, "_record_refusal", lambda *a: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(so.SwarmOutRefused):
        asyncio.run(so.dispatch_spec(None, "T", "Lib", "fix", "ok", execution="native", returns="commits",
                                     seen_revision=revision(b), needs_private="vpn"))
    from fastapi import HTTPException
    from routers import swarm_out as route

    async def refuse():
        raise so.SwarmOutRefused("Lib cannot be sent", True)
    with pytest.raises(HTTPException) as h:
        asyncio.run(route._call(refuse()))
    assert h.value.status_code == 422 and h.value.detail == {"message": "Lib cannot be sent", "refused": True,
                                                              "private": True}


def test_the_cli_and_the_card_carry_the_refusal_and_the_task(tmp_path, monkeypatch, capsys):
    import io
    import urllib.error
    import scripts.swarm_out as cli
    from core import swarm_out_card as card
    body = json.dumps({"detail": {"message": "Lib cannot be sent: it needs a private network", "refused": True,
                                  "private": True}}).encode()

    def refuse(*a, **k):
        raise urllib.error.HTTPError("u", 422, "x", {}, io.BytesIO(body))
    monkeypatch.setattr(cli.urllib.request, "urlopen", refuse)
    assert cli._call("POST", "/dispatch", {}) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"ok": False, "status": 422, "detail": "Lib cannot be sent: it needs a private network",
                               "refused": True, "private": True}
    task = tmp_path / "task.md"
    task.write_text("read the corp wiki page and summarise it")
    b = card.card(f'"$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --title T --brain Lib --task-file {task}', out)
    assert b["refused"] and b["needsPrivate"] and b["task"] == "read the corp wiki page and summarise it"
    assert b["text"].startswith("✕ Refused: T") and "no Hive was contacted" in b["text"]
    link = tmp_path / "link.md"
    link.symlink_to(task)
    b = card.card(f'"$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --title T --task-file {link}', out)
    assert b["task"] is None                                         # a link is not followed
    b = card.card('"$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --title T --task-file "$TMP/t.md"', out)
    assert b["refused"] and b["task"] is None                        # not a path this side can read
    odd = tmp_path / "$X" / "t.md"                                     # a file whose name looks expanded
    odd.parent.mkdir()
    odd.write_text("not what the shell sent")
    b = card.card(f"\"$SWARM_BACKEND_BIN\" py -m scripts.swarm_out dispatch --title T --task-file '{odd}'", out)
    assert b["task"] is None
    plain = card.card('"$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --title T',
                      json.dumps({"ok": False, "detail": "no Hive"}))
    assert plain["refused"] is False and "task" not in plain
