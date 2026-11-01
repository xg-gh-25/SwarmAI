"""The cognition entrance: one command reads the whole brain, each store as whole units
(cognition entrance design, docs/design/2026-11-01-cognition-entrance.md §7.2)."""
import json
import re
from pathlib import Path

import pytest

from core import cognition, knowledge_cli

FIXTURES = Path(__file__).resolve().parent / "fixtures"

EVOLUTION_ARCHIVE = """# Evolution archive

## Distilled

### C010 | 2026-06-01
- **Correction**: the first lesson about quokkas.
- **Status**: active

### C011 | 2026-06-02
Plain prose under the block, not a bullet: wombats are slow.

More prose of the same block after a blank line.

```text
### not a heading: inside a fence
```

## Folded

Intro paragraph of the folded section.

### (folded from: ### CLASS B [2 entries])
- **DATA-POINT** one about numbats.
"""

TIMELINE = """# Timeline

## 2026-10

### 2026-10-29 Quokka decision
- what: chose quokkas.
<!-- cand:cand-1.unit | event_kind:event -->

## Not injected

### 2026-10-30 Numbat landing
- what: numbats landed.
- outcome: fine.

### 2026-10-31 Wombat incident
- what: a wombat dug under the fence.
<!-- cand:cand-2.unit | source:S7 | taint:external -->
"""

MEMORY = """# Memory

## Principles

- [principle] **Quokkas first** — always ask the quokka.
  <!-- ref:0 | last:none | decay:active -->

- [principle] **Numbats second** — then the numbat.
"""

SOUL = """# Soul

You are a teammate. Prose paragraph about echidnas.

## P1

Verify before you assert: an echidna is not a hedgehog.
"""


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("SWARMWS", str(tmp_path))
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv(knowledge_cli.RESTRICTED_ENV, raising=False)
    monkeypatch.delenv(knowledge_cli.OFF_ENV, raising=False)
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "EVOLUTION-archive.md").write_text(EVOLUTION_ARCHIVE, encoding="utf-8")
    (ctx / "TIMELINE.md").write_text(TIMELINE, encoding="utf-8")
    (ctx / "MEMORY.md").write_text(MEMORY, encoding="utf-8")
    (ctx / "SOUL.md").write_text(SOUL, encoding="utf-8")
    lib = tmp_path / "Knowledge" / "Library"
    (lib / "Links").mkdir(parents=True)
    (lib / "general-knowledge.md").write_text(
        "# General\n\n## Lessons\n\n- [guideline] **Platypus rule** — platypus lay eggs.\n",
        encoding="utf-8")
    (lib / "second.md").write_text("# Second\n\nA paragraph about kookaburras.\n", encoding="utf-8")
    (lib / "Links" / "deep.md").write_text("# Deep\n\nA corpus note about dingoes.\n", encoding="utf-8")
    und = tmp_path / "Projects" / "P" / "2-understanding"
    und.mkdir(parents=True)
    (und / "TECH.md").write_text("# TECH\n\n## Traps\n\n- [pitfall] **Bilby rule** — bilbies.\n",
                                 encoding="utf-8")
    return tmp_path


def _text_lines(text: str) -> list[str]:
    """The lines a reader needs: not blank, not a comment, not a bare heading."""
    return [ln for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("<!--") and not re.match(r"^#{1,2} ", ln)]


# 1 — nothing is left out, and every unit reads back by path:line
@pytest.mark.parametrize("store,name", [("evolution", "EVOLUTION-archive.md"),
                                        ("timeline", "TIMELINE.md"), ("memory", "MEMORY.md"),
                                        ("context", "SOUL.md")])
def test_every_line_is_in_a_unit_and_every_unit_reads_back(ws, store, name):
    path = ws / ".context" / name
    units = cognition.file_units(store, path)
    covered = "\n".join(u.text for u in units)
    missing = [ln for ln in _text_lines(path.read_text(encoding="utf-8")) if ln not in covered]
    assert not missing, missing
    for u in units:
        back = cognition.get(f"{u.path}:{u.line}")
        assert back and back[0].text == u.text and back[0].id == u.id


