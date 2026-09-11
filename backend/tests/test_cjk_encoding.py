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


# ── CJK FTS bigram expansion (run_4ed75215) ───────────────────────────────────
#
# WHY these tests exist: SQLite FTS5 ships no tokenizer that can segment a
# 2-character Chinese word (all six built-ins probed: 0 hits), so the
# segmentation must be applied to the indexed TEXT and to the QUERY. The naive
# form of that fix regresses English, and the naive way of storing it corrupts
# the index — both are pinned below.


class TestExpandCjk:
    """AC2/AC10 — the expansion authority itself."""

    def test_pure_cjk_becomes_overlapping_bigrams(self):
        from core.cjk_index import expand_cjk
        assert expand_cjk("配置文件").split() == ["配置", "置文", "文件"]

    def test_a_mixed_token_preserves_its_embedded_english(self):
        """The trap that makes the existing _bm25_tokenize unusable verbatim.

        `用goal-pipeline跑` is ONE `\\w+` token. Bigram-shredding the whole token
        yields ['用g','go','oa','al','l-','-p',...] and destroys the English word,
        which measurably drops English recall. Split into maximal CJK / non-CJK
        runs FIRST, then expand only the CJK parts.
        """
        from core.cjk_index import expand_cjk
        out = expand_cjk("用goal-pipeline跑").split()
        assert "goal-pipeline" in out or {"goal", "pipeline"} <= set(out), out
        assert "oa" not in out, f"embedded English was shredded: {out}"

    def test_pure_ascii_is_untouched(self):
        from core.cjk_index import expand_cjk
        assert expand_cjk("plain english words").split() == [
            "plain", "english", "words"]

    def test_single_cjk_char_survives(self):
        from core.cjk_index import expand_cjk
        assert expand_cjk("看").split() == ["看"]

    def test_is_idempotent_and_space_joined(self):
        """AC10 — the migration may re-run; expanding twice must not drift."""
        from core.cjk_index import expand_cjk
        for s in ["配置文件", "用goal-pipeline跑", "plain words", "看", ""]:
            once = expand_cjk(s)
            assert expand_cjk(once) == once, s
            assert "  " not in once, s


class TestExpandCjkQuery:
    """AC1 (query half) — contract rule 1: expanding only the index is a no-op."""

    def test_a_multi_char_cjk_term_becomes_an_and_of_its_bigrams(self):
        """One word's bigrams must be AND-ed — they all belong to that word."""
        from core.cjk_index import expand_cjk_query
        q = expand_cjk_query("配置文件")
        assert q == '("配置" AND "置文" AND "文件")', q

    def test_separate_terms_are_or_ed(self):
        from core.cjk_index import expand_cjk_query
        q = expand_cjk_query("配置文件 语义搜索")
        assert " OR " in q
        assert q.startswith('("配置"')

    def test_english_terms_stay_quoted_single_tokens(self):
        from core.cjk_index import expand_cjk_query
        assert expand_cjk_query("recall engine") == '"recall" OR "engine"'

    def test_fts5_operators_in_user_text_cannot_become_operators(self):
        """Injection-safety: the existing builders quote every term; so must we.

        Asserted as the real property — every generated expression PARSES, so no
        smuggled operator survives — rather than as one escaping mechanism: the
        tokenizer drops FTS5 special chars before they can reach the escaper, so
        asserting on doubled quotes would test a path that never runs.
        """
        import sqlite3
        from core.cjk_index import expand_cjk, expand_cjk_query

        assert expand_cjk_query("OR NEAR") == '"OR" OR "NEAR"'

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE f USING fts5(seg)")
        conn.execute("INSERT INTO f VALUES(?)",
                     (expand_cjk("这是关于配置文件的说明 and english"),))
        hostile = ['"quoted"', 'a" OR f MATCH "b', "配置 AND", "*",
                   "^abc", "x)(y", "NEAR(a b)", '配置"文件']
        for raw in hostile:
            match = expand_cjk_query(raw)
            if not match:
                continue  # nothing tokenizable — caller short-circuits
            # Must not raise: a smuggled operator would be a syntax error here.
            conn.execute(
                "SELECT count(*) FROM f WHERE f MATCH ?", (match,)).fetchone()
        conn.close()

    def test_empty_input_yields_empty_string_not_a_broken_match(self):
        from core.cjk_index import expand_cjk_query
        assert expand_cjk_query("") == ""
        assert expand_cjk_query("   ") == ""

    def test_an_expanded_index_is_only_searchable_with_an_expanded_query(self):
        """Contract rule 1, proven against real FTS5 rather than asserted.

        This is the test that would catch an index-only 'fix': it deploys
        cleanly and returns nothing for any term longer than 2 chars.
        """
        import sqlite3
        from core.cjk_index import expand_cjk, expand_cjk_query

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE base(id INTEGER PRIMARY KEY, seg TEXT)")
        conn.execute(
            "CREATE VIRTUAL TABLE f USING fts5(seg, content=base, content_rowid=id)")
        conn.execute("INSERT INTO base VALUES(1, ?)",
                     (expand_cjk("这是关于配置文件的说明"),))
        conn.execute("INSERT INTO f(rowid, seg) SELECT id, seg FROM base")

        def hits(match: str) -> int:
            return conn.execute(
                "SELECT count(*) FROM f WHERE f MATCH ?", (match,)).fetchone()[0]

        assert hits('"配置文件"') == 0, "raw multi-char query must miss"
        assert hits(expand_cjk_query("配置文件")) == 1, "expanded query must hit"
        conn.close()


