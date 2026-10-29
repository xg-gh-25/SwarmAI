"""Brain sync to a Hive (design docs/design/2026-10-28-project-agent-flow.md §6, §11
"同步" and "安装接口").

Questions these tests answer: is only the whitelist packed (no private context, run
records, code graphs, archives, locks, secrets or DDD-lifecycle skills), with machine
paths taken out of bindings.yaml; does the same content give the same digest and the
same bytes; does a document being written stop the package; does the Hive refuse a
package with a link, a directory entry, a path outside the whitelist, `..`, an
absolute path, a wrong digest or too much data, before writing anything; does a
symlink planted in the Brain redirect no write; are files the package no longer has
deleted while run records stay; does the Hive recompute the host's digest from its
own disk, and count files changed there; are the endpoints Hive-only, refuse while a
session using the Brain runs, and answer the console's questions; does a draft stay
on the host while the default Brains still sync.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
from pathlib import Path

import httpx
import pytest
import yaml

from core import brain_sync as bs

DEF = {"schema": 2, "status": "reviewed", "placements": ["chat-tab", "hive"],
       "landing": {"approval": "human"},
       "modes": [{"id": "research", "when": "a question", "profile": "research"}],
       "default_mode": "research"}


def _brain(root: Path, name: str = "Lib", doc: dict | None = None) -> Path:
    b = root / "Projects" / name
    (b / "2-understanding" / "knowledge").mkdir(parents=True)
    (b / "2-understanding" / "PRODUCT.md").write_text("# PRODUCT\n")
    (b / "2-understanding" / "knowledge" / "note.md").write_text("a note\n")
    (b / "2-understanding" / "TECH-archive-2026-01.md").write_text("old\n")
    (b / "assets" / "data-source" / "data-contract").mkdir(parents=True)
    (b / "assets" / "data-source" / "data-contract" / "entities.md").write_text("## Account\n- id\n")
    (b / "assets" / "data-source" / "sample.png").write_bytes(b"\x89PNG")
    (b / "3-gates").mkdir()
    (b / "3-gates" / "no_push.py").write_text("import sys\nsys.exit(0)\n")
    (b / "3-gates" / "__pycache__").mkdir()
    (b / "3-gates" / "__pycache__" / "no_push.cpython-312.pyc").write_bytes(b"\0")
    (b / "4-capabilities" / "s_lib-release").mkdir(parents=True)
    (b / "4-capabilities" / "s_lib-release" / "SKILL.md").write_text("---\nname: s_lib-release\n---\n")
    (b / "4-capabilities" / "s_ddd-persist").mkdir()
    (b / "4-capabilities" / "s_ddd-persist" / "SKILL.md").write_text("x\n")
    (b / "4-capabilities" / "README.md").write_text("readme\n")
    (b / ".artifacts" / "runs").mkdir(parents=True)
    (b / ".artifacts" / "runs" / "r.json").write_text("{}")
    (b / "code_intel.db").write_bytes(b"db")
    (b / ".env").write_text("SECRET=1\n")
    (b / "AGENTS.md").write_text("# Lib\n")
    (b / "aim.json").write_text('{"name": "Lib"}')
    (b / ".project.json").write_text('{"name": "Lib"}')
    (b / "bindings.yaml").write_text(
        "bindings:\n  - repo: lib\n    kind: external\n    clone: https://github.com/acme/lib.git\n"
        "    worktree: /Users/me/src/lib\n"
        "    delivery_contract: {remote_kind: github-pr, branch: main, review_path: github-pr, auto_send: manual-push}\n")
    (b / "project-agent.yaml").write_text(yaml.safe_dump(doc or DEF))
    return b


EXPECTED = ["2-understanding/PRODUCT.md", "2-understanding/TECH-archive-2026-01.md",
            "2-understanding/knowledge/note.md", "assets/data-source/data-contract/entities.md",
            "3-gates/no_push.py",
            "4-capabilities/s_lib-release/SKILL.md", ".project.json", "AGENTS.md", "aim.json",
            "bindings.yaml", "project-agent.yaml"]


def test_only_the_whitelist_travels_and_worktree_paths_stay_home(tmp_path):
    # (document bodies travel as written: a doc that names a host path carries it)
    b = _brain(tmp_path)
    files = bs.collect(b, general=False)
    assert sorted(files) == sorted(EXPECTED)
    assert b"/Users/me" not in files["bindings.yaml"]
    assert yaml.safe_load(files["bindings.yaml"])["bindings"][0]["repo"] == "lib"


@pytest.mark.parametrize("rel,ok", [
    ("MEMORY.md", False), (".context/USER.md", False), ("2-understanding/x.md.lock", False),
    ("2-understanding/x.txt", False), ("assets/logo.png", False), ("3-gates/sub/helper.py", True),
    ("4-capabilities/s_x/run.sh", True), ("4-capabilities/s_ddd-x/SKILL.md", False),
    ("../etc/passwd", False), ("/etc/passwd", False), ("2-understanding\\a.md", False),
    ("2-understanding/./a.md", False), ("3-gates/.env", False), ("4-capabilities/s_x/id_rsa", False),
    ("2-understanding/IMPROVEMENT-archive.md", True), ("assets/data-source/data-contract/e.md", True),
    ("assets/data-source/data-contract/e.csv", False), ("assets/data-source/x.md", False),
    ("assets/code-repo/data-contract/e.md", False), ("a/b/", False), ("2-understanding//a.md", False),
])
def test_whitelist(rel, ok):
    assert bs.whitelisted(rel, general=False) is ok


def test_the_general_brain_is_library_markdown(tmp_path):
    lib = tmp_path / "Knowledge" / "Library"
    (lib / "sub").mkdir(parents=True)
    (lib / "a.md").write_text("a\n")
    (lib / "sub" / "b.md").write_text("b\n")
    (lib / "c.pdf").write_bytes(b"%PDF")
    (lib / "project-agent.yaml").write_text("x: 1\n")
    (lib / "old-archive.md").write_text("x\n")
    assert sorted(bs.collect(lib, general=True)) == ["a.md", "project-agent.yaml", "sub/b.md"]


def test_same_content_same_digest_and_bytes(tmp_path):
    b = _brain(tmp_path)
    f1 = bs.collect(b, False)
    os.utime(b / "AGENTS.md", (1, 1))
    f2 = bs.collect(b, False)
    assert bs.content_digest(f1) == bs.content_digest(f2) and bs.pack(f1) == bs.pack(f2)
    (b / "AGENTS.md").write_text("# Lib!\n")
    assert bs.content_digest(bs.collect(b, False)) != bs.content_digest(f1)


def test_a_document_being_written_stops_the_package(tmp_path):
    from utils.file_lock import md_lock
    b = _brain(tmp_path)
    with md_lock(b / "2-understanding" / "PRODUCT.md"):
        with pytest.raises(bs.SyncError) as e:
            bs.collect(b, False)
    assert e.value.status == 409 and "being written" in str(e.value)


def test_a_symlink_in_the_brain_is_not_followed(tmp_path):
    b = _brain(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("private\n")
    (b / "2-understanding" / "link.md").symlink_to(outside)
    (b / "2-understanding" / "linkdir").symlink_to(tmp_path)
    assert "2-understanding/link.md" not in bs.collect(b, False)


# ── unpack ───────────────────────────────────────────────────────────────────

def _tar(members: list[tuple[str, bytes, bytes]]) -> bytes:
    """members: (name, type, data) with type in tarfile constants."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, kind, data in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "/etc/passwd"
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return buf.getvalue()


