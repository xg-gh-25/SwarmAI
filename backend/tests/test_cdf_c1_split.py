"""C1 (design 2026-10-30-cdf-agent-decides step 2): the taint mark means one thing.

Questions:
- F1: a session that read external content but whose unit cites only the user is
  ``read_external`` and not ``cited_external``; a unit citing content descended from
  external content is ``cited_external``.
- F1b: an entry CDF wrote is registered in the baseline with the trust of its recorded
  basis (inferred, never the user's by default), and a unit citing it is at most
  inferred and not external because of it; a person's entry is the user's; a marked
  entry is external; with the store unreadable a CDF entry is inferred.
- v8 → v9: a backup is taken and checked before the evidence_roots rebuild; rows are
  kept; ``inferred`` is accepted; ``unit_basis`` exists.
- the one-time unmark: an entry marked only because it was rerouted loses the mark and
  is registered inferred; one citing external content keeps it; an entry citing a
  now-inferred entry follows on the next round (fixed point); a second run changes
  nothing.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401  (fixture)


def test_f1_read_external_is_not_cited_external(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from tests.test_cdf_v2_provenance import _session_root
    from core.ingestion.api_v2 import register_root, resolve_evidence
    user = _session_root(monkeypatch, tmp_path, "s1", "input:e1", "keep one owner")
    web = register_root("web_document", "https://example.com/a", "a page", "external")
    doc = register_root("agent_document", "s1:summary", "summary of the page", "user",
                        parent_root_ids=(web,))
    only_user = resolve_evidence(["event:input:e1"], session_id="s1", run_id=None, read_root_ids=(web,))
    assert only_user.read_external and not only_user.cited_external
    assert user in only_user.lineage_root_ids
    via_doc = resolve_evidence([f"doc:{doc}"], session_id="s1", run_id=None)
    assert via_doc.cited_external and via_doc.read_external


def _ddd_entry(cdf_env, title, cand, *, marked=False):
    path = cdf_env["ddd"] / "TECH.md"
    meta = "  <!-- ref:0 | last:2026-10-29 | decay:active | source:S7" + (" | taint:external" if marked else "") + " -->"
    text = path.read_text(encoding="utf-8")
    text = text.replace("## Conventions\n", f"## Conventions\n\n- [guideline] **{title}** {title} holds. <!-- cand:{cand}.unit -->\n{meta}\n", 1)
    path.write_text(text, encoding="utf-8")
    return path


def _basis(cand, basis):
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        conn.execute("INSERT INTO unit_basis(unit_id,basis,recorded_at) VALUES(?,?,'now')", (f"{cand}.unit", basis))
        conn.commit()
    finally:
        conn.close()


def _trust_of(manifest, title):
    for f in manifest["files"].values():
        for u in f["units"]:
            if title in str(u.get("identity")) or title in str(u):
                return u
    raise AssertionError(title)


def test_f1b_the_baseline_registers_each_entry_with_what_it_rests_on(cdf_env):
    from core.ingestion.store import connect_rw
    from core.ingestion.units import parse_units
    from core.ingestion.writer import ensure_baseline
    connect_rw().close()
    path = cdf_env["ddd"] / "TECH.md"
    if "## Conventions" not in path.read_text(encoding="utf-8"):
        path.write_text("# Tech\n\n## Conventions\n", encoding="utf-8")
    _ddd_entry(cdf_env, "Inferred rule", "cand-aaaa")
    _ddd_entry(cdf_env, "Legacy rule", "cand-bbbb")
    _ddd_entry(cdf_env, "Marked rule", "cand-cccc", marked=True)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("## Conventions\n", "## Conventions\n\n- [guideline] **Person rule** Written by hand.\n", 1),
                    encoding="utf-8")
    _basis("cand-aaaa", "inferred")
    manifest = ensure_baseline()
    units = {u["identity"]: u for u in manifest["files"]["Projects/P/2-understanding/TECH.md"]["units"]}
    assert units["cand:cand-aaaa.unit"]["trust"] == "inferred"
    assert units["cand:cand-bbbb.unit"]["trust"] == "inferred"    # no recorded basis: never the user's (I4)
    assert units["cand:cand-cccc.unit"]["trust"] == "external"
    person = next(u for k, u in units.items() if not k.startswith("cand:"))
    assert person["trust"] == "user"
    # a unit citing the inferred entry is at most inferred and not external
    from core.ingestion.api_v2 import resolve_evidence
    unit = next(u for u in parse_units(path, root=cdf_env["workspace"]) if u.identity == "cand:cand-aaaa.unit")
    anchor = f"Projects/P/2-understanding/TECH.md#{unit.section}#{unit.identity}"
    resolved = resolve_evidence([f"unit:{anchor}"], session_id=None, run_id=None)
    assert resolved.strength == "inferred" and not resolved.cited_external


def test_f1b_an_unreadable_store_never_registers_a_cdf_entry_as_the_users():
    from core.ingestion.writer import _unit_trust
    cdf = SimpleNamespace(identity="cand:cand-x.unit", taint=None, text="- [guideline] **x** y")
    person = SimpleNamespace(identity="x-hash", taint=None, text="- [guideline] **x** y")
    assert _unit_trust(cdf, None) == "inferred"
    assert _unit_trust(person, None) == "user"
    assert _unit_trust(cdf, {"cand-x.unit": "measured"}) == "measured"


def test_a_v8_store_is_backed_up_then_migrated_to_v9(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.store import connect_rw, ingestion_db_path
    conn = connect_rw()
    # take the store back to v8: no unit_basis, the old trust CHECK
    conn.executescript("""
        BEGIN IMMEDIATE;
        DROP TABLE unit_basis;
        CREATE TABLE evidence_roots_old (
          root_id TEXT PRIMARY KEY, root_kind TEXT NOT NULL, locator TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          trust TEXT NOT NULL CHECK (trust IN ('user','measured','external')),
          parent_root_ids TEXT NOT NULL DEFAULT '[]', observed_at TEXT NOT NULL, created_at TEXT NOT NULL);
        DROP TABLE evidence_roots;
        ALTER TABLE evidence_roots_old RENAME TO evidence_roots;
        INSERT INTO evidence_roots VALUES ('r1','user_message','s:input:1','h','user','[]','t','t');
        PRAGMA user_version=8;
        COMMIT;""")
    conn.close()
    migrated = connect_rw()
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 11
        assert migrated.execute("SELECT trust FROM evidence_roots WHERE root_id='r1'").fetchone()[0] == "user"
        migrated.execute("INSERT INTO evidence_roots VALUES ('r2','corpus_unit','x','h','inferred','[]','t','t')")
        migrated.execute("INSERT INTO unit_basis VALUES ('u','inferred','now')")
    finally:
        migrated.close()
    backups = list(Path(ingestion_db_path()).parent.glob("ingestion.db.pre-v11-from-v8-*.bak"))
    assert len(backups) == 1
    check = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
    try:
        assert check.execute("SELECT root_id FROM evidence_roots").fetchall() == [("r1",)]
    finally:
        check.close()


def _candidate(conn, cand, lineage):
    conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
                 "content_hash,envelope,project_snapshot,created_at) VALUES(?,?,'S7','s','immediate','{}',"
                 "'v','h',?,'{}','now')", (cand, cand, json.dumps({"lineage_root_ids": lineage})))


def test_the_one_time_unmark_reaches_a_fixed_point(cdf_env):
    from core.ingestion.api_v2 import register_root
    from core.ingestion.store import connect_rw
    from core.ingestion.writer import ensure_baseline
    from scripts import cdf_unmark_inferred as mig
    path = cdf_env["ddd"] / "TECH.md"
    if "## Conventions" not in path.read_text(encoding="utf-8"):
        path.write_text("# Tech\n\n## Conventions\n", encoding="utf-8")
    _ddd_entry(cdf_env, "Rerouted rule", "cand-1111", marked=True)
    _ddd_entry(cdf_env, "Web rule", "cand-2222", marked=True)
    _ddd_entry(cdf_env, "Follower rule", "cand-3333", marked=True)
    ensure_baseline()
    user = register_root("user_message", "s:input:1", "the rule", "user")
    web = register_root("web_document", "https://example.com/w", "page", "external")
    conn = connect_rw()
    try:
        # the follower cites the rerouted entry's root, external while it is marked
        rerouted_root = conn.execute("SELECT root_id FROM evidence_roots WHERE root_kind='corpus_unit' "
                                     "AND locator LIKE '%#cand:cand-1111.unit'").fetchone()[0]
        _candidate(conn, "cand-1111", [user])
        _candidate(conn, "cand-2222", [web])
        _candidate(conn, "cand-3333", [rerouted_root])
        conn.commit()
    finally:
        conn.close()
    assert mig.run(cdf_env["workspace"], apply=False) == {"unmark": 2, "stay_external": 1, "applied": False}
    got = mig.run(cdf_env["workspace"], apply=True)
    assert (got["unmark"], got["stay_external"], got["files"]) == (2, 1, 1)
    text = path.read_text(encoding="utf-8")
    for title, marked in [("Rerouted rule", False), ("Follower rule", False), ("Web rule", True)]:
        block = text[text.index(title):].split("\n", 2)[1]
        assert ("taint:external" in block) is marked, (title, block)
    units = {u["identity"]: u["trust"] for u in ensure_baseline()["files"]["Projects/P/2-understanding/TECH.md"]["units"]}
    assert units["cand:cand-1111.unit"] == units["cand:cand-3333.unit"] == "inferred"
    assert units["cand:cand-2222.unit"] == "external"
    before = path.read_bytes()
    again = mig.run(cdf_env["workspace"], apply=True)
    assert (again["unmark"], again["files"]) == (0, 0) and path.read_bytes() == before
