"""CJK JSON-encoding contract for DB storage (run_a7587134).

WHY THIS EXISTS
---------------
``json.dumps`` defaults to ``ensure_ascii=True``, which turns every non-ASCII
character into a literal ``\\uXXXX`` escape. ``messages.content`` is a JSON list of
content blocks, so every Chinese message was stored as ASCII escapes. Because
``messages_fts`` is an **external-content** FTS5 index over that column, the index
contained zero CJK characters and *every* Chinese query returned 0 hits by
construction — recall could not reach a third of recorded history.

WHAT THESE TESTS PIN
--------------------
1. ``dumps_json`` is the SINGLE encoder authority for DB JSON storage, and both
   write doors route through it (a second parallel encoder is how this class
   recurs — Gate-1 F4).
2. The escaped and raw forms are read-INTERCHANGEABLE, so the writer fix can
   deploy before any backfill without breaking history.
3. FTS searchability is the actual user-visible outcome: an escaped row is
   unfindable, a raw row is findable, and the ``messages_fts_update`` trigger
   reindexes on UPDATE (so a backfill needs no explicit rebuild).

DELIBERATELY NOT ASSERTED: any affected-row COUNT. The number drifts every
minute while the writer is unfixed (26,775 -> 33,953 during the authoring run
alone), so a frozen integer would be a lie within the hour (R30#4). Tests assert
semantic and relative properties only.
"""

import json
import sqlite3
from pathlib import Path

import pytest

# The CJK sample is deliberately mixed-script: a 4-char CJK run, an ASCII word
# glued to a CJK char (the token shape that broke a naive expansion), an
# ideographic space (U+3000) and a curly quote (U+2018) — the last two are the
# rows a naive `\u4`-`\u9` LIKE pattern silently misses (Gate-1 F2).
CJK_TEXT = "对抗审查　用goal-pipeline ‘部署’"
ESCAPE_MARKER = "\\u"


def _blocks(text: str = CJK_TEXT) -> list[dict]:
    """The real production shape of messages.content — a list of content blocks."""
    return [{"type": "text", "text": text}]


# ── AC2: both write doors emit raw CJK ────────────────────────────────────────


class TestSingleEncoderAuthority:
    """AC2 — one encoder, and every write door routes through it."""

    def test_dumps_json_emits_raw_cjk_not_escapes(self):
        """The shared authority must never escape non-ASCII."""
        from database.sqlite import dumps_json

        out = dumps_json(_blocks())
        assert "对抗审查" in out, "CJK must survive as real characters"
        assert ESCAPE_MARKER not in out, f"must not contain an escape: {out[:80]}"
        # U+3000 / U+2018 are the non-CJK-plane characters a naive pattern misses.
        assert "　" in out and "‘" in out

    def test_serialize_value_door_delegates_to_the_authority(self):
        """Door 1 (the shared table serializer) must produce the same bytes."""
        from database.sqlite import SQLiteTable, dumps_json

        # _serialize_value is an instance method but touches no instance state
        # for the list/dict branch, so construct without hitting a real DB.
        table = SQLiteTable.__new__(SQLiteTable)
        got = table._serialize_value(_blocks())
        assert got == dumps_json(_blocks()), "door 1 must not have its own policy"
        assert ESCAPE_MARKER not in got

    def test_session_pending_door_delegates_to_the_authority(self):
        """Door 2 (the busy-session raw INSERT path) must produce the same bytes.

        This door bypasses _serialize_value entirely (it builds its own JSON and
        inserts via a raw ``INSERT INTO messages``), so it is the door a
        happy-path test would never catch.
        """
        from core.session_pending import _payload_json
        from database.sqlite import dumps_json

        assert _payload_json(None, _blocks()) == dumps_json(_blocks())
        assert ESCAPE_MARKER not in _payload_json(CJK_TEXT, None)
        assert "对抗审查" in _payload_json(CJK_TEXT, None)

    def test_a_third_door_cannot_reintroduce_a_parallel_encoder(self):
        """AST-scan the writer module for a BARE ``json.dumps`` call.

        This must catch a door that does not exist YET: a future edit adding
        ``json.dumps(...)`` back into this module would re-fork the encoding policy
        and silently re-break Chinese search on the busy-session path. Calling the
        one function we already know about cannot detect that — an earlier version of
        this test did exactly that, so its NAME promised what its body could not
        deliver (REVIEW caught it).

        So this asserts a MODULE-WIDE property by parsing the source. AST, not a
        string grep, so a mention inside a docstring or comment cannot trip it.
        """
        import ast
        import inspect

        from core import session_pending

        tree = ast.parse(inspect.getsource(session_pending))

        # Cover every ALIAS the encoder is reachable through. Gate-2 proved the
        # narrow `json.dumps` check was bypassable three ways:
        #   from json import dumps  → dumps(x)
        #   import json as js       → js.dumps(x)
        json_aliases = {"json"}
        direct_dump_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "json":
                        json_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "json":
                for alias in node.names:
                    if alias.name == "dumps":
                        direct_dump_names.add(alias.asname or alias.name)

        def _is_bare(call: ast.Call) -> bool:
            # An explicit ensure_ascii=False is still correct; what must never
            # reappear is a BARE dumps that re-decides the policy.
            if any(kw.arg == "ensure_ascii" for kw in call.keywords):
                return False
            f = call.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr == "dumps"
                and isinstance(f.value, ast.Name)
                and f.value.id in json_aliases
            ):
                return True
            return isinstance(f, ast.Name) and f.id in direct_dump_names

        bare = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _is_bare(n)]
        assert not bare, (
            f"bare json dumps at line(s) {[n.lineno for n in bare]} — "
            "route it through database.sqlite.dumps_json"
        )
        assert not direct_dump_names, (
            f"`from json import dumps` re-forks the encoding policy: {direct_dump_names}"
        )
        # The module must genuinely CALL the authority — the old substring check
        # passed on a docstring mention alone (Gate-2).
        assert any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "dumps_json"
            for n in ast.walk(tree)
        ), "the module must CALL dumps_json, not merely mention it"