@pytest.mark.parametrize("members", [
    [("AGENTS.md", tarfile.SYMTYPE, b"")],
    [("AGENTS.md", tarfile.LNKTYPE, b"")],
    [("2-understanding", tarfile.DIRTYPE, b"")],
    [("../AGENTS.md", tarfile.REGTYPE, b"x")],
    [("/AGENTS.md", tarfile.REGTYPE, b"x")],
    [("2-understanding/../AGENTS.md", tarfile.REGTYPE, b"x")],
    [("MEMORY.md", tarfile.REGTYPE, b"x")],
    [("AGENTS.md", tarfile.REGTYPE, b"x"), ("AGENTS.md", tarfile.REGTYPE, b"y")],
])
def test_unpack_refuses_what_is_not_a_plain_whitelisted_file(members):
    data = _tar(members)
    with pytest.raises(bs.SyncError):
        bs.unpack(data, False, bs.content_digest({m[0]: m[2] for m in members if m[1] == tarfile.REGTYPE}))


def test_unpack_checks_the_digest_and_the_limits(monkeypatch):
    files = {"AGENTS.md": b"# x\n"}
    data = bs.pack(files)
    assert bs.unpack(data, False, bs.content_digest(files)) == files
    with pytest.raises(bs.SyncError, match="digest"):
        bs.unpack(data, False, "0" * 64)
    with pytest.raises(bs.SyncError, match="not a tar.gz"):
        bs.unpack(b"plain", False, bs.content_digest(files))
    monkeypatch.setattr(bs, "MAX_UNPACKED_BYTES", 2)
    with pytest.raises(bs.SyncError) as e:
        bs.unpack(data, False, bs.content_digest(files))
    assert e.value.status == 413
    monkeypatch.setattr(bs, "MAX_UNPACKED_BYTES", 1 << 20)
    monkeypatch.setattr(bs, "MAX_PACKAGE_BYTES", len(data) - 1)
    with pytest.raises(bs.SyncError) as e:
        bs.unpack(data, False, bs.content_digest(files))
    assert e.value.status == 413