def test_evolution_archive_blocks_are_whole_units(ws):
    units = cognition.file_units("evolution", ws / ".context" / "EVOLUTION-archive.md")
    blocks = [u for u in units if u.text.startswith("### ")]
    assert [u.text.splitlines()[0] for u in blocks] == [
        "### C010 | 2026-06-01", "### C011 | 2026-06-02", "### (folded from: ### CLASS B [2 entries])"]
    c011 = blocks[1].text
    assert "wombats are slow" in c011 and "More prose" in c011 and "inside a fence" in c011


# 2 — TIMELINE events not injected are units of their own
def test_timeline_not_injected_events_are_separate_units(ws):
    units = [u for u in cognition.file_units("timeline", ws / ".context" / "TIMELINE.md")
             if u.section == "Not injected"]
    assert [u.text.splitlines()[0] for u in units] == [
        "### 2026-10-30 Numbat landing", "### 2026-10-31 Wombat incident"]
    assert len({u.id for u in units}) == 2 and units[0].line < units[1].line


def test_find_in_timeline_marks_external_and_prints_location(ws, capsys):
    assert knowledge_cli.main(["find", "--in", "timeline", "--query", "wombat fence"], prog="cognition") == 0
    out = capsys.readouterr().out
    assert "[TIMELINE] 1 matching" in out and "TIMELINE.md:" in out and "(external)" in out
    assert "a wombat dug under the fence" in out


# 3 — the off switch: both names refuse and read nothing
@pytest.mark.parametrize("prog", ["cognition", "knowledge"])
@pytest.mark.parametrize("argv", [["map"], ["map", "--projects"],
                                  ["find", "--query", "quokka"], ["get", "deadbeef"]])
def test_off_switch_refuses_and_reads_nothing(ws, monkeypatch, capsys, prog, argv):
    monkeypatch.setenv(knowledge_cli.OFF_ENV, "1")
    opened = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: opened.append(self) or real(self, *a, **k))
    assert knowledge_cli.main(argv, prog=prog) == 3
    assert f"{prog}: not available in this session" in capsys.readouterr().out
    assert opened == []


# 4 — the spawn environment follows push recall's channel rule
@pytest.mark.parametrize("ctx,off", [
    (None, False),
    ({"is_owner": True, "chat_type": "im"}, False),
    ({"is_owner": True, "chat_type": "channel", "is_group": True}, True),
    ({"is_owner": False, "chat_type": "channel", "is_group": True}, True),
    ({"is_owner": False, "chat_type": "im"}, True),
    ({"is_owner": True, "is_group": False}, True),      # no chat type: not the private chat
])
def test_spawn_env_sets_off_where_recall_is_not_allowed(ctx, off):
    from core.prompt_builder import with_cognition_gate
    env = with_cognition_gate({"A": "1"}, ctx)
    assert (knowledge_cli.OFF_ENV in env) is off and env["A"] == "1"


def test_spawn_env_uses_the_push_recall_rule_itself(monkeypatch):
    from core import session_router
    from core.prompt_builder import with_cognition_gate
    monkeypatch.setattr(session_router, "_channel_recall_allowed", lambda c: True)
    assert knowledge_cli.OFF_ENV not in with_cognition_gate({}, {"is_owner": False, "chat_type": "channel"})


def test_prewarmed_owner_unit_is_adopted_only_into_the_private_chat():
    from channels.gateway import ChannelGateway
    from channels.base import PermissionTier, SenderIdentity
    gw = ChannelGateway.__new__(ChannelGateway)
    gw._prewarmed_session_id, gw._prewarmed_owner_id = "pw", "U1"
    owner = SenderIdentity("U1", "Owner", PermissionTier.OWNER, True)
    assert gw._adopts_prewarm(True, True, "im", owner)
    assert not gw._adopts_prewarm(True, True, "channel", owner)
    assert not gw._adopts_prewarm(True, True, "", owner)
    assert not gw._adopts_prewarm(False, True, "im", owner)


# 5 — the Bash handler's guard knows the new name
@pytest.mark.parametrize("cmd", ['"$SWARM_BACKEND_BIN" cognition find --query x',
                                 '"$SWARM_BACKEND_BIN" cogni\'\'tion map'])
def test_denial_covers_cognition(cmd):
    from core.security_hooks import knowledge_command_denial
    assert knowledge_command_denial(cmd)