# ── AC3: a mixed corpus reads identically ─────────────────────────────────────


class TestMixedCorpusReadCompatibility:
    """AC3 — the writer fix is deploy-order independent."""

    def test_escaped_and_raw_forms_parse_to_the_same_object(self):
        escaped = json.dumps(_blocks())              # legacy form (ensure_ascii=True)
        raw = json.dumps(_blocks(), ensure_ascii=False)  # new form
        assert escaped != raw, "the two encodings must differ at the byte level"
        assert json.loads(escaped) == json.loads(raw) == _blocks()

    def test_row_reader_returns_equal_content_for_both_forms(self, tmp_path):
        """Drive the REAL reader (_row_to_dict), not a hand-rolled parse."""
        from database.sqlite import SQLiteTable

        db = tmp_path / "mixed.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE messages(id TEXT PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO messages VALUES('legacy', ?)", (json.dumps(_blocks()),))
        conn.execute(
            "INSERT INTO messages VALUES('new', ?)",
            (json.dumps(_blocks(), ensure_ascii=False),),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row

        table = SQLiteTable.__new__(SQLiteTable)
        rows = {
            r["id"]: table._row_to_dict(r)
            for r in conn.execute("SELECT * FROM messages ORDER BY id")
        }
        conn.close()

        assert rows["legacy"]["content"] == rows["new"]["content"] == _blocks()


# ── AC1: FTS searchability — the user-visible outcome ─────────────────────────


def _fts_fixture(conn: sqlite3.Connection) -> None:
    """Reproduce the production messages + external-content FTS + 3 triggers."""
    conn.executescript(
        """
        CREATE TABLE messages(id TEXT PRIMARY KEY, content TEXT);
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content, content=messages, content_rowid=rowid);
        CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
        CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content)
            VALUES('delete', old.rowid, old.content);
        END;
        CREATE TRIGGER messages_fts_update AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content)
            VALUES('delete', old.rowid, old.content);
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
        """
    )


def _match(conn: sqlite3.Connection, word: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?", (f'"{word}"',)
    ).fetchone()[0]


class TestFtsSearchability:
    """AC1 — escaped content is unfindable; raw content is findable."""

    def test_escaped_row_is_unfindable_and_raw_row_is_findable(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "fts.db"))
        _fts_fixture(conn)
        conn.execute("INSERT INTO messages VALUES('esc', ?)", (json.dumps(_blocks()),))
        conn.execute(
            "INSERT INTO messages VALUES('raw', ?)",
            (json.dumps(_blocks(), ensure_ascii=False),),
        )
        conn.commit()

        # 部署 appears in BOTH rows' logical content, but only the raw row indexes it.
        assert _match(conn, "部署") == 1, "only the raw row should be findable"
        # The escaped row's CJK is indexed as its escape token instead.
        assert _match(conn, "u90e8") >= 1, "escaped row indexes the escape token"
        conn.close()

    def test_update_to_raw_form_reindexes_via_trigger(self, tmp_path):
        """A backfill needs no explicit rebuild — the update trigger handles it."""
        conn = sqlite3.connect(str(tmp_path / "reindex.db"))
        _fts_fixture(conn)
        conn.execute("INSERT INTO messages VALUES('a', ?)", (json.dumps(_blocks()),))
        conn.commit()
        assert _match(conn, "部署") == 0

        conn.execute(
            "UPDATE messages SET content=? WHERE id='a'",
            (json.dumps(_blocks(), ensure_ascii=False),),
        )
        conn.commit()
        assert _match(conn, "部署") == 1, "the update trigger must reindex the row"
        conn.close()