class TestSegColumnIntegrity:
    """AC3/AC5 — the design decision that Gate-1 forced, pinned as a test.

    The obvious implementation — write expanded text INTO an index whose
    external-content source is the raw column — corrupts the index. FTS5
    re-derives tokens from the content source on 'rebuild', on DELETE FROM, and
    inside the raw-`old.*` triggers, so each of those subtracts tokens that were
    never inserted. Indexing a persisted `*_seg` column instead makes all three
    paths re-derive the already-expanded text.

    These tests drive REAL sqlite + REAL FTS5 + REAL triggers. `integrity-check`
    with rank=1 is the assertion that matters: the plain form does not compare
    the index against the content source, so it passes on a corrupt index.
    """

    @staticmethod
    def _integrity(conn, fts: str) -> None:
        """Raise if the index disagrees with its content source."""
        conn.execute(
            f"INSERT INTO {fts}({fts}, rank) VALUES('integrity-check', 1)")

    def _seeded(self, *, index_raw_column: bool):
        """Build a messages-shaped table with the production trigger set.

        index_raw_column=True reproduces the REJECTED design (index points at
        the raw column while expanded text is written in) so the tests can prove
        it fails; False is the shipped design.
        """
        import sqlite3
        from core.cjk_index import expand_cjk

        conn = sqlite3.connect(":memory:")
        # Mirrors the production shape (schema v10): a plain content_seg column
        # that the Python writers fill. (A generated COALESCE fallback column was
        # rejected — SQLite cannot ALTER one onto an existing table, and the only
        # alternative was rebuilding the user's message history.)
        conn.execute(
            "CREATE TABLE messages(id TEXT PRIMARY KEY, content TEXT, "
            "content_seg TEXT, sent INTEGER DEFAULT 1)")
        indexed = "content" if index_raw_column else "content_seg"
        conn.execute(
            f"CREATE VIRTUAL TABLE messages_fts USING fts5({indexed}, "
            "content=messages, content_rowid=rowid)")
        # The production triggers, verbatim in shape: they pass new/old values
        # of the INDEXED column, which is the whole point.
        conn.execute(f"""
            CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, {indexed})
                VALUES (new.rowid, new.{indexed});
            END""")
        conn.execute(f"""
            CREATE TRIGGER messages_fts_update AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, {indexed})
                VALUES('delete', old.rowid, old.{indexed});
                INSERT INTO messages_fts(rowid, {indexed})
                VALUES (new.rowid, new.{indexed});
            END""")
        conn.execute(f"""
            CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, {indexed})
                VALUES('delete', old.rowid, old.{indexed});
            END""")
        conn.execute(
            "INSERT INTO messages(id, content, content_seg) VALUES('a', ?, ?)",
            ("部署重启完成", expand_cjk("部署重启完成")))
        return conn

    @staticmethod
    def _hits(conn, term: str) -> int:
        from core.cjk_index import expand_cjk_query
        return conn.execute(
            "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?",
            (expand_cjk_query(term),)).fetchone()[0]

    def test_a_two_char_cjk_term_is_findable(self):
        conn = self._seeded(index_raw_column=False)
        assert self._hits(conn, "部署") == 1
        conn.close()

    def test_a_hot_path_update_does_not_corrupt_the_index(self):
        """`UPDATE messages SET sent=?` fires the trigger on every queue op.

        Under the rejected design this silently accumulated stale postings and
        then failed integrity-check with "database disk image is malformed".
        """
        conn = self._seeded(index_raw_column=False)
        for i in range(5):
            conn.execute("UPDATE messages SET sent=? WHERE id='a'", (i % 2,))
        self._integrity(conn, "messages_fts")
        assert self._hits(conn, "部署") == 1
        conn.close()

    def test_a_content_update_leaves_no_stale_postings(self):
        conn = self._seeded(index_raw_column=False)
        from core.cjk_index import expand_cjk
        conn.execute("UPDATE messages SET content=?, content_seg=? WHERE id='a'",
                     ("第二版内容", expand_cjk("第二版内容")))
        self._integrity(conn, "messages_fts")
        assert self._hits(conn, "部署") == 0, "old term must stop matching"
        assert self._hits(conn, "第二") == 1, "new term must match"
        conn.close()

    def test_sql_rebuild_preserves_the_expansion(self):
        """The step the original plan relied on — and which erased the fix.

        A bare 'rebuild' re-derives from the content source. Pointing the index
        at content_seg is what makes this safe, so repair paths (and the
        migration's own final rebuild) need no special-casing.
        """
        conn = self._seeded(index_raw_column=False)
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        assert self._hits(conn, "部署") == 1
        self._integrity(conn, "messages_fts")
        conn.close()

    def test_delete_from_fts_stays_consistent(self):
        """`DELETE FROM <fts>` must remain legal AND consistent.

        It is ILLEGAL on a contentless table — that is why contentless was
        rejected — and `transcript_indexer.remove_session` uses exactly this
        form. Modelled on the table that actually runs it: transcript_chunks and
        knowledge_chunks carry ZERO triggers (verified against the live DB);
        only `messages` is trigger-backed. Using a trigger-backed fixture here
        would double-delete and test sqlite's bookkeeping, not this change.
        """
        import sqlite3
        from core.cjk_index import expand_cjk, expand_cjk_query

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE transcript_chunks(id INTEGER PRIMARY KEY, "
            "content TEXT, content_seg TEXT)")
        conn.execute(
            "CREATE VIRTUAL TABLE transcript_fts USING fts5(content_seg, "
            "content=transcript_chunks, content_rowid=id)")
        conn.execute("INSERT INTO transcript_chunks VALUES(1, ?, ?)",
                     ("部署重启完成", expand_cjk("部署重启完成")))
        conn.execute("INSERT INTO transcript_fts(rowid, content_seg) "
                     "SELECT id, content_seg FROM transcript_chunks")

        def hits(term):
            return conn.execute(
                "SELECT count(*) FROM transcript_fts WHERE transcript_fts MATCH ?",
                (expand_cjk_query(term),)).fetchone()[0]

        assert hits("部署") == 1
        # The production sequence in remove_session: FTS row, then base rows.
        conn.execute("DELETE FROM transcript_fts WHERE rowid = 1")
        conn.execute("DELETE FROM transcript_chunks WHERE id = 1")
        assert hits("部署") == 0
        self._integrity(conn, "transcript_fts")
        conn.close()

    def test_indexing_the_raw_column_is_provably_broken(self):
        """The negative control. Without this, the _seg column looks like
        gratuitous indirection — this is the evidence that it is load-bearing."""
        import sqlite3
        import pytest

        conn = self._seeded(index_raw_column=True)
        # Write EXPANDED text into an index whose source is the RAW column.
        from core.cjk_index import expand_cjk
        rowid = conn.execute("SELECT rowid FROM messages").fetchone()[0]
        conn.execute(
            "INSERT INTO messages_fts(messages_fts, rowid, content) "
            "VALUES('delete', ?, ?)", (rowid, "部署重启完成"))
        conn.execute("INSERT INTO messages_fts(rowid, content) VALUES(?, ?)",
                     (rowid, expand_cjk("部署重启完成")))
        assert self._hits(conn, "部署") == 1, "expansion appears to work..."
        with pytest.raises(sqlite3.DatabaseError):
            self._integrity(conn, "messages_fts")  # ...but the index is corrupt
        conn.close()

    def test_no_custom_sql_function_is_required_to_write(self):
        """AC6 — guards the rejected UDF / generated-column designs.

        Both would require the function registered on EVERY writing connection;
        one without it fails the whole INSERT. There are ~70 connect sites, so
        that is an availability risk on the message store.
        """
        conn = self._seeded(index_raw_column=False)
        from core.cjk_index import expand_cjk
        # No conn.create_function() anywhere in this test.
        conn.execute(
            "INSERT INTO messages(id, content, content_seg) VALUES('b', ?, ?)",
            ("新消息内容", expand_cjk("新消息内容")))
        assert self._hits(conn, "新消") == 1
        self._integrity(conn, "messages_fts")
        conn.close()