# ── install ──────────────────────────────────────────────────────────────────

def test_install_recreates_the_brain_and_the_hive_recomputes_its_digest(tmp_path):
    host, hive = tmp_path / "host", tmp_path / "hive"
    files = bs.collect(_brain(host), False)
    digest = bs.content_digest(files)
    target = hive / "Projects" / "Lib"
    (target / ".artifacts").mkdir(parents=True)
    (target / ".artifacts" / "run.json").write_text("{}")
    (target / "2-understanding").mkdir()
    (target / "2-understanding" / "STALE.md").write_text("gone\n")
    (target / "code_intel.db").write_bytes(b"hive db")
    rec = bs.install(hive, "Lib", files, digest)
    assert rec["digest"] == digest and rec["revision"]
    assert not (target / "2-understanding" / "STALE.md").exists()           # whitelisted, no longer packed
    assert (target / ".artifacts" / "run.json").exists() and (target / "code_intel.db").read_bytes() == b"hive db"
    state = bs.installed_state(hive, "Lib")
    assert state["current_digest"] == digest and state["changed_files"] == 0
    (target / "AGENTS.md").write_text("edited on the Hive\n")
    (target / "2-understanding" / "new.md").write_text("added\n")
    assert bs.installed_state(hive, "Lib")["changed_files"] == 2
    bs.install(hive, "Lib", files, digest)                                    # the next install restores
    assert bs.installed_state(hive, "Lib")["changed_files"] == 0


def test_a_planted_symlink_cannot_redirect_a_write(tmp_path):
    host, hive = tmp_path / "host", tmp_path / "hive"
    files = bs.collect(_brain(host), False)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = hive / "Projects" / "Lib"
    target.mkdir(parents=True)
    (target / "2-understanding").symlink_to(outside)
    with pytest.raises(bs.SyncError) as e:
        bs.install(hive, "Lib", files, bs.content_digest(files))
    assert e.value.status == 409 and list(outside.iterdir()) == []
    (target / "2-understanding").unlink()
    (target / "AGENTS.md").symlink_to(outside / "victim")
    bs.install(hive, "Lib", files, bs.content_digest(files))
    assert not (outside / "victim").exists() and not (target / "AGENTS.md").is_symlink()


def test_a_linked_brain_directory_is_refused(tmp_path):
    hive = tmp_path / "hive"
    (hive / "Projects").mkdir(parents=True)
    (hive / "Projects" / "Lib").symlink_to(tmp_path)
    with pytest.raises(bs.SyncError):
        bs.install(hive, "Lib", {"AGENTS.md": b"x"}, bs.content_digest({"AGENTS.md": b"x"}))
    linked = tmp_path / "hive2"
    linked.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (linked / "Projects").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(bs.SyncError):
        bs.install(linked, "Lib", {"AGENTS.md": b"x"}, bs.content_digest({"AGENTS.md": b"x"}))
    assert not (tmp_path / "elsewhere" / "Lib").exists()


@pytest.mark.parametrize("name", ["..", "a/b", "%2F", "x" * 65, ""])
def test_bad_brain_names_are_refused(tmp_path, name):
    with pytest.raises(bs.SyncError):
        bs.brain_root(tmp_path, name)


def test_a_name_differing_only_in_case_is_refused(tmp_path):
    (tmp_path / "Projects" / "Lib").mkdir(parents=True)
    with pytest.raises(bs.SyncError) as e:
        bs.brain_root(tmp_path, "LIB")
    assert e.value.status == 409


# ── endpoints, host to Hive end to end ───────────────────────────────────────

