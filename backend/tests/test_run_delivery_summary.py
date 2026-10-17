"""Tests for core.run_delivery_summary (pipeline design §4-E) + the prompt_builder
``SWARM_APP_SESSION_ID`` env injection.

Methodology: real sqlite files in tmp_path (schema copied from database/sqlite.py),
a real throwaway git repo for file_changes (hooks disabled via ``-c core.hooksPath``
on the command, git config untouched), and pure-function assertions on the
rendered markdown. TZ is pinned to Asia/Shanghai (UTC+8, no DST) so the UTC→local
conversion is checked against a known offset.

Invariants:
- token_usage rows are LOCAL time; a UTC run window selects exactly the rows whose
  local wall clock falls inside it (a naive string compare would pick wrong rows).
- No session id / db / table / rows → None.
- capture_intent skips meta blocks, short acks and unsent rows; strictly before.
- file_changes: A/M/D statuses, numstat lines, base_ref ``<sha>^`` only for M/D.
- Renderer: exactly two ━ lines, zero ``` fences, #base only on modified links,
  caps (15 files / 7 requirements), _md_cell neutralization (no injected heading),
  "估算" label when there is no measured usage.
- build_delivery_summary never raises.
"""

import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from core import run_delivery_summary as rds

HEADING_RE = re.compile(r"^\s*(>\s*)*#{1,6}(\s|$)")