# ── AC4/AC5/AC6/AC7: the gated backfill ───────────────────────────────────────


class TestBackfillSafety:
    """AC4/AC5 — idempotent, verification-gated, and NEVER writes by default."""

    def _seed(self, path):
        """A DB whose escaped rows span BOTH the CJK plane and outside it."""
        conn = sqlite3.connect(str(path))
        _fts_fixture(conn)
        conn.execute("CREATE TABLE todos(id TEXT PRIMARY KEY, linked_context TEXT)")
        rows = [
            ("cjk", json.dumps(_blocks("对抗审查"))),          # 对... CJK plane
            ("wide", json.dumps(_blocks("a　b"))),             # 　 — OUTSIDE \u4-\u9
            ("quote", json.dumps(_blocks("‘部署’"))),          # ‘ — OUTSIDE \u4-\u9
            ("ascii", json.dumps(_blocks("plain ascii"))),      # already clean — must not change
        ]
        conn.executemany("INSERT INTO messages VALUES(?,?)", rows)
        conn.execute(
            "INSERT INTO todos VALUES('t1', ?)", (json.dumps({"note": "配置"}),)
        )
        conn.commit()
        conn.close()

    def test_dry_run_is_the_default_and_writes_nothing(self, tmp_path):
        """AC5 — the destructive path must require an explicit flag."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "dry.db"
        self._seed(db)
        before = db.read_bytes()

        report = backfill(db)  # NO apply= argument — default must be non-destructive

        assert db.read_bytes() == before, "dry run must not modify a single byte"
        assert report["would_convert"] > 0, "must still REPORT what it would do"
        assert report["converted"] == 0

    def test_selection_is_semantic_not_a_cjk_plane_pattern(self, tmp_path):
        """AC6 — \\u3000 and \\u2018 rows must be caught, not just \\u4-\\u9."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "semantic.db"
        self._seed(db)
        backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        conn = sqlite3.connect(str(db))
        got = dict(conn.execute("SELECT id, content FROM messages"))
        conn.close()
        assert "　" in got["wide"], "ideographic space row must be converted"
        assert "‘" in got["quote"], "curly-quote row must be converted"
        assert "对抗审查" in got["cjk"]
        assert ESCAPE_MARKER not in got["wide"] + got["quote"] + got["cjk"]

    def test_is_idempotent(self, tmp_path):
        """AC4 — a second run converts nothing."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "idem.db"
        self._seed(db)
        first = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)
        second = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        assert first["converted"] > 0
        assert second["converted"] == 0, "re-running must be a no-op"
        assert second["would_convert"] == 0

    def test_preserves_semantics_of_every_converted_row(self, tmp_path):
        """AC4 — the parsed object must be EQUAL before and after (not just valid)."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "equal.db"
        self._seed(db)
        conn = sqlite3.connect(str(db))
        before = {i: json.loads(c) for i, c in conn.execute("SELECT id, content FROM messages")}
        conn.close()

        backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        conn = sqlite3.connect(str(db))
        after = {i: json.loads(c) for i, c in conn.execute("SELECT id, content FROM messages")}
        conn.close()
        assert before == after, "re-encoding must never change the parsed content"

    def test_leaves_already_clean_rows_untouched(self, tmp_path):
        """A pure-ASCII row must not be rewritten (no churn, no false conversion)."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "clean.db"
        self._seed(db)
        conn = sqlite3.connect(str(db))
        before = conn.execute("SELECT content FROM messages WHERE id='ascii'").fetchone()[0]
        conn.close()

        backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        conn = sqlite3.connect(str(db))
        after = conn.execute("SELECT content FROM messages WHERE id='ascii'").fetchone()[0]
        conn.close()
        assert after == before

    def test_covers_other_tables_not_just_messages(self, tmp_path):
        """AC6 — the shared-serializer defect affects more than one table."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "multi.db"
        self._seed(db)
        backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        conn = sqlite3.connect(str(db))
        lc = conn.execute("SELECT linked_context FROM todos WHERE id='t1'").fetchone()[0]
        conn.close()
        assert "配置" in lc and ESCAPE_MARKER not in lc

    def test_apply_takes_a_backup_before_writing(self, tmp_path):
        """AC5 / STEERING #20 — no write without a recoverable copy first."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "backup.db"
        self._seed(db)
        report = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        backup = report.get("backup_path")
        assert backup, "an --apply run must record the backup it took"
        from pathlib import Path
        assert Path(backup).exists(), f"backup must exist on disk: {backup}"
        # The backup must hold the PRE-fix bytes so recovery is real.
        conn = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        pre = conn.execute("SELECT content FROM messages WHERE id='cjk'").fetchone()[0]
        conn.close()
        assert ESCAPE_MARKER in pre, "backup must contain the ORIGINAL escaped bytes"

    def test_fts_becomes_searchable_after_backfill(self, tmp_path):
        """AC1 — the user-visible outcome, driven through the real FTS index."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "fts_after.db"
        self._seed(db)
        conn = sqlite3.connect(str(db))
        assert _match(conn, "对抗审查") == 0, "escaped history starts unsearchable"
        conn.close()

        backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        conn = sqlite3.connect(str(db))
        assert _match(conn, "对抗审查") == 1, "history must be searchable after backfill"
        conn.close()

    def test_counts_are_measured_from_the_db_not_frozen(self, tmp_path):
        """AC7 — the count must TRACK the database, not be a constant.

        A single-fixture assertion (``would_convert == 4``) is too weak: a script
        that literally ``return 4``-ed would pass it, which is why REVIEW flagged the
        earlier version of this test as not testing its own title. The discriminator
        is whether the number CHANGES with the corpus — so this drives TWO databases
        of different sizes and asserts the report follows.
        """
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        small = tmp_path / "small.db"
        self._seed(small)
        small_report = backfill(small)

        # A strictly larger corpus: the same seed plus 3 more escaped rows.
        large = tmp_path / "large.db"
        self._seed(large)
        conn = sqlite3.connect(str(large))
        conn.executemany(
            "INSERT INTO messages VALUES(?,?)",
            [(f"extra{i}", json.dumps(_blocks(f"额外{i}"))) for i in range(3)],
        )
        conn.commit()
        conn.close()
        large_report = backfill(large)

        assert small_report["would_convert"] > 0, "the small corpus must have hits"
        assert large_report["would_convert"] == small_report["would_convert"] + 3, (
            f"count must track the corpus: small={small_report['would_convert']} "
            f"large={large_report['would_convert']}"
        )
        # And the per-table breakdown must also be measured, not a fixed shape.
        assert (
            large_report["per_table"]["messages.content"]
            == small_report["per_table"]["messages.content"] + 3
        )

    def test_every_skip_is_attributed_not_a_silent_count(self, tmp_path):
        """A skipped row must say WHY — measured on the real corpus, the dominant
        skip class is prose that merely CONTAINS a literal backslash-u and would be
        CORRUPTED by a rewrite. A bare count cannot distinguish that from a row the
        selector wrongly missed."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill, classify

        db = tmp_path / "skips.db"
        self._seed(db)
        conn = sqlite3.connect(str(db))
        # Prose containing a literal \u — NOT JSON. Must be skipped, never rewritten.
        conn.execute(
            "INSERT INTO messages VALUES('prose', ?)",
            ("see the escape \\u4f60 in this markdown line",),
        )
        conn.commit()
        conn.close()

        report = backfill(db)

        assert report["skipped"] >= 1
        assert any(
            r.endswith(":not_json") for r in report["skipped_by_reason"]
        ), f"the prose row must be attributed as not_json: {report['skipped_by_reason']}"
        # And the classifier must name the reason, not just return None.
        assert classify("plain text \\u4f60")[1] == "not_json"
        assert classify('[{"t": "\\u5bf9"}]')[1] == "convert"
        assert classify('[{"t": "raw 对"}]')[1] == "no_escape"

    def test_prose_containing_a_literal_escape_is_never_rewritten(self, tmp_path):
        """The corruption case: 335 real rows are markdown quoting code."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "prose.db"
        self._seed(db)
        prose = "a doc line mentioning \\u4f60 verbatim"
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO messages VALUES('prose', ?)", (prose,))
        conn.commit()
        conn.close()

        backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        conn = sqlite3.connect(str(db))
        after = conn.execute("SELECT content FROM messages WHERE id='prose'").fetchone()[0]
        conn.close()
        assert after == prose, "prose must survive byte-identically"