@pytest.fixture
def hive_app(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import routers.hive_brains as hb
    ws = tmp_path / "hive"
    ws.mkdir()
    monkeypatch.setattr(hb, "_workspace", lambda: ws)
    refreshed = []

    async def refresh(w):
        refreshed.append(w)
    monkeypatch.setattr(hb, "refresh_skills", refresh)
    app = FastAPI()
    app.include_router(hb.router, prefix="/api/hive")
    return app, ws, refreshed, hb


def _client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hive")


def test_the_endpoints_exist_only_on_a_hive(hive_app, monkeypatch):
    app, _ws, _r, _hb = hive_app
    monkeypatch.delenv("SWARMAI_MODE", raising=False)

    async def go():
        async with _client_for(app) as c:
            return (await c.get("/api/hive/brains")).status_code, (
                await c.put("/api/hive/brains/Lib", content=b"x", headers={"X-Brain-Digest": "0" * 64})).status_code
    assert asyncio.run(go()) == (404, 404)


def test_push_install_and_list_end_to_end(hive_app, tmp_path, monkeypatch):
    from core import brain_sync_host as host
    import core.swarm_out as so
    app, ws, refreshed, hb = hive_app
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    local = tmp_path / "host"
    _brain(local)
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: _client_for(app))
    inst = {"name": "hive3"}
    out = asyncio.run(host.sync(inst, "Lib", local))
    assert out["files"] == len(EXPECTED) and refreshed == [ws]
    assert (ws / "Projects" / "Lib" / "AGENTS.md").read_text() == "# Lib\n"
    remote = asyncio.run(host.hive_brains(inst))
    assert remote["supported"] and remote["brains"]["Lib"]["digest"] == out["digest"]
    assert remote["brains"]["Lib"]["current_digest"] == out["digest"]
    row = host._row(host.local_brain(local, "Lib"), remote["brains"]["Lib"], True)
    assert row["synced"] and row["dispatchable"] and row["hive_changed_files"] == 0
    # a turn holds the Brain: no install, and the console row counts it
    from core import brain_use

    async def held():
        await brain_use.hold("Lib", "turn-1")
        try:
            (local / "Projects" / "Lib" / "AGENTS.md").write_text("# Lib v2\n")
            with pytest.raises(host.SyncRefused, match="busy"):
                await host.sync(inst, "Lib", local)
            return (await host.hive_brains(inst))["brains"]["Lib"]["running"]
        finally:
            brain_use.release("Lib", "turn-1")
    assert asyncio.run(held()) == 1
    assert (ws / "Projects" / "Lib" / "AGENTS.md").read_text() == "# Lib\n"


def test_the_hive_checks_size_and_digest_before_reading(hive_app, monkeypatch):
    app, _ws, _r, _hb = hive_app
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    files = {"AGENTS.md": b"# x\n"}
    data = bs.pack(files)

    async def go(headers, body=data):
        async with _client_for(app) as c:
            return (await c.put("/api/hive/brains/Lib", content=body, headers=headers)).status_code
    assert asyncio.run(go({"X-Brain-Digest": "nope"})) == 400
    assert asyncio.run(go({"X-Brain-Digest": "0" * 64})) == 400              # wrong digest
    assert asyncio.run(go({"X-Brain-Digest": bs.content_digest(files),
                           "Content-Length": str(bs.MAX_PACKAGE_BYTES + 1)}, b"")) == 413
    assert asyncio.run(go({"X-Brain-Digest": bs.content_digest(files)})) == 200


def test_a_draft_stays_on_the_host_but_the_default_brains_sync(tmp_path, monkeypatch):
    from core import brain_sync_host as host
    local = tmp_path / "host"
    _brain(local, doc={**DEF, "status": "draft"})
    with pytest.raises(host.SyncRefused, match="confirmed"):
        host._package(local, "Lib", default=False)
    _brain(local, "Other", doc={**DEF, "placements": ["chat-tab"]})
    with pytest.raises(host.SyncRefused):
        host._package(local, "Other", default=False)
    _brain(local, "SwarmAI", doc={**DEF, "status": "draft"})
    data, digest, n = host._package(local, "SwarmAI", default=True)
    assert n and len(digest) == 64