@pytest.fixture
def shanghai_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _usage_db(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE token_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,"
        " session_id TEXT, source TEXT NOT NULL DEFAULT 'cli', input_tokens INTEGER NOT NULL DEFAULT 0,"
        " output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,"
        " cache_create_tokens INTEGER NOT NULL DEFAULT 0, cost_usd REAL, model TEXT)")
    conn.executemany(
        "INSERT INTO token_usage (timestamp, session_id, input_tokens, output_tokens,"
        " cache_read_tokens, cache_create_tokens, cost_usd) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


# ── token_usage_for_window ───────────────────────────────────────────────────

class TestTokenUsage:
    START, END = "2026-10-17T09:00:00+00:00", "2026-10-17T10:00:00+00:00"  # = 17:00-18:00 local

    def test_utc_window_converted_to_local(self, tmp_path, shanghai_tz):
        db = _usage_db(tmp_path / "d.db", [
            ("2026-10-17T17:30:00", "s1", 10, 100, 1000, 5, 1.25),   # inside (local)
            ("2026-10-17T17:59:59", "s1", 1, 2, 3, 4, 0.75),         # inside
            ("2026-10-17T09:30:00", "s1", 99, 99, 99, 99, 9.0),      # naive-UTC trap: 01:30Z
            ("2026-10-17T18:30:00", "s1", 99, 99, 99, 99, 9.0),      # after window
            ("2026-10-17T17:30:00", "other", 99, 99, 99, 99, 9.0),   # other session
            ("2026-10-17T09:15:00Z", "s1", 0, 7, 0, 0, 0.5),         # explicit UTC row, inside
        ])
        got = rds.token_usage_for_window("s1", self.START, self.END, db_path=db)
        assert got == {"cost_usd": 2.5, "input_tokens": 11, "output_tokens": 109,
                       "cache_read_tokens": 1003, "cache_creation_tokens": 9, "rows": 3}
        assert rds.utc_iso_to_local_naive(self.START) == "2026-10-17T17:00:00"

    def test_none_cases(self, tmp_path, shanghai_tz):
        db = _usage_db(tmp_path / "d.db", [("2026-10-17T17:30:00", "s1", 1, 1, 1, 1, 1.0)])
        assert rds.token_usage_for_window(None, self.START, self.END, db_path=db) is None
        assert rds.token_usage_for_window("", self.START, self.END, db_path=db) is None
        assert rds.token_usage_for_window("nobody", self.START, self.END, db_path=db) is None
        assert rds.token_usage_for_window("s1", self.START, self.END,
                                          db_path=tmp_path / "missing.db") is None
        empty = tmp_path / "empty.db"
        sqlite3.connect(empty).execute("CREATE TABLE t (x)").connection.close()
        assert rds.token_usage_for_window("s1", self.START, self.END, db_path=empty) is None
        assert rds.token_usage_for_window("s1", "garbage", self.END, db_path=db) is None


# ── run_token_usage (turn-end stamping, HIGH-1) ──────────────────────────────

class TestRunTokenUsage:
    START, END = TestTokenUsage.START, TestTokenUsage.END  # 17:00-18:00 local

    def _db(self, tmp_path, rows, messages=()):
        db = _usage_db(tmp_path / "d.db", rows)
        if messages:
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE messages (id TEXT, session_id TEXT, role TEXT,"
                         " content TEXT, created_at TEXT, sent INTEGER)")
            conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?)", messages)
            conn.commit()
            conn.close()
        return db

    def test_closing_turn_row_after_completed_at_is_included(self, tmp_path, shanghai_tz):
        db = self._db(tmp_path, [
            ("2026-10-17T16:30:00", "s1", 9, 9, 9, 9, 9.0),    # turn before the run
            ("2026-10-17T17:20:00", "s1", 10, 100, 1000, 5, 1.0),  # run-create turn (pre-run inside)
            ("2026-10-17T18:01:00", "s1", 1, 50, 7, 2, 0.5),   # turn in flight at completed_at
            ("2026-10-17T18:30:00", "s1", 99, 99, 99, 99, 9.0),  # a later turn
        ])
        got = rds.run_token_usage("s1", self.START, self.END, db_path=db)
        assert (got["turns"], got["cost_usd"], got["output_tokens"]) == (2, 1.5, 150)
        assert got["tokens"] == 11 + 150 + 7
        assert got["complete"] is True and got["excludes_current_turn"] is False
        assert got["includes_prerun"] is True
        # legacy function unchanged: strict window, legacy keys only
        legacy = rds.token_usage_for_window("s1", self.START, self.END, db_path=db)
        assert legacy == {"cost_usd": 1.0, "input_tokens": 10, "output_tokens": 100,
                          "cache_read_tokens": 1000, "cache_creation_tokens": 5, "rows": 1}

    def test_single_turn_run_measured_after_turn_ends(self, tmp_path, shanghai_tz):
        # Whole pipeline in one turn: the only row lands after completed_at.
        db = self._db(tmp_path, [("2026-10-17T18:01:00", "s1", 100, 77000, 9_000_000, 5000, 23.4)])
        got = rds.run_token_usage("s1", self.START, self.END, db_path=db)
        assert got["turns"] == 1 and got["cost_usd"] == 23.4 and got["complete"] is True

    def test_current_turn_not_recorded_yet(self, tmp_path, shanghai_tz):
        db = self._db(tmp_path, [("2026-10-17T17:20:00", "s1", 1, 2, 3, 4, 0.25)])
        got = rds.run_token_usage("s1", self.START, self.END, db_path=db)
        assert got["excludes_current_turn"] is True and got["complete"] is False
        assert got["turns"] == 1 and got["tokens"] == 1 + 2 + 4

    def test_row_after_new_user_message_belongs_to_later_turn(self, tmp_path, shanghai_tz):
        db = self._db(tmp_path, [
            ("2026-10-17T17:20:00", "s1", 1, 2, 3, 4, 0.25),
            ("2026-10-17T18:30:00", "s1", 99, 99, 99, 99, 9.0),  # after a new user message
        ], messages=[("m1", "s1", "user", "[]", "2026-10-17T18:10:00", 1)])
        got = rds.run_token_usage("s1", self.START, self.END, db_path=db)
        assert got["turns"] == 1 and got["excludes_current_turn"] is True

    def test_caller_turn_end_bounds_the_extension(self, tmp_path, shanghai_tz):
        db = self._db(tmp_path, [("2026-10-17T17:20:00", "s1", 1, 2, 3, 4, 0.25),
                                 ("2026-10-17T18:05:00", "s1", 1, 2, 3, 4, 0.25)])
        early = rds.run_token_usage("s1", self.START, self.END, db_path=db,
                                    turn_end_iso="2026-10-17T10:02:00+00:00")
        late = rds.run_token_usage("s1", self.START, self.END, db_path=db,
                                   turn_end_iso="2026-10-17T10:06:00+00:00")
        assert early["turns"] == 1 and early["excludes_current_turn"] is True
        assert late["turns"] == 2 and late["complete"] is True

    def test_nothing_measurable_is_none(self, tmp_path, shanghai_tz):
        db = self._db(tmp_path, [("2026-10-17T16:00:00", "s1", 1, 1, 1, 1, 1.0)])
        assert rds.run_token_usage("s1", self.START, self.END, db_path=db) is None
        assert rds.run_token_usage(None, self.START, self.END, db_path=db) is None
        assert rds.run_token_usage("s1", self.START, self.END, db_path=tmp_path / "no.db") is None