class TestKnowledgeStoreCjk:
    """AC1/AC4/AC5 — the `library` recall domain, driven through the real store."""

    def _store(self):
        import sqlite3
        from core.knowledge_store import KnowledgeStore
        conn = sqlite3.connect(":memory:")
        store = KnowledgeStore(conn)
        store.ensure_tables()
        return store, conn

    @staticmethod
    def _integrity(conn):
        conn.execute(
            "INSERT INTO knowledge_fts(knowledge_fts, rank) "
            "VALUES('integrity-check', 1)")

    def test_a_two_char_cjk_term_finds_the_chunk(self):
        store, conn = self._store()
        store.upsert_chunk("notes.md", 0, "标题", "这是关于配置文件的语义搜索说明", "h1")
        assert len(store.fts5_search("配置")) == 1
        assert len(store.fts5_search("语义")) == 1
        self._integrity(conn)
        conn.close()

    def test_a_multi_char_cjk_term_finds_the_chunk(self):
        """The case an index-only fix silently fails (contract rule 1)."""
        store, conn = self._store()
        store.upsert_chunk("notes.md", 0, "标题", "这是关于配置文件的说明", "h1")
        assert len(store.fts5_search("配置文件")) == 1
        conn.close()

    def test_english_search_still_works(self):
        store, conn = self._store()
        store.upsert_chunk("notes.md", 0, "Heading", "recall engine and bm25 scoring", "h1")
        assert len(store.fts5_search("recall")) == 1
        assert len(store.fts5_search("bm25 scoring")) == 1
        conn.close()

    def test_a_mixed_token_keeps_its_english_searchable(self):
        """AC2 at the store level, not just the helper level."""
        store, conn = self._store()
        store.upsert_chunk("notes.md", 0, None, "我们用goal-pipeline跑了一遍", "h1")
        assert len(store.fts5_search("goal-pipeline")) == 1, "embedded English lost"
        assert len(store.fts5_search("一遍")) == 1
        conn.close()

    def test_a_cjk_term_only_in_the_heading_is_findable(self):
        """AC4 — contract rule 3: expanding only the body loses heading matches."""
        store, conn = self._store()
        store.upsert_chunk("notes.md", 0, "部署重启说明", "ascii only body text", "h1")
        assert len(store.fts5_search("部署")) == 1, "heading column not expanded"
        conn.close()

    def test_reindexing_a_chunk_leaves_no_stale_match_and_no_corruption(self):
        """AC5 — the 'delete' protocol must receive what was actually indexed."""
        store, conn = self._store()
        store.upsert_chunk("notes.md", 0, None, "第一版的配置文件", "h1")
        assert len(store.fts5_search("配置")) == 1
        store.upsert_chunk("notes.md", 0, None, "第二版的部署说明", "h2")
        assert len(store.fts5_search("配置")) == 0, "stale posting survived the update"
        assert len(store.fts5_search("部署")) == 1
        self._integrity(conn)
        conn.close()

    def test_removing_a_file_leaves_the_index_consistent(self):
        store, conn = self._store()
        store.upsert_chunk("notes.md", 0, "标题", "配置文件说明", "h1")
        store.remove_file_entries("notes.md")
        assert len(store.fts5_search("配置")) == 0
        self._integrity(conn)
        conn.close()

    def test_removing_stale_chunks_leaves_the_index_consistent(self):
        store, conn = self._store()
        store.upsert_chunk("notes.md", 0, None, "第一段配置内容", "h1")
        store.upsert_chunk("notes.md", 1, None, "第二段部署内容", "h2")
        store.remove_stale_chunks("notes.md", keep_indexes={0})
        assert len(store.fts5_search("配置")) == 1
        assert len(store.fts5_search("部署")) == 0
        self._integrity(conn)
        conn.close()

    def test_repair_preserves_cjk_searchability(self):
        """AC3 at the store level — repair runs SQL 'rebuild', which re-derives
        from the content source. Under the rejected raw-source design this
        silently un-fixed CJK search on the first health-hook pass."""
        store, conn = self._store()
        store.upsert_chunk("notes.md", 0, "标题", "配置文件的说明", "h1")
        store.repair_fts_index()
        assert len(store.fts5_search("配置")) == 1, "repair erased the expansion"
        assert store._fts_is_healthy() is True
        self._integrity(conn)
        conn.close()

    def test_a_legacy_index_is_migrated_in_place(self):
        """A pre-existing DB has knowledge_fts over the RAW columns. A virtual
        table cannot be ALTERed, so ensure_tables must DROP + recreate + rebuild.
        Idempotent: running it again must not re-migrate."""
        import sqlite3
        from core.knowledge_store import KnowledgeStore
        conn = sqlite3.connect(":memory:")
        # Build the OLD shape by hand, with a row already in it.
        conn.execute("""CREATE TABLE knowledge_chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT NOT NULL,
            chunk_index INTEGER NOT NULL, heading TEXT, content TEXT NOT NULL,
            content_hash TEXT NOT NULL, metadata TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("""CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            content, heading, source_file,
            content=knowledge_chunks, content_rowid=id)""")
        conn.execute("INSERT INTO knowledge_chunks(source_file, chunk_index, "
                     "heading, content, content_hash) VALUES('a.md',0,'标题','配置文件',?)",
                     ("h1",))
        conn.commit()

        store = KnowledgeStore(conn)
        store.ensure_tables()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(knowledge_fts)")]
        assert cols[0] == "content_seg", cols
        store.ensure_tables()  # idempotent — must not raise or re-drop
        assert [r[1] for r in conn.execute("PRAGMA table_info(knowledge_fts)")] == cols
        conn.close()


class TestTranscriptStoreCjk:
    """AC1/AC3/AC5 — the `session` transcript leg, driven through the real store."""

    def _store(self):
        import sqlite3
        from core.transcript_indexer import TranscriptStore
        conn = sqlite3.connect(":memory:")
        store = TranscriptStore(conn)
        store.ensure_tables()
        return store, conn

    @staticmethod
    def _integrity(conn):
        conn.execute(
            "INSERT INTO transcript_fts(transcript_fts, rank) "
            "VALUES('integrity-check', 1)")

    def test_a_two_char_cjk_term_finds_the_chunk(self):
        store, conn = self._store()
        store.upsert_chunk("s1", "t.jsonl", 0, "user", "帮我看下部署有没有生效", "h1")
        assert len(store.fts5_search("部署")) == 1
        self._integrity(conn)
        conn.close()

    def test_english_search_still_works(self):
        store, conn = self._store()
        store.upsert_chunk("s1", "t.jsonl", 0, "user", "check the recall engine", "h1")
        assert len(store.fts5_search("recall")) == 1
        conn.close()

    def test_reindexing_a_chunk_leaves_no_stale_match(self):
        """AC5 — upsert_chunk's UPDATE branch reverses postings with the OLD
        _seg values; handing it raw text here is what corrupted the index in the
        rejected design (and caused a real malformed-index bug historically)."""
        store, conn = self._store()
        store.upsert_chunk("s1", "t.jsonl", 0, "user", "第一版的配置文件", "h1")
        assert len(store.fts5_search("配置")) == 1
        store.upsert_chunk("s1", "t.jsonl", 0, "user", "第二版的部署说明", "h2")
        assert len(store.fts5_search("配置")) == 0, "stale posting survived"
        assert len(store.fts5_search("部署")) == 1
        assert store._fts_is_healthy() is True
        self._integrity(conn)
        conn.close()

    def test_remove_session_leaves_the_index_consistent(self):
        """remove_session uses `DELETE FROM transcript_fts` — the statement that
        is ILLEGAL on a contentless table (why that design was rejected)."""
        store, conn = self._store()
        store.upsert_chunk("s1", "t.jsonl", 0, "user", "配置文件说明", "h1")
        store.remove_session("s1")
        assert len(store.fts5_search("配置")) == 0
        self._integrity(conn)
        conn.close()

    def test_repair_preserves_cjk_searchability(self):
        """AC3 — repair runs SQL 'rebuild', which re-derives from the content
        source. Safe only because the source is the segmented column."""
        store, conn = self._store()
        store.upsert_chunk("s1", "t.jsonl", 0, "user", "配置文件的说明", "h1")
        store.repair_fts_index()
        assert len(store.fts5_search("配置")) == 1, "repair erased the expansion"
        self._integrity(conn)
        conn.close()

    def test_a_legacy_index_is_migrated_in_place(self):
        import sqlite3
        from core.transcript_indexer import TranscriptStore
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE transcript_chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            source_file TEXT NOT NULL, chunk_index INTEGER NOT NULL, role TEXT,
            content TEXT NOT NULL, content_hash TEXT NOT NULL, metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("""CREATE VIRTUAL TABLE transcript_fts USING fts5(
            content, source_file,
            content=transcript_chunks, content_rowid=id)""")
        conn.commit()
        store = TranscriptStore(conn)
        store.ensure_tables()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(transcript_fts)")]
        assert cols[0] == "content_seg", cols
        store.ensure_tables()  # idempotent
        assert [r[1] for r in conn.execute("PRAGMA table_info(transcript_fts)")] == cols
        conn.close()


class TestMessagesFtsCjk:
    """AC1/AC3/AC6 — the `session` messages leg, through the REAL schema.

    Drives the actual migration (`_run_versioned_migrations`) and the real
    `SQLiteMessagesTable.put`, so the schema, the triggers and the writer are all
    the production ones. Nothing here is mocked.
    """

    async def _db(self, tmp_path):
        from database.sqlite import SQLiteDatabase
        db = SQLiteDatabase(str(tmp_path / "t.db"))
        await db.initialize()
        return db

    @staticmethod
    def _hits(conn, term: str) -> int:
        from core.cjk_index import expand_cjk_query
        cur = conn.execute(
            "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?",
            (expand_cjk_query(term),))
        return cur.fetchone()[0]

    def test_migration_repoints_the_index_and_rewrites_the_triggers(self, tmp_path):
        """Schema v10 — the index must declare content_seg, and all three
        triggers must carry it (a raw `old.content` trigger against a segmented
        index corrupts the postings on every UPDATE)."""
        import asyncio
        import sqlite3

        async def go():
            db = await self._db(tmp_path)
        asyncio.run(go())

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 10
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages_fts)")]
        assert cols == ["content_seg"], cols
        mcols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
        assert "content_seg" in mcols, mcols
        trigs = dict(conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='messages'"))
        assert len(trigs) == 3, sorted(trigs)
        for name, sql in trigs.items():
            assert "content_seg" in sql, f"{name} still carries raw content"
            assert "new.content," not in sql and "old.content," not in sql, name
        conn.close()

    def test_a_written_message_is_findable_by_a_two_char_cjk_term(self, tmp_path):
        """The end-to-end point of the whole run: real writer → real trigger →
        real index → a Chinese query that used to return 0."""
        import asyncio
        import sqlite3

        async def go():
            db = await self._db(tmp_path)
            await db.messages.put({
                "session_id": "s1", "role": "user",
                "content": [{"type": "text", "text": "帮我看下部署有没有生效"}],
            })
        asyncio.run(go())

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        assert self._hits(conn, "部署") == 1
        assert self._hits(conn, "生效") == 1
        conn.execute(
            "INSERT INTO messages_fts(messages_fts, rank) VALUES('integrity-check', 1)")
        conn.close()

    def test_a_metadata_only_update_does_not_corrupt_the_index(self, tmp_path):
        """`UPDATE messages SET sent=...` runs on the pending-queue hot path and
        fires messages_fts_update. Under the rejected design (raw `old.content`
        against a segmented index) this accumulated stale postings until
        integrity-check reported "database disk image is malformed"."""
        import asyncio
        import sqlite3

        async def go():
            db = await self._db(tmp_path)
            await db.messages.put({
                "id": "m1", "session_id": "s1", "role": "user",
                "content": [{"type": "text", "text": "部署重启完成"}],
            })
        asyncio.run(go())

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        for i in range(6):
            conn.execute("UPDATE messages SET sent=? WHERE id='m1'", (i % 2,))
        conn.execute(
            "INSERT INTO messages_fts(messages_fts, rank) VALUES('integrity-check', 1)")
        assert self._hits(conn, "部署") == 1
        conn.close()

    def test_english_content_is_still_findable(self, tmp_path):
        import asyncio
        import sqlite3

        async def go():
            db = await self._db(tmp_path)
            await db.messages.put({
                "session_id": "s1", "role": "user",
                "content": [{"type": "text", "text": "check the recall engine please"}],
            })
        asyncio.run(go())

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        assert self._hits(conn, "recall") == 1
        conn.close()

    def test_the_pending_writer_also_populates_the_index(self, tmp_path,
                                                        monkeypatch):
        """R27 — `persist_pending` writes messages with a RAW INSERT that bypasses
        SQLiteMessagesTable.put. If it forgets content_seg the row stores fine and
        is silently unsearchable: exactly the second-writer miss this guards."""
        import asyncio
        import sqlite3
        from core import session_pending

        db_file = tmp_path / "t.db"

        async def go():
            db = await self._db(tmp_path)
            await db.agents.put({"id": "a1", "name": "A"})
            await db.sessions.put({"id": "s1", "agent_id": "a1", "title": "t"})
            # persist_pending resolves the DB path itself; `_db_path_override`
            # is the module's own documented test seam for exactly this.
            monkeypatch.setattr(
                session_pending, "_db_path_override", str(db_file))
            await session_pending.persist_pending(
                "s1", user_message="等一下部署完成", content=None, agent_id="a1")

        asyncio.run(go())

        conn = sqlite3.connect(str(db_file))
        row = conn.execute(
            "SELECT content, content_seg FROM messages WHERE sent=0").fetchone()
        assert row is not None, "pending row not written"
        assert row[1], "content_seg left NULL by the pending writer"
        assert self._hits(conn, "部署") == 1
        conn.close()


class TestMigrationSegPhase:
    """AC7/AC8 — one ordered idempotent pass over history."""

    def _seeded_db(self, tmp_path):
        """A DB in the NEW shape holding rows whose _seg columns are unpopulated —
        i.e. what an existing installation looks like right after the schema
        migration and before the backfill runs."""
        import sqlite3
        db = tmp_path / "t.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE messages(id TEXT PRIMARY KEY, content TEXT, "
            "content_seg TEXT, sent INTEGER DEFAULT 1)")
        conn.execute(
            "CREATE VIRTUAL TABLE messages_fts USING fts5(content_seg, "
            "content=messages, content_rowid=rowid)")
        conn.executemany(
            "INSERT INTO messages(id, content) VALUES(?, ?)",
            [("m1", '[{"type": "text", "text": "帮我看下部署有没有生效"}]'),
             ("m2", '[{"type": "text", "text": "plain english message"}]'),
             # An escaped row — proves the ORDERING: if segmentation ran first,
             # this row's _seg would hold literal \uXXXX and no Chinese at all.
             ("m3", '[{"type": "text", "text": "\\u914d\\u7f6e\\u6587\\u4ef6"}]')])
        conn.commit()
        conn.close()
        return db

    def test_dry_run_reports_what_it_would_write_and_writes_nothing(self, tmp_path):
        import sqlite3
        from scripts.backfill_cjk_escapes import backfill
        db = self._seeded_db(tmp_path)
        before = db.stat().st_mtime_ns

        report = backfill(db)  # dry-run is the default

        assert report["applied"] is False
        assert report["seg_populated"]["messages"] == 3
        assert db.stat().st_mtime_ns == before, "dry-run touched the file"
        conn = sqlite3.connect(str(db))
        assert conn.execute(
            "SELECT count(*) FROM messages WHERE content_seg IS NOT NULL"
        ).fetchone()[0] == 0
        conn.close()

    def test_apply_then_rerun_is_idempotent(self, tmp_path):
        """AC7 — 'touch history once' must be VERIFIABLE: the second run
        reporting 0 is the proof, not the intention."""
        from scripts.backfill_cjk_escapes import backfill, WRITE_CONFIRMATION
        db = self._seeded_db(tmp_path)

        first = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)
        assert first["seg_populated"]["messages"] == 3

        second = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)
        assert second["seg_populated"]["messages"] == 0, "not idempotent"
        assert second["converted"] == 0

    def test_escape_reencoding_happens_before_segmentation(self, tmp_path):
        """AC8 — the ORDER is load-bearing. m3 holds literal \\uXXXX; if the
        segmentation phase ran first its _seg column would carry the escape text
        and hold no Chinese, so the row would stay unsearchable even though both
        phases 'succeeded'."""
        import re
        import sqlite3
        from scripts.backfill_cjk_escapes import backfill, WRITE_CONFIRMATION
        db = self._seeded_db(tmp_path)

        backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        conn = sqlite3.connect(str(db))
        seg = conn.execute(
            "SELECT content_seg FROM messages WHERE id='m3'").fetchone()[0]
        assert re.search(r"[一-鿿]", seg), f"segmented escapes, not text: {seg!r}"
        assert "u914d" not in seg, seg
        conn.close()

    def test_after_apply_a_cjk_query_finds_history(self, tmp_path):
        """The user-visible outcome: unsearchable history becomes findable."""
        import sqlite3
        from core.cjk_index import expand_cjk_query
        from scripts.backfill_cjk_escapes import backfill, WRITE_CONFIRMATION
        db = self._seeded_db(tmp_path)

        def hits(conn, term):
            return conn.execute(
                "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?",
                (expand_cjk_query(term),)).fetchone()[0]

        conn = sqlite3.connect(str(db))
        assert hits(conn, "部署") == 0, "precondition: history is unsearchable"
        conn.close()

        backfill(db, apply=True, confirm=WRITE_CONFIRMATION)

        conn = sqlite3.connect(str(db))
        assert hits(conn, "部署") == 1
        assert hits(conn, "配置") == 1, "the escaped row must become searchable"
        conn.execute(
            "INSERT INTO messages_fts(messages_fts, rank) VALUES('integrity-check', 1)")
        conn.close()

    def test_a_write_still_requires_the_confirmation_token(self, tmp_path):
        """The seg phase must not have opened a second, ungated write path."""
        import pytest
        from scripts.backfill_cjk_escapes import backfill
        db = self._seeded_db(tmp_path)
        with pytest.raises(PermissionError):
            backfill(db, apply=True)  # no token

    def test_a_database_without_the_seg_columns_is_skipped_not_crashed(self, tmp_path):
        """Someone may run this before restarting the daemon, so the columns may
        not exist yet. That must be a no-op, not a traceback."""
        import sqlite3
        from scripts.backfill_cjk_escapes import backfill
        db = tmp_path / "old.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE messages(id TEXT PRIMARY KEY, content TEXT)")
        conn.commit()
        conn.close()
        report = backfill(db)
        assert "messages" not in report["seg_populated"]
        assert "seg_failed" not in report


class TestBm25TokenizerDelegation:
    """AC2/AC9 — one CJK policy, and no ranking drift on the HEALTHY domains.

    `_bm25_tokenize` serves the `context_files` and `ddd` recall domains, which
    already handled CJK correctly. Fixing its mixed-token shredding is required
    (two disagreeing CJK policies is the drift this consolidates away), but
    `_bm25_scores` derives BOTH `avgdl` and `idf` from the candidate corpus, so
    changing the token count of ANY document re-normalizes EVERY document. Token
    equality alone would not catch that — hence the ranking-ORDER assertion.
    """

    def test_pure_cjk_tokenization_is_unchanged(self):
        from core.memory_index import _bm25_tokenize
        assert _bm25_tokenize("配置文件") == ["配置", "置文", "文件"]
        assert _bm25_tokenize("竞品分析") == ["竞品", "品分", "分析"]

    def test_pure_ascii_tokenization_is_unchanged(self):
        from core.memory_index import _bm25_tokenize
        assert _bm25_tokenize("recall engine bm25") == ["recall", "engine", "bm25"]

    def test_a_mixed_token_no_longer_shreds_its_english(self):
        """The defect: `用goal-pipeline跑` returned ['用g','go','oa','al',...]."""
        from core.memory_index import _bm25_tokenize
        out = _bm25_tokenize("用goal-pipeline跑")
        assert "oa" not in out and "al" not in out, out
        assert any("goal" in tok for tok in out), out

    def test_it_delegates_to_the_single_cjk_authority(self):
        """Asserted by BEHAVIOUR, not by grepping for an import: a source-scan
        assertion would pass on a docstring that merely mentions the module."""
        from core.cjk_index import expand_cjk
        from core.memory_index import _bm25_tokenize
        for text in ["配置文件", "用goal-pipeline跑", "混合mixed内容", "看"]:
            assert _bm25_tokenize(text) == expand_cjk(text.lower()).split(), text

    def test_ranking_is_corpus_relative_and_the_shift_is_bounded(self):
        """AC9 — the corpus-relative effect, asserted against a CAPTURED baseline.

        `_bm25_scores` derives BOTH `avgdl` and `idf` from the candidate corpus, so
        changing ANY document's token count re-normalizes EVERY score. Measured on
        the corpus below, the mixed document drops 23 -> 10 tokens (its embedded
        English is no longer shredded into bigrams), which moves `avgdl` and DOES
        reorder a pure-CJK query:

            pre : ['cjk_more', 'cjk_only', 'mixed']
            post: ['cjk_more', 'mixed', 'cjk_only']

        So "ranking is unchanged" is FALSE and asserting it would be a lie. What
        must hold is narrower and is what this pins:
          * the same DOCUMENT SET matches (no document gained or lost a match);
          * the top hit is stable (the doc mentioning the term twice still leads);
          * the reorder is confined to documents CONTAINING a mixed token.

        An earlier version of this test asserted a hardcoded order for a pure-CJK
        query and stayed GREEN when `_bm25_tokenize` was reverted to whole-token
        shredding — it was vacuous for its own stated purpose. That is why the
        baseline here is captured from the pre-change algorithm (reproduced inline)
        rather than written from expectation.
        """
        import math

        from core.memory_index import _CJK_RE, _bm25_scores, _tokenize_lower

        docs = {
            "cjk_only": "配置文件的语义搜索说明,包含配置细节",
            "cjk_more": "配置文件 配置文件 配置项与文件说明",
            "ascii_only": "recall engine bm25 scoring and retrieval",
            "mixed": "我们用goal-pipeline跑了一遍配置文件",
            "unrelated": "天气很好今天出门散步",
        }

        def pre_change_tokenize(text):
            """The exact tokenizer this change replaced (whole-token shredding)."""
            out = []
            for tok in _tokenize_lower(text):
                if _CJK_RE.search(tok) and len(tok) >= 2:
                    out.extend(tok[i:i + 2] for i in range(len(tok) - 1))
                else:
                    out.append(tok)
            return out

        def pre_change_scores(query, corpus, k1=1.5, b=0.75):
            """Okapi-BM25 over the pre-change tokens — same formula as the module."""
            toks = {k: pre_change_tokenize(v) for k, v in corpus.items()}
            n = len(toks)
            dl = {k: len(v) for k, v in toks.items()}
            avgdl = sum(dl.values()) / n
            sets = {k: set(v) for k, v in toks.items()}
            out = {}
            for key, tk in toks.items():
                s = 0.0
                for term in set(pre_change_tokenize(query)):
                    df = sum(1 for x in sets.values() if term in x)
                    if not df:
                        continue
                    f = tk.count(term)
                    if not f:
                        continue
                    idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
                    s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl[key] / avgdl))
                if s > 0:
                    out[key] = s
            return out

        query = "配置文件"
        before = pre_change_scores(query, docs)
        after = _bm25_scores(query, docs)

        # 1. Same matching set — nothing gained or lost a match.
        assert set(before) == set(after), (sorted(before), sorted(after))
        # 2. Documents genuinely containing the term, and only those.
        assert set(after) == {"cjk_more", "cjk_only", "mixed"}, sorted(after)
        # 3. Top hit stable: the doc naming the term twice still leads.
        rank = lambda s: [k for k, _ in sorted(s.items(), key=lambda kv: -kv[1])]
        assert rank(before)[0] == "cjk_more" and rank(after)[0] == "cjk_more"
        # 4. Any reordering is confined to docs holding a mixed CJK+ASCII token.
        moved = {k for k in before if rank(before).index(k) != rank(after).index(k)}
        assert moved <= {"mixed", "cjk_only"}, (
            f"a document with no mixed token changed rank: {moved}")
        # 5. Documents with NO mixed token keep their token count exactly.
        from core.memory_index import _bm25_tokenize
        for key in ("cjk_only", "cjk_more", "ascii_only", "unrelated"):
            assert len(_bm25_tokenize(docs[key])) == len(
                pre_change_tokenize(docs[key])), key
        # 6. ABSOLUTE, not comparative: the mixed document's embedded English must
        #    survive as a real token. Checks 1-5 compare against a reproduced
        #    baseline, so they all hold vacuously if production regresses back TO
        #    that baseline (verified: reverting _bm25_tokenize left them green —
        #    the token COUNT happens to coincide). Only an absolute assertion has
        #    teeth against a revert.
        mixed_tokens = _bm25_tokenize(docs["mixed"])
        assert any("goal" in tok for tok in mixed_tokens), mixed_tokens
        assert "oa" not in mixed_tokens, mixed_tokens

    def test_an_english_query_still_finds_the_mixed_document(self):
        """The payoff of the split: before, `goal-pipeline` inside a CJK run was
        destroyed, so this query scored the mixed doc at zero."""
        from core.memory_index import _bm25_scores
        docs = {
            "mixed": "我们用goal-pipeline跑了一遍",
            "unrelated": "天气很好今天出门散步",
        }
        scores = _bm25_scores("goal-pipeline", docs)
        assert scores.get("mixed", 0) > 0, scores


class TestEveryMessagesWriterPopulatesSeg:
    """The guard that stands in for the rejected COALESCE fallback column.

    `messages_fts` indexes `content_seg`, which the Python writers must fill; a
    writer that forgets it stores the row perfectly and leaves it invisible to
    search. The structural fix — a GENERATED `COALESCE(content_seg, content)`
    column, so a forgetful writer degrades to raw indexing instead of vanishing —
    is IMPOSSIBLE here: SQLite's `ALTER TABLE ADD ... GENERATED` silently succeeds
    while adding no column, so the only route would be rebuilding the user's
    message history, the shape STEERING #20 forbids.

    So the invariant is enforced by ENUMERATION: this fails when a new
    `INSERT INTO messages` appears in production code without the column.
    """

    def test_no_production_writer_inserts_messages_without_content_seg(self):
        """Scans the AST of every backend module rather than a hand-listed set of
        files, because a hand-listed set is exactly what goes stale when the next
        writer is added somewhere new."""
        import ast
        import pathlib as _pl

        backend = _pl.Path(__file__).resolve().parent.parent
        offenders: list[str] = []
        for path in backend.rglob("*.py"):
            rel = path.relative_to(backend).as_posix()
            if rel.startswith(("tests/", ".venv/")) or "/.venv/" in rel:
                continue
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                lowered = " ".join(node.value.lower().split())
                if "insert into messages" not in lowered:
                    continue
                if "messages_fts" in lowered:
                    continue  # index maintenance, not a base-table write
                # The column may live in a sibling string of a split literal, so
                # fall back to the enclosing file rather than flagging every
                # concatenated fragment.
                if "content_seg" in node.value or "content_seg" in source:
                    continue
                offenders.append(f"{rel}:{node.lineno}")

        assert not offenders, (
            "these production writers INSERT INTO messages without populating "
            f"content_seg, so their rows will be unsearchable: {offenders}"
        )

    def test_both_known_writers_actually_populate_it(self):
        """Behavioural companion: the scan proves the column is MENTIONED, this
        proves it is FILLED. A writer could name it in a comment and still not
        write it."""
        import inspect
        from core import session_pending
        from database.sqlite import SQLiteMessagesTable

        put_src = inspect.getsource(SQLiteMessagesTable.put)
        assert 'item["content_seg"] = expand_cjk(' in put_src, put_src

        pending_src = inspect.getsource(session_pending.persist_pending)
        assert "content_seg" in pending_src
        assert "expand_cjk(payload)" in pending_src, pending_src


class TestFreshIndexReconciliation:
    """The CRITICAL that only a real-data smoke found (run_4ed75215).

    Creating an external-content FTS index leaves it EMPTY while its source
    column already has a value for every row. Those two states DISAGREE, and the
    disagreement is not cosmetic:
      * `integrity-check rank=1` reports "database disk image is malformed";
      * on a trigger-backed table the first UPDATE fails outright, so the
        segmentation backfill dies and search returns 0 for EVERY query — Chinese
        AND English.

    Measured on a copy of the real 636 MB database: without the reconciling
    rebuild, English recall went 3347 -> 0 for `recall`, 19957 -> 0 for `session`.
    Every unit test passed, because they all built their index and their rows in
    the same breath. Only the real-data smoke had rows-before-index.
    """

    def test_messages_migration_leaves_a_consistent_index(self, tmp_path):
        """Rows exist BEFORE the migration runs — the real upgrade shape."""
        import asyncio
        import sqlite3
        from database.sqlite import SQLiteDatabase

        db_file = tmp_path / "t.db"
        # A pre-v10 database with real history in it.
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            "CREATE TABLE messages(id TEXT PRIMARY KEY, session_id TEXT, "
            "role TEXT, content TEXT, model TEXT, metadata TEXT, "
            "created_at TEXT, updated_at TEXT, sent INTEGER DEFAULT 1, "
            "pending_seq INTEGER, claimed_at TEXT, expires_at INTEGER)")
        conn.execute(
            "CREATE VIRTUAL TABLE messages_fts USING fts5("
            "content, content=messages, content_rowid=rowid)")
        conn.executemany(
            "INSERT INTO messages(id, content) VALUES(?, ?)",
            [(f"m{i}", "部署重启完成 and english words") for i in range(20)])
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.execute("PRAGMA user_version = 9")
        conn.commit()
        conn.close()

        asyncio.run(SQLiteDatabase(str(db_file)).initialize())

        conn = sqlite3.connect(str(db_file))
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 10
        # The assertion that failed before the fix:
        conn.execute(
            "INSERT INTO messages_fts(messages_fts, rank) "
            "VALUES('integrity-check', 1)")
        # And an UPDATE (the shape the backfill performs) must not blow up.
        conn.execute("UPDATE messages SET content_seg = ? WHERE id = 'm0'",
                     ("部署 署重 重启 启完 完成",))
        conn.execute(
            "INSERT INTO messages_fts(messages_fts, rank) "
            "VALUES('integrity-check', 1)")
        conn.close()

    def test_a_fresh_knowledge_index_over_existing_rows_is_reconciled(self, tmp_path):
        import sqlite3
        from core.knowledge_store import KnowledgeStore

        conn = sqlite3.connect(str(tmp_path / "k.db"))
        # Base table with rows but NO index yet (e.g. FTS5 was unavailable once).
        conn.execute("""CREATE TABLE knowledge_chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT NOT NULL,
            chunk_index INTEGER NOT NULL, heading TEXT, content TEXT NOT NULL,
            content_hash TEXT NOT NULL, metadata TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.executemany(
            "INSERT INTO knowledge_chunks(source_file, chunk_index, heading, "
            "content, content_hash) VALUES(?,?,?,?,?)",
            [(f"n{i}.md", 0, "标题", "配置文件的说明", f"h{i}") for i in range(5)])
        conn.commit()

        KnowledgeStore(conn).ensure_tables()

        conn.execute(
            "INSERT INTO knowledge_fts(knowledge_fts, rank) "
            "VALUES('integrity-check', 1)")
        conn.close()

    def test_a_fresh_transcript_index_over_existing_rows_is_reconciled(self, tmp_path):
        import sqlite3
        from core.transcript_indexer import TranscriptStore

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        conn.execute("""CREATE TABLE transcript_chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            source_file TEXT NOT NULL, chunk_index INTEGER NOT NULL, role TEXT,
            content TEXT NOT NULL, content_hash TEXT NOT NULL, metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.executemany(
            "INSERT INTO transcript_chunks(session_id, source_file, chunk_index, "
            "role, content, content_hash) VALUES(?,?,?,?,?,?)",
            [("s1", "a.jsonl", i, "user", "部署说明", f"h{i}") for i in range(5)])
        conn.commit()

        TranscriptStore(conn).ensure_tables()

        conn.execute(
            "INSERT INTO transcript_fts(transcript_fts, rank) "
            "VALUES('integrity-check', 1)")
        conn.close()

    def test_reconciliation_does_not_rebuild_a_healthy_index(self, tmp_path):
        """Guard against re-tokenizing the whole corpus on every startup."""
        import sqlite3
        from core.knowledge_store import KnowledgeStore

        conn = sqlite3.connect(str(tmp_path / "k.db"))
        store = KnowledgeStore(conn)
        store.ensure_tables()
        store.upsert_chunk("n.md", 0, "标题", "配置文件的说明", "h1")

        # `sqlite3.Connection.execute` is read-only on the C type, so spy with a
        # thin wrapper object rather than monkeypatching the attribute.
        calls: list[str] = []

        class _Spy:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *a, **kw):
                if "VALUES('rebuild')" in sql:
                    calls.append(sql)
                return self._inner.execute(sql, *a, **kw)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        store._conn = _Spy(conn)  # type: ignore[assignment]
        store.ensure_tables()     # a normal second startup
        store._conn = conn        # type: ignore[assignment]
        assert calls == [], f"rebuilt a healthy index: {calls}"
        conn.close()


class TestRecallAgainstGroundTruth:
    """AC1 + AC2 first half — the headline numbers, asserted not asserted-about.

    The spec reviewer was right that these were unproven: every other test in this
    file checks single-row FINDABILITY, which cannot distinguish "some rows match"
    from "the right proportion of rows match". Recall is a RATIO, so it needs a
    denominator — `content LIKE '%term%'` is the ground truth, and the terms are
    HARVESTED from the corpus rather than hand-picked (hand-picked terms measure
    the author's taste, not the corpus).
    """

    CORPUS = [
        ("部署完成后需要重启守护进程才能生效", "部署说明"),
        ("配置文件里的语义搜索参数需要调整", "配置说明"),
        ("对抗审查发现了两个严重缺陷", "审查记录"),
        ("这次重启没有生效,配置文件没被重新读取", "问题记录"),
        ("recall engine uses bm25 scoring over the corpus", "Recall Notes"),
        ("the migration is idempotent and takes a backup", "Migration Notes"),
        ("我们用goal-pipeline跑了一遍部署流程", "混合记录"),
        ("语义搜索的召回率从百分之十一提升到百分之百", "召回率"),
    ]
    ENGLISH_PROBE = ["recall", "engine", "bm25", "scoring", "corpus",
                     "migration", "idempotent", "backup", "goal-pipeline",
                     "pipeline"]

    def _store(self):
        import sqlite3
        from core.knowledge_store import KnowledgeStore
        conn = sqlite3.connect(":memory:")
        store = KnowledgeStore(conn)
        store.ensure_tables()
        for i, (body, heading) in enumerate(self.CORPUS):
            store.upsert_chunk("notes.md", i, heading, body, f"h{i}")
        return store, conn

    @staticmethod
    def _harvest_two_char_terms(conn) -> list[str]:
        """Take the terms FROM the corpus, so the measurement cannot be curated."""
        import re
        from collections import Counter
        counts: Counter = Counter()
        for (body,) in conn.execute("SELECT content FROM knowledge_chunks"):
            for run in re.findall(r"[一-鿿]{2}", body or ""):
                counts[run] += 1
        # Terms appearing in 2+ documents make recall meaningful (a term in one
        # document cannot distinguish 100% recall from a lucky single hit).
        return [t for t, n in counts.most_common() if n >= 2][:12]

    def test_cjk_recall_reaches_parity_with_ground_truth(self):
        """AC1 — recall as a RATIO against LIKE, not a findability spot-check."""
        store, conn = self._store()
        terms = self._harvest_two_char_terms(conn)
        assert len(terms) >= 5, f"corpus too thin to measure recall: {terms}"

        matched = truth = 0
        zero_hit = []
        for term in terms:
            found = {r["id"] for r in store.fts5_search(term, limit=100)}
            actual = {r[0] for r in conn.execute(
                "SELECT id FROM knowledge_chunks WHERE content LIKE ?",
                (f"%{term}%",))}
            matched += len(found & actual)
            truth += len(actual)
            if not found:
                zero_hit.append(term)

        recall = matched / truth
        assert not zero_hit, f"terms returning nothing: {zero_hit}"
        assert recall >= 0.95, f"recall {recall:.1%} ({matched}/{truth}) on {terms}"
        conn.close()

    def test_english_probe_set_does_not_regress(self):
        """AC2 first half — every English term must still be found.

        Absolute rather than comparative: the pre-change index cannot be built in
        the same process (the schema differs), and a comparative assertion would
        hold vacuously if both sides regressed together — the exact trap that made
        the first version of the AC9 test toothless.
        """
        store, conn = self._store()
        missing = []
        for term in self.ENGLISH_PROBE:
            actual = conn.execute(
                "SELECT count(*) FROM knowledge_chunks WHERE lower(content) LIKE ?",
                (f"%{term.lower()}%",)).fetchone()[0]
            if not actual:
                continue  # not in this corpus — nothing to regress
            if not store.fts5_search(term, limit=100):
                missing.append(term)
        assert not missing, f"English terms lost their match: {missing}"
        conn.close()

    def test_a_cjk_term_only_in_the_source_file_is_findable(self):
        """AC4's third column — heading was covered, source_file was not."""
        store, conn = self._store()
        store.upsert_chunk("部署笔记.md", 99, "Heading", "ascii only body", "hx")
        hits = store.fts5_search("部署", limit=50)
        assert any(h["source_file"] == "部署笔记.md" for h in hits), \
            "source_file column not expanded"
        conn.close()


class TestRealWriterNeedsNoCustomFunction:
    """AC6 — driven through the REAL production writer, not a hand-written INSERT.

    The earlier version of this test inserted its own row with an explicit
    content_seg value, which proves only that a plain INSERT works — it never
    exercised the writer that must compute the column. If a UDF or generated
    column ever creeps back in, only the real writer path would fail.
    """

    def test_the_real_messages_writer_needs_no_registered_function(self, tmp_path):
        import asyncio
        import sqlite3
        from database.sqlite import SQLiteDatabase

        db_file = tmp_path / "t.db"

        async def go():
            db = SQLiteDatabase(str(db_file))
            await db.initialize()          # runs the real migration
            await db.messages.put({        # the real writer
                "session_id": "s1", "role": "user",
                "content": [{"type": "text", "text": "部署重启完成"}]})

        asyncio.run(go())

        # A brand-new connection with NO create_function call anywhere. If a UDF
        # or generated column ever creeps back in, this is where it breaks.
        conn = sqlite3.connect(str(db_file))
        row = conn.execute(
            "SELECT content_seg FROM messages WHERE content_seg IS NOT NULL"
        ).fetchone()
        assert row and row[0], "the real writer left content_seg empty"

        # A further write on the same unregistered connection must also succeed.
        now = "2026-01-01T00:00:00"
        conn.execute(
            "INSERT INTO messages(id, session_id, role, content, content_seg, "
            "created_at, updated_at) VALUES('x', 's1', 'user', ?, ?, ?, ?)",
            ("纯文本", "纯文 文本", now, now))
        conn.commit()
        conn.execute(
            "INSERT INTO messages_fts(messages_fts, rank) "
            "VALUES('integrity-check', 1)")
        conn.close()

    def test_the_real_knowledge_writer_needs_no_registered_function(self):
        import sqlite3
        from core.knowledge_store import KnowledgeStore

        conn = sqlite3.connect(":memory:")   # no create_function anywhere
        store = KnowledgeStore(conn)
        store.ensure_tables()
        store.upsert_chunk("n.md", 0, "标题", "配置文件说明", "h1")
        row = conn.execute(
            "SELECT content_seg, heading_seg FROM knowledge_chunks").fetchone()
        assert row[0] and row[1], f"writer left seg columns empty: {row}"
        assert len(store.fts5_search("配置", limit=10)) == 1
        conn.close()


class TestReviewHardening:
    """Every REVIEW finding, each reproduced before being fixed."""

    def test_kana_hangul_and_extension_ideographs_are_segmented(self):
        """HIGH — the character class covered only U+4E00-9FFF, so a 2-character
        KOREAN or JAPANESE word was exactly as unfindable as the Chinese one this
        change exists to fix. Japanese and Korean are also written without spaces,
        so the same reasoning applies verbatim."""
        from core.cjk_index import expand_cjk
        assert expand_cjk("설정파일").split() == ["설정", "정파", "파일"], "hangul"
        assert expand_cjk("ファイル").split() == ["ファ", "ァイ", "イル"], "katakana"
        assert expand_cjk("ｱｲｳ").split() == ["ｱｲ", "ｲｳ"], "half-width katakana"
        # And the two regexes must agree, or a token takes the CJK branch and then
        # fails to split — silently reproducing the mixed-token bug.
        out = expand_cjk("설정file파일")
        assert "file" in out.split(), out

    def test_the_seg_write_compare_and_swaps_on_the_source(self):
        """HIGH — `messages` has no AUTOINCREMENT, so SQLite REUSES a deleted row's
        rowid, and the survey runs minutes before the write on a live database.
        Without a compare-and-swap the surveyed row's segmented text lands on
        whatever NEW row took that rowid: the new row becomes unfindable and
        searching the old text surfaces the wrong message. The idempotence
        predicate would then re-apply it on every run, so it never self-corrects.

        Asserts the INVARIANT (the write is conditional on the source still
        matching) rather than staging the race. The race is not injectable through
        the public function — it happens strictly between the survey `fetchall`
        and the write, inside one call, and a trigger fires only AFTER the UPDATE
        has already been evaluated (attempted: the CAS legitimately matched, so the
        test passed with the guard REMOVED — it was decoration).

        Two independent assertions, so neither alone can pass vacuously:
          1. the emitted SQL constrains the source column, not only the rowid;
          2. a row whose source no longer matches the surveyed value is NOT
             written, and is reported as stale.
        """
        import sqlite3
        from scripts.backfill_cjk_escapes import (
            WRITE_CONFIRMATION, _populate_seg_columns)

        # (1) The statement itself must carry the guard.
        seen: list[str] = []

        class _Spy:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *a, **kw):
                if sql.strip().upper().startswith("UPDATE MESSAGES SET"):
                    seen.append(" ".join(sql.split()))
                return self._inner.execute(sql, *a, **kw)

            def __enter__(self):
                return self._inner.__enter__()

            def __exit__(self, *a):
                return self._inner.__exit__(*a)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE messages(id TEXT PRIMARY KEY, content TEXT, "
                     "content_seg TEXT)")
        conn.execute("INSERT INTO messages VALUES('a', '原始内容', NULL)")
        _populate_seg_columns(_Spy(conn), apply=True, confirm=WRITE_CONFIRMATION)  # type: ignore[arg-type]
        assert seen, "no UPDATE was issued"
        for sql in seen:
            # The identifier is quoted (`"content"`), which is correct — assert on
            # the guard's SHAPE, not on one spelling of it.
            assert "WHERE rowid = ? AND" in sql, sql
            assert 'content" IS ?' in sql or "content IS ?" in sql, sql

        # (2) A row whose source changed since the survey must be SKIPPED, proven
        #     by driving the same statement shape the phase emits.
        conn.execute("UPDATE messages SET content = '换掉了' WHERE rowid = 1")
        cur = conn.execute(
            'UPDATE messages SET content_seg = ? WHERE rowid = ? AND "content" IS ?',
            ("原始 始内 内容", 1, "原始内容"))
        assert cur.rowcount == 0, "stale write was NOT rejected by the CAS"
        conn.close()

    def test_a_long_cjk_query_cannot_explode_the_match_expression(self):
        """HIGH — a CJK paste has no spaces, so it arrives as ONE term and each
        character adds an AND-clause. Measured before the cap: a 20K-character
        paste produced a ~180 KB expression taking ~2.8s, and session search runs
        per debounced keystroke."""
        from core.cjk_index import expand_cjk_query
        huge = "配置文件语义搜索" * 2500          # 20,000 chars, no spaces
        expr = expand_cjk_query(huge)
        assert len(expr) < 1000, f"expression is {len(expr)} bytes"
        # A normal query is untouched.
        assert expand_cjk_query("配置文件") == '("配置" AND "置文" AND "文件")'

    def test_index_columns_are_declared_in_the_canonical_schema(self):
        """MEDIUM — the writer writes content_seg on every insert, so the base
        DDL must declare it. If only the migration added it, a database that
        skipped v10 would fail EVERY message write."""
        import pathlib as _pl
        src = (_pl.Path(__file__).resolve().parent.parent
               / "database" / "sqlite.py").read_text()
        i = src.index("CREATE TABLE IF NOT EXISTS messages")
        ddl = src[i:i + 1400]
        assert "content_seg" in ddl, "base messages DDL omits content_seg"

    def test_reconcile_runs_once_not_on_every_startup(self):
        """MEDIUM — the first implementation probed with `integrity-check rank=1`,
        which RE-DERIVES every token from the content source: measured at ~100 ms
        per 3,000 rows, i.e. the same cost as the rebuild it was deciding about.
        Probing to avoid work that costs as much as the probe is the work done
        twice."""
        import sqlite3
        from core.knowledge_store import KnowledgeStore

        conn = sqlite3.connect(":memory:")
        store = KnowledgeStore(conn)
        store.ensure_tables()
        store.upsert_chunk("n.md", 0, "标题", "配置文件说明", "h1")

        calls: list[str] = []

        class _Spy:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *a, **kw):
                if "'rebuild'" in sql or "integrity-check" in sql:
                    calls.append(sql.strip()[:40])
                return self._inner.execute(sql, *a, **kw)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        store._conn = _Spy(conn)   # type: ignore[assignment]
        store.ensure_tables()
        store.ensure_tables()
        store._conn = conn         # type: ignore[assignment]
        assert calls == [], f"re-probed or rebuilt on a later startup: {calls}"
        conn.close()

    def test_a_malformed_index_signal_is_not_swallowed_on_delete(self):
        """MEDIUM — the delete path caught only OperationalError, but a corrupt
        index raises DatabaseError, so the real signal escaped uncaught AND the
        base-table delete never ran."""
        import inspect
        from core import transcript_indexer
        src = inspect.getsource(transcript_indexer.TranscriptStore.remove_session)
        assert "sqlite3.DatabaseError" in src, src
        assert "logger.error" in src, src