def test_console_rows_say_why_a_brain_cannot_be_sent():
    from core import brain_sync_host as host
    local = {"brain": "Lib", "status": "draft", "hive": True, "digest": "a" * 64, "error": None}
    row = host._row(local, None, True)
    assert not row["dispatchable"] and "its Project Agent is a draft" in row["reasons"] and "not synced" in row["reasons"]
    row = host._row({**local, "status": "reviewed", "hive": False}, {"digest": "a" * 64}, True)
    assert row["reasons"] == ["its Project Agent does not allow the Hive"] and row["synced"]
    row = host._row({**local, "status": "reviewed"}, None, False)
    assert "this Hive's version is too old to receive Brains" in row["reasons"] and not row["syncable"]
    assert not row["default"] and host._row({**local, "brain": "SwarmAI"}, None, True)["default"]
    assert host._row({**local, "brain": "General"}, None, True)["default"]
    assert host._clean_remote({"brain": "Lib", "digest": "<script>", "changed_files": -1}) == {
        "brain": "Lib", "digest": None, "current_digest": None, "installed_at": None,
        "changed_files": None, "running": None}


def test_the_hive_reads_the_same_knowledge_units_as_the_host(tmp_path, monkeypatch):
    """Every file the host's unit table reads (docs, archives, corpus, data contract)
    travels, so `knowledge find` on the Hive finds the same entries with the same ids."""
    import core.addressing as ad
    b = _brain(tmp_path)
    (b / "2-understanding" / "TECH.md").write_text("## Rules\n- keep the lock\n")
    (b / "2-understanding" / "IMPROVEMENT-archive.md").write_text("## Old\n- an old lesson\n")
    host_files = {str(p.relative_to(b)) for _s, _d, p in ad._all_files(b, [])}
    assert {"2-understanding/IMPROVEMENT-archive.md", "assets/data-source/data-contract/entities.md",
            "2-understanding/knowledge/note.md"} <= host_files
    files = bs.collect(b, general=False)
    assert host_files <= set(files)
    hive = tmp_path / "hive"
    bs.install(hive, "Lib", files, bs.content_digest(files))
    monkeypatch.setattr(ad, "project_root", lambda name: hive / "Projects" / name)
    on_hive = {(u.rel, u.line, u.id) for u in ad.ddd_units("Lib").units}
    monkeypatch.setattr(ad, "project_root", lambda name: tmp_path / "Projects" / name)
    assert on_hive and on_hive == {(u.rel, u.line, u.id) for u in ad.ddd_units("Lib").units}


def test_the_host_needs_the_hive_to_confirm_this_version(tmp_path, monkeypatch):
    from core import brain_sync_host as host
    import core.swarm_out as so
    local = tmp_path / "host"
    _brain(local)

    def handler(request):
        return httpx.Response(200, json={"brain": "Lib", "digest": "1" * 64})
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://hive"))
    with pytest.raises(host.SyncRefused, match="did not confirm"):
        asyncio.run(host.sync({"name": "hive3"}, "Lib", local))


# ── review round 1 ───────────────────────────────────────────────────────────

def test_the_hive_recomputes_the_hosts_revision_and_gates_digest(tmp_path):
    """Files the whitelist keeps home (a .gitkeep, a dotfile in a skill) are outside the
    revision too: a run dispatched at the host's revision is not refused on the Hive."""
    from core import project_agent as pa
    doc = {**DEF, "modes": [{"id": "research", "when": "a question", "profile": "research",
                              "skills": ["s_lib-release"]}]}
    b = _brain(tmp_path, doc=doc)
    (b / "3-gates" / ".gitkeep").write_text("")
    (b / "3-gates" / ".scratch.py").write_text("print(1)\n")
    (b / "4-capabilities" / "s_lib-release" / ".notes").write_text("local\n")
    files = bs.collect(b, general=False)
    hive = tmp_path / "hive"
    bs.install(hive, "Lib", files, bs.content_digest(files))
    on_hive = hive / "Projects" / "Lib"
    assert pa.revision(b) and pa.revision(on_hive) == pa.revision(b)
    assert pa.gates_digest(on_hive) == pa.gates_digest(b)
    assert set(pa.gate_files(on_hive)) == set(pa.gate_files(b)) == {"no_push"}


def test_turns_and_installs_exclude_each_other():
    from core import brain_use

    async def go():
        assert brain_use.begin_install("B")
        assert not brain_use.begin_install("B")          # one install at a time
        waiter = asyncio.create_task(brain_use.hold("B", "t1"))
        await asyncio.sleep(0.01)
        assert not waiter.done() and brain_use.in_use("B") == 0   # waits for the install
        brain_use.end_install("B")
        await asyncio.wait_for(waiter, 1)
        assert brain_use.in_use("B") == 1 and not brain_use.begin_install("B")
        brain_use.release("B", "t1")
        assert brain_use.in_use("B") == 0 and brain_use.begin_install("B")
        brain_use.end_install("B")
    asyncio.run(go())