def test_usage_labels_say_what_they_cover():
    base = {"cost_usd": 3.0, "output_tokens": 5000, "cache_read_tokens": 2_000_000,
            "input_tokens": 100, "cache_creation_tokens": 900}
    partial = rds._usage_label({**base, "tokens": 6000, "excludes_current_turn": True,
                                "includes_prerun": True})
    assert partial == "💰 已结算 6.0K tokens · $3.00（输出 5.0K tokens · 缓存读 2.0M；不含当前一轮，含建 run 前的同一轮）"
    full = rds._usage_label({**base, "excludes_current_turn": False, "includes_prerun": True})
    assert full == "💰 $3.00（输出 5.0K tokens · 缓存读 2.0M；含建 run 前的同一轮）"
    assert "估算" not in partial + full


# ── capture_intent ───────────────────────────────────────────────────────────

def _msg_db(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,"
                 " content TEXT NOT NULL, created_at TEXT NOT NULL, sent INTEGER)")
    for i, (sid, role, content, created, sent) in enumerate(rows):
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                     (str(i), sid, role, json.dumps(content, ensure_ascii=False), created, sent))
    conn.commit()
    conn.close()
    return path


def _blocks(*texts):
    return [{"type": "text", "text": t} for t in texts]


def test_capture_intent_skips_meta_and_acks(tmp_path, shanghai_tz):
    real = "清理 127K 退化污染，再起 full run：流式输出退化检测"
    db = _msg_db(tmp_path / "m.db", [
        ("s1", "user", _blocks("older substantive request"), "2026-10-17T16:00:00.1", None),
        ("s1", "user", _blocks("## Current UI State\nopen: x", real), "2026-10-17T16:50:00.5", 1),
        ("s1", "assistant", _blocks("a long assistant reply here"), "2026-10-17T16:51:00", None),
        ("s1", "user", _blocks("<task-notification>done</task-notification>"), "2026-10-17T16:55:00", 1),
        ("s1", "user", _blocks("[RESUMED CONVERSATION HISTORY] blah blah"), "2026-10-17T16:56:00", 1),
        ("s1", "user", _blocks("继续"), "2026-10-17T16:58:00", 1),
        ("s1", "user", _blocks("Continue"), "2026-10-17T16:58:30", 1),
        ("s1", "user", _blocks("pending, never delivered text"), "2026-10-17T16:59:00", 0),
        ("s1", "user", _blocks("after the run started, ignore me"), "2026-10-17T17:10:00", 1),
        ("s2", "user", _blocks("another session's message"), "2026-10-17T16:59:30", 1),
    ])
    before = "2026-10-17T09:02:48.440236+00:00"  # 17:02:48 local
    assert rds.capture_intent("s1", before, db_path=db) == (real, "user_message")
    assert rds.capture_intent(None, before, db_path=db) is None
    assert rds.capture_intent("s1", "2026-10-17T07:00:00+00:00", db_path=db) is None
    assert rds.capture_intent("s1", before, db_path=tmp_path / "missing.db") is None