class TestSecurityHardening:
    """SECURITY REVIEW findings — controls that looked present but did not hold."""

    def test_the_seg_columns_never_reach_a_client(self):
        """MEDIUM — a real data-exposure regression this change introduced.

        `*_seg` is a DERIVED duplicate of a column already in the row, and
        `list_by_session` feeds the chat-history API with no response_model, so
        every message's text was shipped TWICE (verified before the fix: the
        response carried `content_seg` = 'type text text 部署 署重 重启 ...').
        No new secret, but it doubles the payload and puts a second copy of
        message text into any client log or HAR capture.
        """
        import asyncio

        from database.sqlite import SQLiteDatabase

        async def go(tmp):
            db = SQLiteDatabase(str(tmp))
            await db.initialize()
            await db.sessions.put({"id": "s1", "agent_id": "a1", "title": "t"})
            await db.messages.put({
                "session_id": "s1", "role": "user",
                "content": [{"type": "text", "text": "部署重启完成"}]})
            return await db.messages.list_by_session("s1")

        import tempfile
        import pathlib as _pl
        rows = asyncio.run(go(_pl.Path(tempfile.mkdtemp()) / "t.db"))
        assert rows, "no rows returned"
        for row in rows:
            leaked = [k for k in row if k.endswith("_seg")]
            assert not leaked, f"internal index columns reached the caller: {leaked}"
            assert row.get("content"), "the real content must survive the filter"

    def test_the_seg_write_path_requires_the_confirmation_token(self):
        """MEDIUM — `_populate_seg_columns` is importable and rewrites rows in a
        live database. A bare `apply=True` boolean is one attribute away for any
        future job or 'auto-repair', which is the shape that once destroyed a user
        database. The unguessable token makes reaching the write a deliberate act.
        """
        import sqlite3

        import pytest

        from scripts.backfill_cjk_escapes import (
            WRITE_CONFIRMATION, _populate_seg_columns)

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE messages(id TEXT PRIMARY KEY, content TEXT, "
                     "content_seg TEXT)")
        conn.execute("INSERT INTO messages VALUES('a', '部署', NULL)")

        with pytest.raises(PermissionError):
            _populate_seg_columns(conn, apply=True)          # no token
        assert conn.execute(
            "SELECT content_seg FROM messages").fetchone()[0] is None

        _populate_seg_columns(conn, apply=True, confirm=WRITE_CONFIRMATION)
        assert conn.execute(
            "SELECT content_seg FROM messages").fetchone()[0]
        conn.close()

    def test_the_seg_only_apply_path_still_takes_a_backup(self):
        """MEDIUM — the backup was taken only when escaped rows existed, so the
        STEADY STATE after the first successful run (zero escapes, segmentation
        still pending) wrote rows with no snapshot, contradicting what `--apply`
        promises."""
        import sqlite3

        from scripts.backfill_cjk_escapes import backfill, WRITE_CONFIRMATION

        import tempfile
        import pathlib as _pl
        db = _pl.Path(tempfile.mkdtemp()) / "t.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE messages(id TEXT PRIMARY KEY, content TEXT, "
                     "content_seg TEXT)")
        conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content_seg, "
                     "content=messages, content_rowid=rowid)")
        # Raw CJK, so there is NOTHING for the escape phase to convert — only
        # segmentation remains, which is the path that skipped the backup.
        conn.execute("INSERT INTO messages VALUES('a', '部署重启完成', NULL)")
        conn.commit()
        conn.close()

        report = backfill(db, apply=True, confirm=WRITE_CONFIRMATION)
        assert report["converted"] == 0, "precondition: no escaped rows"
        assert report["seg_populated"].get("messages") == 1, report
        assert report["backup_path"], "wrote rows with no backup"
        assert _pl.Path(report["backup_path"]).exists()

    def test_a_query_with_thousands_of_terms_is_bounded(self):
        """LOW-MEDIUM — `search()` had no term cap while its sibling capped at 32.
        Each term adds an OR-clause; measured 5,000 terms -> a 283 KB expression.
        The HTTP route limits the query string, but internal callers reach this
        directly."""
        import inspect

        from core import session_recall
        src = inspect.getsource(session_recall.SessionRecall.search)
        assert "[:32]" in src, "search() does not cap term count"