def test_every_turn_of_a_project_agent_session_holds_its_brain(monkeypatch):
    """SessionRouter._published_turn: a new session's pin, an existing one's record;
    off a Hive nothing is held."""
    from core import brain_use
    from core.session_router import SessionRouter
    import database

    class _Sessions:
        async def get(self, sid):
            return {"metadata": {"project_agent": {"brain": "Lib"}}} if sid == "old" else {}
    import types
    monkeypatch.setattr(database, "db", types.SimpleNamespace(sessions=_Sessions()))
    seen = []

    class _Self:
        async def _published_events(self, agen, **_k):
            seen.append(brain_use.in_use("Lib"))
            yield {"type": "x"}

    async def turn(**kw):
        out = []
        async for ev in SessionRouter._published_turn(_Self(), None, turn_client_id=None, source="chat",
                                                      start_on_session_start=True, writer=None, **kw):
            out.append(ev)
        return out
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    asyncio.run(turn(session_id=None, project_agent={"brain": "Lib"}))
    asyncio.run(turn(session_id="old"))
    asyncio.run(turn(session_id="new"))
    monkeypatch.delenv("SWARMAI_MODE")
    asyncio.run(turn(session_id="old"))
    assert seen == [1, 1, 0, 0] and brain_use.in_use("Lib") == 0


def test_the_skill_refresh_rescans_and_projects(tmp_path, monkeypatch):
    import routers.hive_brains as hb
    from core import ddd_skill_registry
    import core.projection_layer as pl
    from core.skill_manager import skill_manager
    calls = []
    monkeypatch.setattr(ddd_skill_registry, "build_manifest", lambda w, b: calls.append(("manifest", w)))

    async def scan_all():
        calls.append(("scan",))
    monkeypatch.setattr(skill_manager, "scan_all", scan_all)
    monkeypatch.setattr(skill_manager, "workspace_root", skill_manager.workspace_root)

    class _PL:
        def __init__(self, sm):
            pass

        async def project_skills(self, w, allow_all=False):
            calls.append(("project", w, allow_all))
    monkeypatch.setattr(pl, "ProjectionLayer", _PL)
    asyncio.run(hb.refresh_skills(tmp_path))
    assert calls == [("manifest", tmp_path), ("scan",), ("project", tmp_path, True)]
    assert skill_manager.workspace_root == tmp_path


def test_a_failed_install_writes_nothing_and_leaves_no_record(tmp_path):
    hive = tmp_path / "hive"
    files = {"AGENTS.md": b"# v1\n", "3-gates/g.py": b"x\n"}
    bs.install(hive, "Lib", files, bs.content_digest(files))
    b = hive / "Projects" / "Lib"
    (b / "4-capabilities").write_text("a file where a directory belongs\n")
    v2 = {"AGENTS.md": b"# v2\n", "3-gates/g.py": b"y\n", "4-capabilities/s_x/SKILL.md": b"s\n"}
    with pytest.raises(bs.SyncError):
        bs.install(hive, "Lib", v2, bs.content_digest(v2))
    assert (b / "AGENTS.md").read_bytes() == b"# v1\n" and (b / "3-gates" / "g.py").read_bytes() == b"x\n"
    assert not (b / bs.STATE_FILE).exists() and bs.installed_state(hive, "Lib") is None


def test_a_planted_temp_name_is_never_opened(tmp_path, monkeypatch):
    hive = tmp_path / "hive"
    files = {"AGENTS.md": b"# v1\n"}
    bs.install(hive, "Lib", files, bs.content_digest(files))
    victim = tmp_path / "authorized_keys"
    victim.write_text("keep\n")
    names = iter(["aaaa", "bbbb", "cccc"])
    monkeypatch.setattr(bs.secrets, "token_hex", lambda n: next(names))
    os.link(victim, hive / "Projects" / "Lib" / ".AGENTS.md.sync-aaaa.tmp")
    v2 = {"AGENTS.md": b"# v2\n"}
    bs.install(hive, "Lib", v2, bs.content_digest(v2))
    assert victim.read_text() == "keep\n"
    assert (hive / "Projects" / "Lib" / "AGENTS.md").read_bytes() == b"# v2\n"