class TestReviewHardening:
    """REVIEW findings — each was a real defect on the data-safety path."""

    def _seed(self, path):
        conn = sqlite3.connect(str(path))
        _fts_fixture(conn)
        conn.execute("INSERT INTO messages VALUES('a', ?)", (json.dumps(_blocks("对抗审查")),))
        conn.commit()
        conn.close()

    def test_backup_is_snapshot_consistent_and_integrity_checked(self, tmp_path):
        """HIGH — three shutil.copy2 calls over a live WAL DB yield a TORN backup.

        The production DB runs journal_mode=wal with a multi-MB -wal and 6 live
        daemon processes, so copying main/-wal/-shm separately produces a file that
        LOOKS like a backup and fails when needed. The fix uses the SQLite backup
        API (one consistent image) and PROVES it with PRAGMA integrity_check.
        """
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "wal.db"
        self._seed(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO messages VALUES('b', ?)", (json.dumps(_blocks("部署")),))
        conn.commit()
        conn.close()

        report = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)
        backup = Path(report["backup_path"])

        assert backup.exists()
        # The property that matters is SELF-SUFFICIENCY: the backup must be a
        # complete, valid database on its own — WAL frames already applied — so
        # recovery works from that single file. (Asserting "no -shm on disk" would
        # be wrong: SQLite CREATES -shm/-wal itself whenever it opens a WAL db, so
        # such an assertion tests the filesystem's reaction to reading, not what the
        # backup copied. Verified empirically before weakening this test.)
        conn = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        rows = dict(conn.execute("SELECT id, content FROM messages"))
        conn.close()
        # Both rows must be present — including the one committed via the WAL,
        # which a torn main-file-only copy could have missed.
        assert set(rows) == {"a", "b"}, f"WAL-committed row missing: {set(rows)}"
        assert ESCAPE_MARKER in rows["a"] and ESCAPE_MARKER in rows["b"]

    def test_backup_failure_aborts_before_any_write(self, tmp_path, monkeypatch):
        """HIGH — no write may happen without a verified backup (STEERING #20)."""
        from scripts import backfill_cjk_escapes as mod

        db = tmp_path / "nobackup.db"
        self._seed(db)
        before = db.read_bytes()

        def boom(_db):
            raise OSError("simulated disk full")

        monkeypatch.setattr(mod, "_backup", boom)
        with pytest.raises(OSError):
            mod.backfill(db, apply=True, confirm=mod.WRITE_CONFIRMATION)
        assert db.read_bytes() == before, "a failed backup must abort before writing"

    def test_cas_guard_refuses_to_overwrite_a_row_changed_since_survey(self, tmp_path):
        """HIGH — `messages` has no AUTOINCREMENT, so SQLite REUSES rowids.

        A bare `WHERE rowid=?` could overwrite an unrelated NEW message with stale
        surveyed content — silent destruction of irreplaceable data. The CAS guard
        (`AND col = <surveyed text>`) makes that impossible; the row is skipped and
        attributed instead.
        """
        from scripts import backfill_cjk_escapes as mod

        db = tmp_path / "cas.db"
        self._seed(db)

        real_backup = mod._backup

        def change_the_row_then_backup(path):
            """Simulate the daemon rewriting the row between survey and write."""
            conn = sqlite3.connect(str(path))
            conn.execute("UPDATE messages SET content=? WHERE id='a'", ("DAEMON WROTE THIS",))
            conn.commit()
            conn.close()
            return real_backup(path)

        mod._backup = change_the_row_then_backup
        try:
            report = mod.backfill(db, apply=True, confirm=mod.WRITE_CONFIRMATION)
        finally:
            mod._backup = real_backup

        conn = sqlite3.connect(str(db))
        after = conn.execute("SELECT content FROM messages WHERE id='a'").fetchone()[0]
        conn.close()
        assert after == "DAEMON WROTE THIS", "the concurrent write must NOT be reverted"
        assert report["converted"] == 0
        assert any(
            r.endswith(":changed_since_survey") for r in report["skipped_by_reason"]
        ), report["skipped_by_reason"]

    def test_non_text_values_do_not_crash_the_survey(self, tmp_path):
        """MEDIUM — a BLOB in a TEXT column matched the LIKE and raised TypeError,
        aborting the whole scan. Verified: SQLite's LIKE DOES match blob rows."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill, classify

        assert classify(b"blob \\u5bf9")[1] == "not_text"
        assert classify(None)[1] == "not_text"
        assert classify(42)[1] == "not_text"

        db = tmp_path / "blob.db"
        self._seed(db)
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO messages VALUES('blob', ?)", (b"blob \\u5bf9",))
        conn.commit()
        conn.close()

        report = backfill(db)  # must not raise
        assert any(r.endswith(":not_text") for r in report["skipped_by_reason"])

    def test_audit_all_surfaces_columns_targets_does_not_cover(self, tmp_path):
        """MEDIUM — TARGETS is hand-maintained; a table added later would be a
        silent miss. audit_unlisted_columns turns that into a visible finding."""
        from scripts.backfill_cjk_escapes import audit_unlisted_columns

        db = tmp_path / "audit.db"
        self._seed(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE future_table(id TEXT, payload TEXT)")
        conn.execute("INSERT INTO future_table VALUES('x', ?)", (json.dumps({"t": "配置"}),))
        conn.commit()
        conn.close()

        unlisted = audit_unlisted_columns(db)
        assert "future_table.payload" in unlisted, unlisted
        # A listed column must NOT be reported as unlisted.
        assert "messages.content" not in unlisted

    def test_converted_count_reflects_rows_actually_changed(self, tmp_path):
        """MEDIUM — the count previously incremented per statement ISSUED, so a
        vanished row still counted as converted, overstating the work done."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "count.db"
        self._seed(db)
        report = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)
        conn = sqlite3.connect(str(db))
        raw_rows = sum(
            1 for (c,) in conn.execute("SELECT content FROM messages")
            if ESCAPE_MARKER not in c
        )
        conn.close()
        assert report["converted"] == raw_rows