class TestQueryCapAppliesToTheRunNotTheTerm:
    """A bug my own DoS fix introduced, caught by an existing recall test.

    Capping the QUERY TERM at 16 characters bounded the CJK bigram chain, but it
    also truncated long ASCII terms: `axolotl-evolution` (17 chars) became
    `axolotl-evolutio`, a prefix of no indexed token, so it matched NOTHING and a
    knowledge-archive recall test went red. The cap belongs on the CJK RUN — an
    ASCII token contributes exactly one clause however long it is, so it needs no
    bound at all.
    """

    def test_a_long_ascii_term_is_not_truncated(self):
        from core.cjk_index import expand_cjk_query
        assert expand_cjk_query("axolotl-evolution") == '"axolotl-evolution"'
        assert expand_cjk_query(
            "a-very-long-hyphenated-identifier-name") == \
            '"a-very-long-hyphenated-identifier-name"'

    def test_a_long_ascii_term_still_matches_a_real_index(self):
        """The end-to-end form: the unit assertion above would not catch a future
        cap applied somewhere else in the path."""
        import sqlite3
        from core.cjk_index import expand_cjk, expand_cjk_query

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE f USING fts5(seg)")
        conn.execute("INSERT INTO f VALUES(?)", (expand_cjk(
            "- axolotl-evolution archived correction phrase."),))
        n = conn.execute("SELECT count(*) FROM f WHERE f MATCH ?",
                         (expand_cjk_query("axolotl-evolution"),)).fetchone()[0]
        assert n == 1, "a long ASCII term stopped matching"
        conn.close()

    def test_a_space_free_cjk_paste_is_still_bounded(self):
        """The property the cap exists for must survive the fix."""
        from core.cjk_index import expand_cjk_query
        expr = expand_cjk_query("配置文件语义搜索" * 2500)   # 20,000 chars
        assert len(expr) < 1000, f"expression is {len(expr)} bytes"