def test_a_package_that_inflates_too_far_is_refused_before_tar(monkeypatch):
    import gzip as _gz
    data = _gz.compress(b"\0" * (2 << 20))
    monkeypatch.setattr(bs, "MAX_UNPACKED_BYTES", 1 << 20)
    monkeypatch.setattr(bs, "_TAR_SLACK", 0)
    with pytest.raises(bs.SyncError) as e:
        bs.unpack(data, False, "0" * 64)
    assert e.value.status == 413


def test_the_host_reads_only_regular_files_without_following_links(tmp_path, monkeypatch):
    b = _brain(tmp_path)
    secret = tmp_path / "creds"
    secret.write_text("aws_secret_access_key=x\n")
    (b / "2-understanding" / "LINK.md").symlink_to(secret)
    real_walk = bs._walk
    monkeypatch.setattr(bs, "_walk", lambda root, general: real_walk(root, general) + ["2-understanding/LINK.md"])
    with pytest.raises(bs.SyncError):
        bs.collect(b, general=False)


def test_the_hive_record_is_not_read_through_a_link_or_a_fifo(tmp_path):
    hive = tmp_path / "hive"
    files = {"AGENTS.md": b"# v1\n"}
    bs.install(hive, "Lib", files, bs.content_digest(files))
    rec = hive / "Projects" / "Lib" / bs.STATE_FILE
    other = tmp_path / "other.json"
    other.write_text(rec.read_text())
    rec.unlink()
    rec.symlink_to(other)
    assert bs.installed_state(hive, "Lib") is None
    rec.unlink()
    os.mkfifo(rec)
    assert bs.installed_state(hive, "Lib") is None          # returns, does not block
    rec.unlink()
    rec.mkdir()
    assert bs.installed_state(hive, "Lib") is None


def test_an_old_layout_brain_does_not_travel(tmp_path):
    b = _brain(tmp_path)
    (b / "TECH.md").write_text("# TECH\n")
    with pytest.raises(bs.SyncError, match="old layout"):
        bs.collect(b, general=False)


def test_enablement_skills_stay_home():
    assert not bs.whitelisted("4-capabilities/s_repo-to-ddd/SKILL.md", False)
    assert bs.whitelisted("4-capabilities/s_lib-x/SKILL.md", False)


@pytest.mark.parametrize("code,match", [(423, "busy"), (409, "cannot be written"), (413, "larger"),
                                        (400, "did not accept"), (500, r"refused Lib \(500\)")])
def test_the_hosts_message_for_each_refusal_is_its_own(tmp_path, monkeypatch, code, match):
    from core import brain_sync_host as host
    import core.swarm_out as so
    local = tmp_path / "host"
    _brain(local)
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(code, json={"detail": "<b>hive text</b>"})),
        base_url="http://hive"))
    with pytest.raises(host.SyncRefused, match=match) as e:
        asyncio.run(host.sync({"name": "hive3"}, "Lib", local))
    assert "hive text" not in str(e.value)


def test_a_confirmation_that_is_not_json_is_not_a_confirmation(tmp_path, monkeypatch):
    from core import brain_sync_host as host
    import core.swarm_out as so
    local = tmp_path / "host"
    _brain(local)
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(
            200, content=b"{oops", headers={"content-type": "application/json"})), base_url="http://hive"))
    with pytest.raises(host.SyncRefused, match="did not confirm"):
        asyncio.run(host.sync({"name": "hive3"}, "Lib", local))


# ── review round 2 ───────────────────────────────────────────────────────────

def _wrapper_self(body):
    class _Self:
        async def _published_events(self, agen, **_k):
            async for ev in body():
                yield ev
    return _Self()


def _turn(self_, **kw):
    from core.session_router import SessionRouter
    return SessionRouter._published_turn(self_, None, turn_client_id=None, source="chat",
                                         start_on_session_start=True, writer=None, **kw)


def test_the_brain_is_released_however_the_turn_ends(monkeypatch):
    from contextlib import aclosing
    from core import brain_use
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    pin = {"brain": "Lib"}

    async def boom():
        yield {"type": "x"}
        raise RuntimeError("crash")

    async def endless():
        while True:
            yield {"type": "x"}

    async def go():
        with pytest.raises(RuntimeError):
            async for _ in _turn(_wrapper_self(boom), session_id=None, project_agent=pin):
                pass
        assert brain_use.in_use("Lib") == 0
        async with aclosing(_turn(_wrapper_self(endless), session_id=None, project_agent=pin)) as t:
            async for _ in t:
                assert brain_use.in_use("Lib") == 1
                break
        assert brain_use.in_use("Lib") == 0
        # cancelled while it waits for an install: it never took the Brain
        assert brain_use.begin_install("Lib")
        task = asyncio.create_task(_drain(_turn(_wrapper_self(endless), session_id=None, project_agent=pin)))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        brain_use.end_install("Lib")
        assert brain_use.in_use("Lib") == 0 and brain_use.begin_install("Lib")
        brain_use.end_install("Lib")
    asyncio.run(go())