class TestSecurityHardening:
    """SECURITY REVIEW findings — controls that looked present but did not hold."""

    def _seed_multi(self, path):
        """messages (HAS triggers) + transcript_chunks (NO triggers) — the asymmetry."""
        conn = sqlite3.connect(str(path))
        _fts_fixture(conn)  # messages + messages_fts + 3 triggers
        conn.executescript(
            """
            CREATE TABLE transcript_chunks(id INTEGER PRIMARY KEY, content TEXT);
            CREATE VIRTUAL TABLE transcript_fts USING fts5(
                content, content=transcript_chunks, content_rowid=id);
            """
        )
        conn.execute("INSERT INTO messages VALUES('m', ?)", (json.dumps(_blocks("对抗审查")),))
        conn.execute("INSERT INTO transcript_chunks VALUES(1, ?)", (json.dumps(_blocks("部署")),))
        conn.execute("INSERT INTO transcript_fts(transcript_fts) VALUES('rebuild')")
        conn.commit()
        conn.close()

    def test_write_requires_an_explicit_token_not_just_a_boolean(self, tmp_path):
        """HIGH — `apply=True` alone is importable, so a job/hook/auto-repair could
        reach the UPDATE loop with no human. That is the shape that once wiped this
        project's data.db. The token makes an accidental write structurally impossible."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "token.db"
        self._seed_multi(db)
        before = db.read_bytes()

        with pytest.raises(PermissionError):
            backfill(db, apply=True)  # the old signature — must now REFUSE
        assert db.read_bytes() == before, "a refused write must touch nothing"

        with pytest.raises(PermissionError):
            backfill(db, apply=True, confirm="wrong-token")
        assert db.read_bytes() == before

        report = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)
        assert report["converted"] > 0, "the correct token must permit the write"

    def test_triggerless_fts_is_rebuilt_without_being_asked(self, tmp_path):
        """HIGH — only `messages` has sync triggers; transcript_chunks and
        knowledge_chunks have NONE (verified on the live DB). Converting a
        trigger-less base table without a rebuild leaves its index holding the OLD
        escape tokens — this run's own defect, inverted. I had measured triggers on
        `messages` only and generalized to all three; REVIEW caught it."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "triggerless.db"
        self._seed_multi(db)

        conn = sqlite3.connect(str(db))
        found = conn.execute(
            "SELECT count(*) FROM transcript_fts WHERE transcript_fts MATCH ?", ('"部署"',)
        ).fetchone()[0]
        conn.close()
        assert found == 0, "escaped transcript content starts unsearchable"

        # NOTE: rebuild_fts is NOT passed — the rebuild must happen anyway.
        report = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        assert "transcript_fts" in report["fts_rebuilt"], (
            f"a touched trigger-less index MUST be rebuilt: {report['fts_rebuilt']}"
        )
        conn = sqlite3.connect(str(db))
        after = conn.execute(
            "SELECT count(*) FROM transcript_fts WHERE transcript_fts MATCH ?", ('"部署"',)
        ).fetchone()[0]
        conn.close()
        assert after == 1, "the trigger-less index must be searchable after backfill"

    def test_backup_is_owner_only(self, tmp_path):
        """MEDIUM — the backup is a full second copy of all chat history; it must not
        sit in the home dir more readable than it needs to be."""
        import stat

        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "perm.db"
        self._seed_multi(db)
        report = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        mode = stat.S_IMODE(Path(report["backup_path"]).stat().st_mode)
        assert mode == 0o600, f"backup must be owner-only, got {oct(mode)}"