def test_capture_intent_short_requests_and_option_picks(tmp_path, shanghai_tz):
    before = "2026-10-17T09:02:48+00:00"  # 17:02:48 local
    db = _msg_db(tmp_path / "m.db", [
        ("s1", "user", _blocks("修复登录bug"), "2026-10-17T16:50:00", 1),
        ("s1", "user", _blocks("选 A"), "2026-10-17T16:55:00", 1),
        ("s1", "user", _blocks("B. 中文提示"), "2026-10-17T16:56:00", 1),
        ("s1", "user", _blocks("2"), "2026-10-17T16:57:00", 1),
        ("s1", "user", _blocks("ok"), "2026-10-17T16:58:00", 1),
    ])
    assert rds.capture_intent("s1", before, db_path=db) == ("修复登录bug", "user_message")
    db2 = _msg_db(tmp_path / "m2.db", [
        ("s1", "user", _blocks("a bug in login page"), "2026-10-17T16:50:00", 1)])
    assert rds.capture_intent("s1", before, db_path=db2) == ("a bug in login page", "user_message")
    assert rds._is_ack("继续。") and rds._is_ack("选择 C 吧") and not rds._is_ack("修复登录bug")


def test_capture_intent_reads_bounded_content(tmp_path, shanghai_tz):
    """Only substr(content,1,20000) is read; a cut JSON list still yields its text."""
    before = "2026-10-17T09:02:48+00:00"
    huge = _blocks("真正的需求：修复导出", "x" * 30000)
    db = _msg_db(tmp_path / "m.db", [("s1", "user", huge, "2026-10-17T16:50:00", 1)])
    text, src = rds.capture_intent("s1", before, db_path=db)
    assert text == "真正的需求：修复导出" and src == "user_message"
    plain = _msg_db(tmp_path / "p.db", [("s1", "user", "[urgent] fix export", "2026-10-17T16:50:00", 1)])
    assert rds.capture_intent("s1", before, db_path=plain)[0] == "[urgent] fix export"


# ── file_changes ─────────────────────────────────────────────────────────────

_GITC = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "-c", "user.name=t", "-c", "user.email=t@example.com"]