async def _drain(agen):
    async for _ in agen:
        pass


def test_a_turn_whose_record_cannot_be_read_holds_every_brain(monkeypatch):
    import types
    import database
    from core import brain_use

    class _Down:
        async def get(self, sid):
            raise RuntimeError("database is locked")
    monkeypatch.setattr(database, "db", types.SimpleNamespace(sessions=_Down()))
    monkeypatch.setenv("SWARMAI_MODE", "hive")

    async def go():
        assert await brain_use.brain_of_turn("s1", None) == brain_use.ANY
        await brain_use.hold(brain_use.ANY, "t")
        refused = not brain_use.begin_install("Lib")
        brain_use.release(brain_use.ANY, "t")
        assert brain_use.begin_install("Lib")
        waiter = asyncio.create_task(brain_use.hold(brain_use.ANY, "t2"))
        await asyncio.sleep(0.01)
        waiting = not waiter.done()
        brain_use.end_install("Lib")
        await asyncio.wait_for(waiter, 1)
        brain_use.release(brain_use.ANY, "t2")
        return refused, waiting
    assert asyncio.run(go()) == (True, True)


def test_a_cancelled_install_keeps_the_brain_marked_until_the_files_are_written(hive_app, monkeypatch):
    import threading
    app, ws, _r, hb = hive_app
    from core import brain_use
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    files = {"AGENTS.md": b"# x\n"}
    data = bs.pack(files)
    started, release = threading.Event(), threading.Event()
    real = bs.install

    def slow(*a):
        started.set()
        release.wait(5)
        return real(*a)
    monkeypatch.setattr(bs, "install", slow)

    async def go():
        req = asyncio.create_task(_put(app, data, bs.content_digest(files)))
        while not started.is_set():
            await asyncio.sleep(0.01)
        req.cancel()
        await asyncio.sleep(0.05)
        marked = not brain_use.begin_install("Lib")       # still installing
        release.set()
        try:
            await req
        except BaseException:  # noqa: BLE001
            pass
        free = brain_use.begin_install("Lib")
        brain_use.end_install("Lib")
        return marked, free
    assert asyncio.run(go()) == (True, True)
    assert (ws / "Projects" / "Lib" / "AGENTS.md").read_text() == "# x\n"


def test_a_second_cancel_does_not_unmark_the_brain_mid_write(hive_app, monkeypatch):
    """The wait for the install thread used to end on a second cancel, unmarking the
    Brain while its files were still being written."""
    import threading
    app, ws, _r, hb = hive_app
    from core import brain_use
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    files = {"AGENTS.md": b"# y\n"}
    data = bs.pack(files)
    started, release = threading.Event(), threading.Event()
    real = bs.install

    def slow(*a):
        started.set()
        release.wait(5)
        return real(*a)
    monkeypatch.setattr(bs, "install", slow)

    async def go():
        req = asyncio.create_task(_put(app, data, bs.content_digest(files)))
        while not started.is_set():
            await asyncio.sleep(0.01)
        req.cancel()
        await asyncio.sleep(0.05)
        req.cancel()
        await asyncio.sleep(0.05)
        marked = not brain_use.begin_install("Lib")       # still installing
        release.set()
        try:
            await req
        except BaseException:  # noqa: BLE001
            pass
        free = brain_use.begin_install("Lib")
        brain_use.end_install("Lib")
        return marked, free
    assert asyncio.run(go()) == (True, True)


async def _put(app, data, digest):
    async with _client_for(app) as c:
        return await c.put("/api/hive/brains/Lib", content=data, headers={"X-Brain-Digest": digest})


def test_a_gate_file_whose_name_does_not_travel_is_reported(tmp_path):
    from core import project_agent as pa
    b = _brain(tmp_path)
    (b / "3-gates" / "_credentials_patterns.py").write_text("X = 1\n")
    problems = pa.validate(yaml.safe_load((b / "project-agent.yaml").read_text()), b)
    assert any("_credentials_patterns.py is not used" in p for p in problems)