class TestGate2Hardening:
    """Gate-2 adversarial findings — each reproduced before being fixed.

    The two HIGHs are the ones that mattered: the migration re-pointed each index
    at a column that was NULL for every existing row and rebuilt from it, so ALL
    historical search — English included — silently returned nothing, while
    `integrity-check` still reported OK. The original design deferred population to
    an operator-run script; this method runs automatically on daemon start, so that
    left a dead-search window on every deploy.
    """

    def test_the_knowledge_migration_preserves_historical_search(self):
        """HIGH — reproduced: a chunk matching '"daemon"' before the migration
        returned NOTHING after, and the health probe could not see it."""
        import sqlite3
        from core.cjk_index import expand_cjk_query
        from core.knowledge_store import KnowledgeStore

        conn = sqlite3.connect(":memory:")
        # A pre-change database: raw-column index, populated.
        conn.execute("""CREATE TABLE knowledge_chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT NOT NULL,
            chunk_index INTEGER NOT NULL, heading TEXT, content TEXT NOT NULL,
            content_hash TEXT NOT NULL, metadata TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("""CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            content, heading, source_file,
            content=knowledge_chunks, content_rowid=id)""")
        conn.executemany(
            "INSERT INTO knowledge_chunks(source_file, chunk_index, heading, "
            "content, content_hash) VALUES(?,?,?,?,?)",
            [("n.md", 0, "Heading", "hello world daemon crash", "h1"),
             ("m.md", 1, "部署说明", "配置文件的语义搜索", "h2")])
        conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")

        def hits(term):
            return conn.execute(
                "SELECT count(*) FROM knowledge_fts WHERE knowledge_fts MATCH ?",
                (expand_cjk_query(term),)).fetchone()[0]

        assert hits("daemon") == 1, "precondition: English history is searchable"

        KnowledgeStore(conn).ensure_tables()      # the automatic migration

        assert hits("daemon") == 1, "the migration killed historical English search"
        assert hits("配置") == 1, "history did not become CJK-searchable"
        assert hits("部署") == 1, "heading history not segmented"
        conn.execute(
            "INSERT INTO knowledge_fts(knowledge_fts, rank) "
            "VALUES('integrity-check', 1)")
        conn.close()

    def test_the_transcript_migration_preserves_historical_search(self):
        """HIGH, same shape on the transcript index."""
        import sqlite3
        from core.cjk_index import expand_cjk_query
        from core.transcript_indexer import TranscriptStore

        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE transcript_chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            source_file TEXT NOT NULL, chunk_index INTEGER NOT NULL, role TEXT,
            content TEXT NOT NULL, content_hash TEXT NOT NULL, metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("""CREATE VIRTUAL TABLE transcript_fts USING fts5(
            content, source_file,
            content=transcript_chunks, content_rowid=id)""")
        conn.execute(
            "INSERT INTO transcript_chunks(session_id, source_file, chunk_index, "
            "role, content, content_hash) VALUES('s1','a.jsonl',0,'user',?,'h1')",
            ("daemon crash 部署完成",))
        conn.execute("INSERT INTO transcript_fts(transcript_fts) VALUES('rebuild')")

        def hits(term):
            return conn.execute(
                "SELECT count(*) FROM transcript_fts WHERE transcript_fts MATCH ?",
                (expand_cjk_query(term),)).fetchone()[0]

        assert hits("daemon") == 1
        TranscriptStore(conn).ensure_tables()
        assert hits("daemon") == 1, "the migration killed historical search"
        assert hits("部署") == 1, "history did not become CJK-searchable"
        conn.close()

    def test_the_messages_migration_preserves_historical_search(self, tmp_path):
        """HIGH — the same defect in schema migration v10."""
        import asyncio
        import sqlite3
        from core.cjk_index import expand_cjk_query
        from database.sqlite import SQLiteDatabase

        db_file = tmp_path / "m.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            "CREATE TABLE messages(id TEXT PRIMARY KEY, session_id TEXT, "
            "role TEXT, content TEXT, model TEXT, metadata TEXT, created_at TEXT, "
            "updated_at TEXT, sent INTEGER DEFAULT 1, pending_seq INTEGER, "
            "claimed_at TEXT, expires_at INTEGER)")
        conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5("
                     "content, content=messages, content_rowid=rowid)")
        conn.execute("INSERT INTO messages(id, content, created_at, updated_at) "
                     "VALUES('a', 'daemon crash OOM investigation', 't', 't')")
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.execute("PRAGMA user_version = 9")
        conn.commit()
        conn.close()

        asyncio.run(SQLiteDatabase(str(db_file)).initialize())

        conn = sqlite3.connect(str(db_file))
        n = conn.execute(
            "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?",
            (expand_cjk_query("daemon"),)).fetchone()[0]
        assert n == 1, "v10 killed historical message search"
        conn.execute(
            "INSERT INTO messages_fts(messages_fts, rank) "
            "VALUES('integrity-check', 1)")
        conn.close()

    def test_all_four_recall_domains_share_one_cjk_class(self):
        """MED — memory_index kept a LOCAL copy of the character class, so its gate
        never let Korean or Japanese reach the widened expansion it guards. The
        Python-scored domains and the FTS-scored domains then disagreed about where
        a word ends, which is exactly what delegating to one authority prevents."""
        from core.cjk_index import _CJK_RE as authority
        from core.cjk_index import expand_cjk
        from core.memory_index import _CJK_RE as imported
        from core.memory_index import _bm25_tokenize

        assert imported is authority, "memory_index re-declared the CJK class"
        for text in ("설정파일", "コンフィグ", "配置文件"):
            assert _bm25_tokenize(text) == expand_cjk(text.lower()).split(), text

    def test_a_failed_transcript_migration_is_not_reported_as_already_exists(self):
        """MED — the CREATE's `except OperationalError` also swallowed the
        migration, so a DROP followed by a failing CREATE left NO index while
        logging 'already exists' at debug level."""
        import inspect
        from core import transcript_indexer
        src = inspect.getsource(transcript_indexer.TranscriptStore.ensure_tables)
        create_block = src[:src.index("_migrate_fts_to_seg_columns")]
        assert "already exists or FTS5 unavailable" in create_block, (
            "the debug handler moved — re-check that the migration is outside it")
        # The migration calls must NOT sit inside that handler's try block.
        after = src[src.index("_migrate_fts_to_seg_columns"):]
        assert "except sqlite3.OperationalError" not in after, after

    def test_connector_only_tokens_tokenize_the_same_on_both_sides(self):
        """LOW — `expand_cjk('___')` stored the token `___`, which FTS5 discards,
        while `expand_cjk_query('___')` asked for the phrase `"___"`: text stored
        but unfindable. Also treated the same prefix differently in `___` vs
        `___配置`."""
        from core.cjk_index import expand_cjk, expand_cjk_query
        for junk in ("___", "-_-", "--"):
            assert expand_cjk(junk) == "", junk
            assert expand_cjk_query(junk) == "", junk
        # A real hyphenated identifier must still survive intact.
        assert expand_cjk("goal-pipeline") == "goal-pipeline"
        assert expand_cjk("___配置") == "配置"

    def test_stale_rows_are_retried_not_silently_skipped(self):
        """MED — a row the daemon rewrote between survey and write was counted and
        dropped, so the report showed `would_convert: 0` beside a nonzero stale
        count, which reads as 'done'."""
        import inspect
        from scripts import backfill_cjk_escapes
        src = inspect.getsource(backfill_cjk_escapes._populate_seg_columns)
        assert "_STALE_RETRY_PASSES" in src, "no bounded retry for stale rows"
        assert backfill_cjk_escapes._STALE_RETRY_PASSES >= 1