# 6 — what `knowledge` printed before is printed the same
def test_knowledge_output_is_unchanged_from_main(tmp_path, monkeypatch):
    """The fixture and the recorded output are tests/fixtures/knowledge_cli_golden*.
    (made with main's backend by knowledge_cli_golden_make.py before this change)."""
    import contextlib
    import io
    golden = json.loads((FIXTURES / "knowledge_cli_golden.json").read_text(encoding="utf-8"))
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("SWARMWS", str(tmp_path))
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv(knowledge_cli.OFF_ENV, raising=False)
    und = tmp_path / "Projects" / "P" / "2-understanding"
    und.mkdir(parents=True)
    (und / "TECH.md").write_text(
        "# TECH\n\n## Traps\n\n- [pitfall] **Quokka rule** — never feed quokkas after midnight.\n"
        "  <!-- ref:0 | last:none | decay:active | source:manual -->\n\n"
        "- [pitfall] **Wombat rule** — wombats dig at night, quokkas do not.\n", encoding="utf-8")
    argvs = {
        "find": ["find", "--project", "P", "--query", "quokkas midnight"],
        "find_json": ["find", "--project", "P", "--query", "quokkas", "--json"],
        "nothing": ["find", "--project", "P", "--query", "zzzunmatched"],
        "unknown": ["find", "--project", "Nope", "--query", "x"],
        "map": ["map", "--project", "P"],
        "get_bad": ["get", "--project", "P", "deadbeef"],
        "lib_json": ["find", "--in", "library", "--query", "quokkas", "--json"],
    }
    for name, argv in argvs.items():
        for prog in ("knowledge", "cognition"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = knowledge_cli.main(argv, prog=prog)
            # the command's own name may differ (the map's how-to names the command);
            # everything else is byte for byte what main printed
            got = (buf.getvalue().replace(str(tmp_path), "<WS>")
                   .replace("knowledge ", "<CMD> ").replace("cognition ", "<CMD> "))
            want = golden[name][1].replace("knowledge ", "<CMD> ")
            assert [code, got] == [golden[name][0], want], (name, prog)


# 7 — every push-recall domain has an on-demand store
def test_every_recall_domain_has_a_store(monkeypatch):
    from core import recall_multi
    covered = {s.domain for s in cognition.STORES.values()}
    assert set(recall_multi.DOMAINS) <= covered
    monkeypatch.setattr(recall_multi, "DOMAINS", recall_multi.DOMAINS + ("new_leg",))
    assert not set(recall_multi.DOMAINS) <= {s.domain for s in cognition.STORES.values()}


# 8 — what the entrance shows of a CDF file is recorded as used
def test_find_in_memory_records_usage(ws, monkeypatch, capsys):
    calls = []
    import core.ingestion.usage as usage
    monkeypatch.setattr(usage, "record_usage", lambda anchor, kind, session: calls.append((anchor, kind)))
    assert knowledge_cli.main(["find", "--in", "memory", "--query", "quokka ask"], prog="cognition") == 0
    capsys.readouterr()
    assert calls and all(kind == "recall_injected" and "MEMORY.md" in a for a, kind in calls)


# 9 — stores are ranked apart: a small store's match is not crowded out
def test_small_store_hit_shows_in_its_own_group(ws, capsys):
    assert knowledge_cli.main(["find", "--in", "all", "--query", "echidna hedgehog"], prog="cognition") == 0
    out = capsys.readouterr().out
    assert "[Context] " in out and "an echidna is not a hedgehog" in out


# 10 — the General Brain is a Brain
def test_general_brain_find_get_map(ws, capsys):
    assert knowledge_cli.main(["find", "--project", "General", "--query", "platypus eggs"], prog="cognition") == 0
    out = capsys.readouterr().out
    m = re.search(r"- `([0-9a-f]{8})` · (\S+general-knowledge\.md):(\d+)", out)
    assert m and "platypus lay eggs" in out
    assert out.startswith("General Brain — ") and "DDD of General" not in out
    assert knowledge_cli.main(["get", "--project", "general", m.group(1)], prog="cognition") == 0
    assert "Platypus rule" in capsys.readouterr().out
    assert knowledge_cli.main(["find", "--project", "General", "--query", "dingoes"], prog="cognition") == 0
    assert "Links/deep.md" in capsys.readouterr().out
    assert knowledge_cli.main(["map", "--projects"], prog="cognition") == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("General: ")


def test_all_search_lists_library_hits_of_the_general_brain_once(ws, monkeypatch, capsys):
    from core.run_protocol import run_knowledge
    monkeypatch.setattr(run_knowledge, "_library_leg", lambda q: [
        {"kind": "library", "source": "Library/general-knowledge.md", "id": "aaaaaaaa", "text": "platypus lay eggs"},
        {"kind": "library", "source": "Notes/n.md", "id": "bbbbbbbb", "text": "platypus note"}])
    assert knowledge_cli.main(["find", "--in", "all", "--query", "platypus eggs"], prog="cognition") == 0
    out = capsys.readouterr().out
    assert "[General Brain]" in out and "Notes/n.md" in out
    assert "Library/general-knowledge.md\n" not in out.replace("Knowledge/Library/general-knowledge.md", "")


def test_worker_hive_reads_only_brains_and_code(ws, monkeypatch, capsys):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setenv("SWARMAI_ROLE", "worker")
    assert knowledge_cli.main(["find", "--in", "memory", "--query", "quokka"], prog="cognition") == 0
    out = capsys.readouterr().out
    assert "not read on a worker Hive" in out and "Quokkas first" not in out
    assert knowledge_cli.main(["find", "--project", "P", "--query", "bilbies"], prog="cognition") == 0
    assert "Bilby rule" in capsys.readouterr().out


# review round 1 — every route keeps the worker rule
@pytest.mark.parametrize("argv", [["get", "MEMORY.md:5"], ["find", "--in", "memory,session", "--query", "x"]])
def test_worker_refuses_every_route_to_its_private_stores(ws, monkeypatch, capsys, argv):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setenv("SWARMAI_ROLE", "worker")
    assert knowledge_cli.main(argv, prog="cognition") in (0, 3)
    out = capsys.readouterr().out
    assert "not read on a worker Hive" in out and "Quokkas" not in out


@pytest.mark.parametrize("where", ["library", "library,ddd"])
def test_worker_library_search_returns_only_the_synced_general_brain(ws, monkeypatch, capsys, where):
    from core.run_protocol import run_knowledge
    monkeypatch.setattr(run_knowledge, "_library_leg", lambda q: [
        {"kind": "library", "source": "Library/second.md", "id": "aaaaaaaa", "text": "kookaburra general"},
        {"kind": "library", "source": "Notes/other-dispatch.md", "id": "bbbbbbbb", "text": "kookaburra private"}])
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setenv("SWARMAI_ROLE", "worker")
    argv = ["find", "--in", where, "--query", "kookaburra"] + (["--project", "P"] if "ddd" in where else [])
    assert knowledge_cli.main(argv, prog="cognition") == 0
    out = capsys.readouterr().out
    assert "kookaburra private" not in out and "kookaburra" in out


def test_worker_refuses_get_by_id_but_reads_a_brain(ws, monkeypatch, capsys):
    uid = cognition.file_units("memory", ws / ".context" / "MEMORY.md")[0].id
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setenv("SWARMAI_ROLE", "worker")
    assert knowledge_cli.main(["get", uid], prog="cognition") == 3
    from core.addressing import find
    bid = find("P", "bilbies").units[0].id
    assert knowledge_cli.main(["get", "--project", "P", bid], prog="cognition") == 0
    assert "Bilby rule" in capsys.readouterr().out


# review round 1 — multi-store search edges
def test_library_hits_of_knowledge_library_stay_when_no_brain_is_searched(ws, monkeypatch, capsys):
    from core.run_protocol import run_knowledge
    monkeypatch.setattr(run_knowledge, "_library_leg", lambda q: [
        {"kind": "library", "source": "Library/general-knowledge.md", "id": "aaaaaaaa", "text": "platypus lay eggs"},
        {"kind": "library", "source": "", "id": None, "text": "2 more hits not shown"}])
    assert knowledge_cli.main(["find", "--in", "library,memory", "--query", "platypus"], prog="cognition") == 0
    out = capsys.readouterr().out
    assert "Library/general-knowledge.md" in out and "2 more hits not shown" in out
    assert "`None`" not in out


@pytest.mark.parametrize("argv,code", [
    (["find", "--in", "nosuch", "--query", "x"], 2),
    (["find", "--in", "", "--query", "x"], 2),
    (["find", "--in", "all", "--project", "Nope", "--query", "x"], 1),
    (["find", "--in", "memory,timeline", "--doc", "MEMORY.md", "--query", "x"], 2),
])
def test_bad_multi_store_arguments_fail_loudly(ws, capsys, argv, code):
    assert knowledge_cli.main(argv, prog="cognition") == code


def test_top_zero_still_returns_the_best_unit(ws, capsys):
    tech = ws / "Projects" / "P" / "2-understanding" / "TECH.md"
    tech.write_text(tech.read_text(encoding="utf-8") + "\n- [pitfall] **Second bilby** — more bilbies.\n",
                    encoding="utf-8")
    assert knowledge_cli.main(["find", "--project", "P", "--query", "bilbies", "--top", "0", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["units"]) == 1


# review round 1 — parsing edges
def test_an_unclosed_fence_never_merges_the_blocks_after_it(ws):
    p = ws / ".context" / "EVOLUTION.md"
    p.write_text("# E\n\n## Corrections\n\n### C2 | one\n- text\n```\nstray\n\n### C3 | two\n- more\n\n"
                 "## Later\n\n### C4 | three\n", encoding="utf-8")
    heads = [u.text.splitlines()[0] for u in cognition.file_units("evolution", p)]
    assert "### C3 | two" in heads and "### C4 | three" in heads
    c4 = [u for u in cognition.file_units("evolution", p) if u.text.startswith("### C4")][0]
    assert c4.section == "Later"


def test_general_brain_file_with_a_ddd_doc_name_reads_back(ws, capsys):
    """Library has no 2-understanding/: ddd_path falls back to the root file."""
    (ws / "Knowledge" / "Library" / "TECH.md").write_text("# T\n\nA paragraph about cassowaries.\n",
                                                          encoding="utf-8")
    assert knowledge_cli.main(["get", "--project", "General", "TECH.md:3"], prog="cognition") == 0
    assert "cassowaries" in capsys.readouterr().out


# review round 1 — what get never reads
@pytest.mark.parametrize("ref", ["/etc/hosts:1", "../Knowledge/Library/second.md:3", "MEMORY.md.lock:1"])
def test_get_never_reads_outside_the_context_stores(ws, capsys, ref):
    assert knowledge_cli.main(["get", ref], prog="cognition") == 1


def test_get_in_the_general_brain_never_leaves_it(ws, capsys):
    notes = ws / "Knowledge" / "Notes"
    notes.mkdir()
    (notes / "x.md").write_text("# X\n\nsecret note\n", encoding="utf-8")
    assert knowledge_cli.main(["get", "--project", "General", "../Notes/x.md:3"], prog="cognition") == 1


def test_a_symlinked_context_file_is_not_read(ws, tmp_path, capsys):
    outside = tmp_path / "outside.md"
    outside.write_text("# O\n\n- [principle] **Leak** — outside text.\n", encoding="utf-8")
    mem = ws / ".context" / "MEMORY.md"
    mem.unlink()
    mem.symlink_to(outside)
    assert knowledge_cli.main(["find", "--in", "memory", "--query", "outside leak"], prog="cognition") == 0
    assert "outside text" not in capsys.readouterr().out


# review round 1 — the spawn wiring itself
@pytest.mark.asyncio
@pytest.mark.parametrize("ctx,off,restricted", [
    ({"channel_id": "c", "channel_type": "slack", "is_owner": True, "is_group": True, "chat_type": "channel",
      "sender_identity": {"permission_tier": "owner", "external_id": "u1"}}, True, False),
    ({"channel_id": "c", "channel_type": "slack", "is_owner": True, "is_group": False, "chat_type": "im",
      "sender_identity": {"permission_tier": "owner", "external_id": "u1"}}, False, False),
    ({"channel_id": "c", "channel_type": "slack", "is_owner": False, "is_group": False, "chat_type": "im",
      "sender_identity": {"permission_tier": "public", "external_id": "u2"}}, True, True),
])
async def test_build_options_puts_the_gate_in_the_cli_environment(ctx, off, restricted, tmp_path, monkeypatch):
    from tests.test_knowledge_restricted_channel import _options
    env = (await _options(ctx, tmp_path, monkeypatch)).env or {}
    assert (env.get(knowledge_cli.OFF_ENV) == "1") is off
    assert (env.get(knowledge_cli.RESTRICTED_ENV) == "1") is restricted