def _git(repo, *args):
    return subprocess.run(["git", *_GITC, "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, timeout=30).stdout.strip()


def _commit(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "m.py").write_text("a\nb\nc\n")
    (r / "d.py").write_text("x\ny\n")
    _commit(r, "base")
    return r


def test_file_changes_from_commits(repo):
    (repo / "a.py").write_text("1\n2\n3\n")
    (repo / "m.py").write_text("a\nB\nc\nd\n")
    (repo / "d.py").unlink()
    sha1 = _commit(repo, "run commit 1")
    (repo / "a.py").write_text("1\n2\n3\n4\n")
    sha2 = _commit(repo, "run commit 2")
    got = {c["path"]: c for c in rds.file_changes(
        [{"repo": str(repo), "sha": sha1[:8]}, {"repo": str(repo), "sha": sha2},
         {"repo": str(repo), "sha": "; rm -rf /"}], [])}
    assert set(got) == {"a.py", "m.py", "d.py"}
    assert got["a.py"]["status"] == "A" and got["a.py"]["base_ref"] is None
    assert (got["a.py"]["added"], got["a.py"]["removed"]) == (4, 0)  # summed over 2 commits
    assert got["m.py"]["status"] == "M" and got["m.py"]["base_ref"] == f"{sha1[:8]}^"
    assert (got["m.py"]["added"], got["m.py"]["removed"]) == (2, 1)
    assert got["d.py"]["status"] == "D" and (got["d.py"]["added"], got["d.py"]["removed"]) == (0, 2)
    assert got["m.py"]["abs_path"] == str((repo / "m.py").resolve())


def test_file_changes_worktree_fallback(repo):
    (repo / "new.py").write_text("1\n2\n")
    (repo / "m.py").write_text("a\nc\n")
    got = {c["path"]: c for c in rds.file_changes([], [str(repo / "new.py"), str(repo / "m.py")],
                                                  repo_root=repo)}
    assert got["new.py"]["status"] == "A" and got["new.py"]["added"] == 2
    assert got["m.py"]["status"] == "M" and (got["m.py"]["added"], got["m.py"]["removed"]) == (0, 1)
    assert all(c["base_ref"] is None for c in got.values())


def test_file_changes_resolve_each_file_against_its_own_repo(repo, tmp_path, monkeypatch):
    """MED-1: no repo_root (non-SwarmAI project) — absolute paths use their own git
    toplevel, relative paths with no resolvable root become plain text."""
    other = tmp_path / "other"
    (other / "pkg").mkdir(parents=True)
    _git(other, "init", "-q")
    (other / "pkg" / "o.py").write_text("1\n")
    _commit(other, "o")
    (other / "pkg" / "o.py").write_text("1\n2\n")
    (repo / "m.py").write_text("a\n")
    monkeypatch.chdir(repo)  # the cwd's repo must never be guessed for relative paths
    got = {c["path"]: c for c in rds.file_changes(
        [{"sha": "abc1234"}],  # commit without repo and no repo_root → skipped, not cwd
        [str(other / "pkg" / "o.py"), "m.py"], repo_root=None)}
    assert got["pkg/o.py"]["abs_path"] == str((other / "pkg" / "o.py").resolve())
    assert got["pkg/o.py"]["status"] == "M" and got["pkg/o.py"]["added"] == 1
    assert got["m.py"]["abs_path"] == "" and got["m.py"]["added"] is None
    md = rds.render_delivery_summary_md({"id": "run_z", "status": "completed"}, {}, {}, {},
                                        None, list(got.values()), "/r/REPORT.md")
    assert "| ✏️ | m.py | — |" in md
    assert f"[pkg/o.py]({(other / 'pkg' / 'o.py').resolve()})" in md
    # a relative path that does not exist in the GIVEN root is not linked into it
    wrong = rds.file_changes([], ["not/here.py"], repo_root=repo)
    assert wrong[0]["abs_path"] == ""


# ── render_delivery_summary_md ───────────────────────────────────────────────

RUN = {"id": "run_abc12345", "profile": "bugfix", "status": "completed",
       "intent": "清理污染\n## injected heading\n━━━━ fake rule ``` fence",
       "requirement": "rewritten requirement",
       "created_at": "2026-10-17T09:00:00+00:00", "completed_at": "2026-10-17T11:27:00+00:00",
       "commits": [{"repo": "/r", "sha": "abc1234", "files": []}],
       "stages": [{"stage": "build", "token_cost": 100},
                  {"stage": "test", "exit_criteria": [{"id": "X1", "met": True},
                                                      {"id": "X2", "status": "pass"},
                                                      {"id": "X3", "met": False}]}]}


def _reqs(n):
    reqs = [{"id": f"FR-{i}", "statement": f"requirement {i}", "status": "confirmed",
             "source": {"kind": "user-stated"}} for i in range(1, n + 1)]
    reqs[0]["statement"] = "## evil heading\n# another\n> quote | pipe"
    reqs[1]["source"] = {"kind": "assumption"}
    return reqs


def _files(n):
    out = [{"path": "new.py", "abs_path": "/r/new.py", "status": "A", "added": 5, "removed": 0,
            "base_ref": None}]
    for i in range(n - 1):
        out.append({"path": f"m{i}.py", "abs_path": f"/r/m{i}.py", "status": "M",
                    "added": i, "removed": 1, "base_ref": "abc1234^"})
    return out


EVAL = {"requirements": _reqs(8),
        "clarifications": [{"qid": "Q1", "answer": "B", "answered_by": "self",
                            "options": ["A. only zh", "B. bilingual\n## x"]},
                           {"qid": "Q2", "answer": "user said so", "answered_by": "user"}]}
DELIVER = {"completion_audit": {"requirements_met": 8, "requirements_total": 8},
           "adversarial_review": {"findings_total": 13, "findings_fixed": 13}, "push_ready": True}
TESTS = {"passed": True, "failed": 0, "tests_total": 34}


def _render(**kw):
    args = dict(run_state=RUN, eval_artifact=EVAL, deliver_artifact=DELIVER, test_info=TESTS,
                usage=None, file_changes=_files(16), report_path="/abs/runs/run_abc12345/REPORT.md",
                stage_gaps={"build": {"wall_minutes": 77.4}, "test": {"wall_minutes": 9.9}},
                estimate_tokens=635000)
    args.update(kw)
    return rds.render_delivery_summary_md(**args)


def test_render_structure_and_caps():
    md = _render()
    lines = md.split("\n")
    assert sum("━" in ln for ln in lines) == 2 and lines[0].startswith("━") and lines[-1].startswith("━")
    assert "```" not in md
    assert "⏱ 147 分钟（BUILD 最长，77 分钟）" in lines[1] and "💰 估算 635,000 tokens" in lines[1]
    assert "**最终需求**（8 条，8 条达成）" in md and "- …另 1 条见报告" in md
    assert sum(ln.startswith("- ✅") or ln.startswith("- ⚠️") for ln in lines) == 7
    assert "另 1 个文件见报告 §8" in md
    rows = [ln for ln in lines if ln.startswith("| 🆕") or ln.startswith("| ✏️")]
    assert len(rows) == 15 and rows[0].startswith("| 🆕")  # added first
    assert "[new.py](/r/new.py)" in md and "/r/new.py#base" not in md
    assert "[m14.py](/r/m14.py#base=abc1234^)" in md
    assert rows[1].startswith("| ✏️ | [m14.py]")  # then by churn desc
    assert "⚠️ **AI 替你拍板 2 处**" in md and "Q1 bilingual" in md and "Q2" not in md
    assert "测试 34/34 通过 · Gate-2 13 条发现全部修复 · 结束条件 2/3 满足 · push-ready" in md
    assert "[REPORT.md](/abs/runs/run_abc12345/REPORT.md)" in md


def test_render_neutralizes_injected_text():
    md = _render()
    assert not [ln for ln in md.split("\n") if HEADING_RE.match(ln)]
    assert "rewritten requirement" not in md  # intent wins over requirement
    assert "\\| pipe" in md  # table-safe pipe
    legacy = _render(run_state={**RUN, "intent": None, "commits": []},
                     eval_artifact={"acceptance_criteria": ["AC1: ok\n## h"]}, file_changes=[])
    assert "> rewritten requirement" in legacy and "- ▫️ AC1: ok ⏎ ## h" in legacy
    assert not [ln for ln in legacy.split("\n") if HEADING_RE.match(ln)]


LINK_OR_IMAGE_RE = re.compile(r"(?<!\\)\]\((?!/r/|/abs/)")  # unescaped `](` not ours


def test_render_neutralizes_forged_links_and_images():
    """LOW-1: intent / requirement / decision text cannot forge a link, image,
    autolink, emphasis or table cell."""
    evil = "看这里 [REPORT.md](/Users/x/.env) 和 ![i](/Users/x.png) <http://e.vil> **粗** _斜_ `c` | x"
    md = _render(run_state={**RUN, "intent": evil},
                 eval_artifact={"requirements": [{"id": "FR-1", "statement": evil,
                                                  "source": {"kind": "assumption"}}]})
    assert not LINK_OR_IMAGE_RE.search(md)
    assert "\\[REPORT.md\\]\\(/Users/x/.env\\)" in md and "\\!\\[i\\]" in md
    assert "\\<http://e.vil\\>" in md and "\\*\\*粗\\*\\*" in md and "\\_斜\\_" in md
    assert "\\`c\\`" in md and "\\| x" in md
    assert "`run_abc12345`" in md  # code span ids are not backslash-escaped


def test_render_usage_and_intent_cap():
    usage = {"cost_usd": 23.4, "output_tokens": 283_000, "cache_read_tokens": 165_000_000,
             "input_tokens": 1, "cache_creation_tokens": 1, "rows": 5}
    md = _render(usage=usage, run_state={**RUN, "intent": "字" * 500})
    assert "💰 $23.40（输出 283K tokens · 缓存读 165M）" in md and "估算" not in md
    quote = next(ln for ln in md.split("\n") if ln.startswith("> "))
    assert len(quote) <= 2 + 200 + 1


# ── build_delivery_summary ───────────────────────────────────────────────────

@pytest.mark.parametrize("run_state,artifacts", [
    (None, None),
    ("garbage", "x"),
    ({"id": "run_x", "status": "completed", "stages": "bad", "commits": [{"sha": "; rm -rf /"}],
      "files_touched": 42, "created_at": 7, "session_id": ["not", "a", "str"]},
     {"evaluate": [], "deliver": "x", "test": {"data": None}}),
])
def test_build_never_raises_on_garbage(tmp_path, run_state, artifacts):
    md = rds.build_delivery_summary(run_state, artifacts, tmp_path / "nope",
                                    db_path=tmp_path / "missing.db")
    assert md.split("\n")[0].startswith("━") and "REPORT.md" in md and "```" not in md


def test_build_internal_error_yields_minimal(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(rds, "render_delivery_summary_md", boom)
    md = rds.build_delivery_summary({"id": "run_y", "status": "failed"}, {}, tmp_path,
                                    db_path=tmp_path / "missing.db")
    assert "`run_y`" in md and "failed" in md
    assert f"[REPORT.md]({(tmp_path / 'REPORT.md').resolve()})" in md
    assert sum("━" in ln for ln in md.split("\n")) == 2


def test_build_wires_metrics_and_estimate(tmp_path, repo):
    (repo / "a.py").write_text("1\n")
    sha = _commit(repo, "c")
    (tmp_path / "METRICS.json").write_text(json.dumps(
        {"derived_stage_gaps": {"deliver": {"wall_minutes": 33.1}}}))
    rs = {**RUN, "commits": [{"repo": str(repo), "sha": sha}],
          "stages": [{"stage": "evaluate", "token_cost": 60000}, {"stage": "build", "token_cost": 40000}]}
    md = rds.build_delivery_summary(rs, {"evaluate": {"data": EVAL}, "deliver": DELIVER, "test": TESTS},
                                    tmp_path, db_path=tmp_path / "missing.db")
    assert "（DELIVER 最长，33 分钟）" in md and "💰 估算 100,000 tokens" in md
    assert f"[a.py]({(repo / 'a.py').resolve()})" in md and f"`{sha[:7]}`" in md


# ── prompt_builder env injection (§4-E1 run ↔ session link) ──────────────────

async def _options(session_context):
    from unittest.mock import MagicMock
    from core.prompt_builder import PromptBuilder

    cfg = MagicMock()
    values = {"thinking_mode": "adaptive", "thinking_effort": "high", "use_bedrock": True}
    cfg.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
    cfg.get_model = MagicMock(return_value="claude-opus-5-5")
    return await PromptBuilder(cfg).build_options(
        agent_config={"model": "claude-opus-5-5"}, enable_skills=False, enable_mcp=False,
        resume_session_id=None, session_context=session_context,
        channel_context=None, editor_context=None, terminal_context=None,
        cached_system_prompt="CACHED",
    )


@pytest.mark.asyncio
async def test_app_session_id_injected_into_cli_env():
    opts = await _options({"sdk_session_id": "app-sid-123"})
    assert opts.env["SWARM_APP_SESSION_ID"] == "app-sid-123"


@pytest.mark.asyncio
@pytest.mark.parametrize("ctx", [None, {}, {"sdk_session_id": None}])
async def test_no_app_session_id_no_env_key(ctx):
    opts = await _options(ctx)
    assert "SWARM_APP_SESSION_ID" not in (opts.env or {})