class TestGate2Hardening:
    """Gate-2 adversarial findings — each reproduced before being fixed."""

    def test_lone_surrogate_is_skipped_not_fatal(self):
        """CRITICAL — a single dirty row could make the run un-completable FOREVER.

        `["\\ud800\\u4e2d"]` is pure ASCII as stored (so SQLite accepted it) and
        json.loads yields the surrogate happily — but sqlite3's UTF-8 encode then
        raises UnicodeEncodeError, which escaped the chunk loop AND main() (which
        caught only FileNotFoundError/OSError). The run died with a traceback, the
        report and backup path were discarded, and every re-run hit the same row.
        """
        from scripts.backfill_cjk_escapes import classify

        text, reason = classify('["\\ud800\\u4e2d"]')
        assert text is None, "a surrogate must never reach the UPDATE"
        assert reason == "unencodable_surrogate", reason
        # A normal CJK row alongside it must still convert.
        assert classify('["\\u5bf9"]')[1] == "convert"

    def test_surrogate_row_does_not_abort_the_whole_run(self, tmp_path):
        """CRITICAL, end-to-end: one poisoned row must not block the other rows."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "surrogate.db"
        conn = sqlite3.connect(str(db))
        _fts_fixture(conn)
        conn.execute("INSERT INTO messages VALUES('bad', ?)", ('["\\ud800\\u4e2d"]',))
        conn.execute("INSERT INTO messages VALUES('good', ?)", (json.dumps(_blocks("部署")),))
        conn.commit()
        conn.close()

        report = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)  # must not raise

        assert report["converted"] == 1, "the healthy row must still convert"
        assert any(
            r.endswith(":unencodable_surrogate") for r in report["skipped_by_reason"]
        ), report["skipped_by_reason"]

    def test_rebuild_fts_works_as_a_standalone_repair(self, tmp_path):
        """CRITICAL — with nothing left to convert, --rebuild-fts returned early and
        reported fts_rebuilt=[], making it useless as the repair tool it documents.
        That is exactly the state a crashed run leaves behind."""
        from scripts.backfill_cjk_escapes import WRITE_CONFIRMATION, backfill

        db = tmp_path / "repair.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE transcript_chunks(id INTEGER PRIMARY KEY, content TEXT);
            CREATE VIRTUAL TABLE transcript_fts USING fts5(
                content, content=transcript_chunks, content_rowid=id);
            """
        )
        # ALREADY raw — nothing to convert, so `pending` will be empty.
        conn.execute(
            "INSERT INTO transcript_chunks VALUES(1, ?)",
            (json.dumps(_blocks("部署"), ensure_ascii=False),),
        )
        conn.commit()
        conn.close()

        report = backfill(db, apply=True, confirm=WRITE_CONFIRMATION, rebuild_fts=True)

        assert report["would_convert"] == 0
        assert "transcript_fts" in report["fts_rebuilt"], (
            f"repair must run with an empty pending set: {report['fts_rebuilt']}"
        )
        conn = sqlite3.connect(str(db))
        found = conn.execute(
            "SELECT count(*) FROM transcript_fts WHERE transcript_fts MATCH ?", ('"部署"',)
        ).fetchone()[0]
        conn.close()
        assert found == 1, "the repaired index must be searchable"

    def test_unrelated_trigger_does_not_suppress_the_mandatory_rebuild(self, tmp_path):
        """HIGH — the guard matched ANY trigger on the table, so an `updated_at`
        touch-trigger made it claim FTS sync existed and skip the rebuild: the stale
        index defect reintroduced through its own guard."""
        from scripts.backfill_cjk_escapes import _has_sync_triggers

        db = tmp_path / "trig.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE transcript_chunks(id INTEGER PRIMARY KEY, content TEXT, updated_at TEXT);
            CREATE VIRTUAL TABLE transcript_fts USING fts5(
                content, content=transcript_chunks, content_rowid=id);
            CREATE TRIGGER tc_touch AFTER UPDATE ON transcript_chunks BEGIN
                SELECT 1; END;
            """
        )
        conn.commit()
        assert _has_sync_triggers(conn, "transcript_chunks") is False, (
            "an unrelated trigger must NOT count as FTS sync"
        )
        # A real FTS-writing trigger DOES count.
        conn.executescript(
            """
            CREATE TRIGGER tc_fts AFTER UPDATE ON transcript_chunks BEGIN
                INSERT INTO transcript_fts(rowid, content) VALUES(new.id, new.content); END;
            """
        )
        conn.commit()
        assert _has_sync_triggers(conn, "transcript_chunks") is True
        conn.close()

    def test_nan_and_infinity_are_rejected(self):
        """MEDIUM — NaN passed the round-trip 'guarantee' only by list identity
        (by value nan != nan), so the invariant was vacuous, and the output is not
        valid JSON for any non-Python reader."""
        from scripts.backfill_cjk_escapes import classify

        for payload in ('[NaN, "\\u4e2d"]', '[Infinity, "\\u4e2d"]', '[-Infinity, "\\u4e2d"]'):
            text, reason = classify(payload)
            assert text is None, f"{payload} must not be converted"
            assert reason == "reencode_unparseable", (payload, reason)

    def test_crash_mid_run_returns_a_truthful_partial_report(self, tmp_path):
        """CRITICAL — earlier chunks are already COMMITTED, so a crash must not throw
        away the report: the caller needs the landed count, the backup path, and the
        fact that a trigger-less index is now stale against a half-converted table.

        Drives a REAL failure rather than mocking the DB layer: the file is made
        read-only after the survey, so the first UPDATE hits a genuine
        sqlite3.OperationalError from SQLite itself (sqlite3.Connection is an
        immutable type and cannot be monkeypatched anyway).
        """
        import os
        import stat

        from scripts import backfill_cjk_escapes as mod

        db = tmp_path / "crash.db"
        conn = sqlite3.connect(str(db))
        _fts_fixture(conn)
        conn.execute(
            """CREATE TABLE transcript_chunks(id INTEGER PRIMARY KEY, content TEXT)"""
        )
        conn.execute(
            """CREATE VIRTUAL TABLE transcript_fts USING fts5(
                   content, content=transcript_chunks, content_rowid=id)"""
        )
        for i in range(3):
            conn.execute(
                "INSERT INTO messages VALUES(?, ?)", (f"m{i}", json.dumps(_blocks("部署")))
            )
        conn.execute("INSERT INTO transcript_chunks VALUES(1, ?)",
                     (json.dumps(_blocks("部署")),))
        conn.commit()
        conn.close()

        real_backup = mod._backup

        def backup_then_make_readonly(path):
            """Backup normally, then revoke write permission → the UPDATE really fails."""
            out = real_backup(path)
            os.chmod(path, stat.S_IRUSR)
            return out

        mod._backup = backup_then_make_readonly
        try:
            report = mod.backfill(db, apply=True, confirm=mod.WRITE_CONFIRMATION)
        finally:
            mod._backup = real_backup
            os.chmod(db, stat.S_IRUSR | stat.S_IWUSR)

        assert report["crashed"], "the failure must be reported, not lost to a traceback"
        assert report["fts_stale"] is True
        assert report["backup_path"], "the backup path must survive a crash"
        assert Path(report["backup_path"]).exists(), "and the backup must be on disk"
        assert report["resume_hint"]


class TestMetaReviewHardening:
    """Meta-review gaps — what the pipeline missed entirely."""

    # Modules that pre-serialize a JSON column themselves (so _serialize_value never
    # sees the list) and therefore MUST route through the shared authority. Found the
    # hard way: chat_thread_manager was a THIRD door my P8 check missed because I only
    # AST-guarded session_pending.
    PRESERIALIZING_WRITERS = ("core.session_pending", "core.chat_thread_manager")

    def test_no_preserializing_writer_uses_a_bare_json_dumps(self):
        """Widen the single-module guard to EVERY module that pre-serializes.

        The original guard covered session_pending only, so chat_thread_manager could
        (and did) write key_decisions/open_questions with a bare json.dumps —
        re-escaping CJK on a column the shared serializer never touches.
        """
        import ast
        import importlib
        import inspect

        offenders = {}
        for mod_name in self.PRESERIALIZING_WRITERS:
            mod = importlib.import_module(mod_name)
            tree = ast.parse(inspect.getsource(mod))
            aliases = {"json"}
            direct = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name == "json":
                            aliases.add(a.asname or a.name)
                elif isinstance(node, ast.ImportFrom) and node.module == "json":
                    for a in node.names:
                        if a.name == "dumps":
                            direct.add(a.asname or a.name)

            bad = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if any(kw.arg == "ensure_ascii" for kw in node.keywords):
                    continue
                f = node.func
                if (
                    isinstance(f, ast.Attribute)
                    and f.attr == "dumps"
                    and isinstance(f.value, ast.Name)
                    and f.value.id in aliases
                ) or (isinstance(f, ast.Name) and f.id in direct):
                    bad.append(node.lineno)
            if bad:
                offenders[mod_name] = bad

        assert not offenders, (
            f"bare json.dumps in a pre-serializing DB writer: {offenders} — "
            "route through database.sqlite.dumps_json"
        )

    def test_thread_summary_columns_are_in_the_backfill_targets(self):
        """The third door's columns must also be CONVERTIBLE, not just fixed forward.

        Fixing the writer only helps new rows; the already-escaped ones need to be in
        TARGETS or they stay unsearchable forever.
        """
        from scripts.backfill_cjk_escapes import TARGETS

        assert ("thread_summaries", "key_decisions") in TARGETS
        assert ("thread_summaries", "open_questions") in TARGETS
