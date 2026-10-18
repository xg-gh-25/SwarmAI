"""Pipeline delivery summary — tool-rendered chat block for a finished run (design §4-E).

Every number in the summary is read by code (data.db, git, the run's own artifacts);
the model only echoes the rendered block. Read-only everywhere: sqlite is opened with
``mode=ro``, git runs list-form with validated shas, nothing is written.

Public symbols:
- ``token_usage_for_window`` — raw token/cost sums of one app session's rows inside a
  UTC window (token_usage timestamps are LOCAL time; converted correctly). Legacy
  shape, used by artifact_cli (REPORT header, per-stage ``token_cost``).
- ``run_token_usage``        — honest run-level usage: a token_usage row is stamped at
  the END of a turn, so the window is extended to the turn that was in flight at
  ``completed_at`` and the result says what it covers (``complete``,
  ``excludes_current_turn``, ``includes_prerun``).
- ``capture_intent``         — the user message that started the run (skips injected
  meta blocks, explicit acks like "继续", and option picks like "选 A").
- ``file_changes``           — per-file A/M/D/R status, +/− lines and diff base ref
  from ``git show`` of the run's commits (fallback: files_touched + worktree state,
  each file resolved against its OWN git toplevel; unresolvable → plain text).
- ``review_summary``         — PURE: the three-part review content (what was solved,
  how, what is left) from DELIVER's ``outcome`` plus every recorded leftover. The
  chat block and the REPORT (TL;DR + §10) both render it, so they cannot disagree.
- ``render_review_lines``    — the one renderer of those parts for both doors.
- ``render_delivery_summary_md`` — PURE renderer of the §4-E chat block.
- ``build_delivery_summary`` — wires the above for a run dir; never raises.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:  # One neutralization rule shared with the REPORT provenance audit.
    from scripts.pipeline_validator import _md_cell
except ImportError:  # pragma: no cover - fallback mirrors pipeline_validator._md_cell
    def _md_cell(value) -> str:  # type: ignore[no-redef]
        raw = str(value if value is not None else "")
        if not raw.strip():
            return ""
        return (raw.replace("\\", "\\\\").replace("\n", " ⏎ ")
                .replace("\r", "").replace("|", "\\|"))

SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
RULE_LINE = "━" * 36
MAX_FILES = 15
MAX_REQS = 7
MAX_INTENT = 200
_GIT_TIMEOUT = 15
_LOCAL_FMT = "%Y-%m-%dT%H:%M:%S"

_META_PREFIXES = (
    "## Current UI State", "<task-notification", "[RESUMED CONVERSATION HISTORY",
    "## Recalled Knowledge", "## Task Continuation", "[Request interrupted",
)
_ACKS = {"继续", "continue", "go", "go on", "yes", "ok", "okay", "好", "好的", "y", "是",
         "对", "嗯", "行", "可以", "收到", "开始", "开始吧", "继续吧", "proceed", "next", "lgtm",
         # Resume-class control words: a run created right after one of these must
         # record the EARLIER request, not the control word itself.
         "resume", "resume please", "please continue", "continue please", "keep going",
         "go ahead", "carry on", "继续做", "接着做", "请继续", "接着来"}
# A clarification answer that only picks an option ("选 A", "B", "2. 第二个"): the run
# was started by an EARLIER message. The lookahead keeps "a bug in login…" a request.
_OPTION_PICK_RE = re.compile(
    r"^\s*(?:选择?)?\s*[A-Da-d1-4](?![A-Za-z0-9])(?!\s+[A-Za-z]{2,}).{0,20}$", re.S)
_MAX_INTENT_SCAN = 20000  # chars of message content read per row (LOW-3)


# ── time + db helpers ────────────────────────────────────────────────────────

def _parse_ts(value: Any, *, naive_is_local: bool) -> Optional[datetime]:
    """Parse an ISO timestamp into an AWARE datetime.

    run.json stamps are UTC (``…+00:00``); data.db stamps are naive LOCAL time.
    ``naive_is_local`` says how to read a stamp without an offset."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone() if naive_is_local else dt.replace(tzinfo=timezone.utc)
    return dt


def utc_iso_to_local_naive(value: str) -> Optional[str]:
    """``2026-10-17T09:02:48+00:00`` → local wall-clock ``%Y-%m-%dT%H:%M:%S``
    (the format ``record_token_usage`` writes)."""
    dt = _parse_ts(value, naive_is_local=False)
    return dt.astimezone().strftime(_LOCAL_FMT) if dt else None


def _default_db_path() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "data.db"


def _connect_ro(db_path: Optional[Path]) -> Optional[sqlite3.Connection]:
    path = Path(db_path) if db_path else _default_db_path()
    if not path.is_file():
        return None
    # as_uri() percent-encodes '?'/'#'/spaces so the path cannot alter the URI query.
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


# ── E1: token usage ──────────────────────────────────────────────────────────

def token_usage_for_window(session_id, start_iso, end_iso, db_path=None) -> Optional[dict]:
    """Sum ``token_usage`` rows of *session_id* inside ``[start_iso, end_iso]``.

    The window is UTC ISO (run.json); rows are naive LOCAL time (a ``Z`` row from
    the DDL default is read as UTC). Comparison is done on aware datetimes, so DST
    and mixed stamp formats are handled. ``end_iso`` falsy → now.

    Returns ``{"cost_usd","input_tokens","output_tokens","cache_read_tokens",
    "cache_creation_tokens","rows"}`` or None (no session id / db / table / rows)."""
    if not session_id:
        return None
    start = _parse_ts(start_iso, naive_is_local=False)
    end = _parse_ts(end_iso, naive_is_local=False) or datetime.now(timezone.utc)
    if start is None:
        return None
    rows = _session_usage_rows(session_id, db_path)
    if not rows:
        return None
    return _sum_usage([r for r in rows if start <= r[0] <= end])


def _session_usage_rows(session_id, db_path) -> Optional[list[tuple]]:
    """All token_usage rows of a session as ``(aware_dt, inp, out, cread, ccreate,
    cost)`` sorted by time; None when the db/table is unavailable."""
    try:
        conn = _connect_ro(db_path)
        if conn is None:
            return None
        try:
            raw = conn.execute(
                "SELECT timestamp, input_tokens, output_tokens, cache_read_tokens, "
                "cache_create_tokens, cost_usd FROM token_usage WHERE session_id = ?",
                (str(session_id),),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:  # missing table / locked / corrupt → no data
        logger.debug("token_usage rows: %s", exc)
        return None
    rows = []
    for ts, *vals in raw:
        dt = _parse_ts(ts, naive_is_local=True)
        if dt is not None:
            rows.append((dt, *vals))
    rows.sort(key=lambda r: r[0])
    return rows


def _sum_usage(rows: list[tuple]) -> Optional[dict]:
    out = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
           "cache_read_tokens": 0, "cache_creation_tokens": 0, "rows": 0}
    for _dt, inp, outp, cread, ccreate, cost in rows:
        out["input_tokens"] += int(inp or 0)
        out["output_tokens"] += int(outp or 0)
        out["cache_read_tokens"] += int(cread or 0)
        out["cache_creation_tokens"] += int(ccreate or 0)
        out["cost_usd"] += float(cost or 0.0)
        out["rows"] += 1
    if not out["rows"]:
        return None
    out["cost_usd"] = round(out["cost_usd"], 4)
    return out


def _first_user_message_after(session_id, after: datetime, db_path) -> Optional[datetime]:
    """Earliest delivered user message of the session strictly after *after*
    (a turn boundary). None when unknown (no table / no such message)."""
    try:
        conn = _connect_ro(db_path)
        if conn is None:
            return None
        try:
            if "created_at" not in _columns(conn, "messages"):
                return None
            sent = " AND (sent IS NULL OR sent != 0)" if "sent" in _columns(conn, "messages") else ""
            raw = conn.execute(
                f"SELECT created_at FROM messages WHERE session_id = ? AND role = 'user'{sent}",
                (str(session_id),),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    later = [dt for (c,) in raw if (dt := _parse_ts(c, naive_is_local=True)) and dt > after]
    return min(later) if later else None


def run_token_usage(session_id, start_iso, end_iso, db_path=None,
                    turn_end_iso=None) -> Optional[dict]:
    """Measured usage of a run, labelled with what it actually covers.

    ``token_usage`` rows are written when a TURN ends. Hence:
    - the first row at/after ``created_at`` is the turn that called ``run-create``,
      which began before the run existed → ``includes_prerun`` (its pre-run work is
      inside; rows cannot be split);
    - the turn in flight at ``completed_at`` is stamped AFTER it. The end bound is
      extended to *turn_end_iso* (caller-provided) or else to the first row after
      ``completed_at`` — unless a new user message arrived before that row (then the
      row belongs to a later turn and is not taken).
    ``excludes_current_turn`` = no such closing row yet (e.g. rendered from inside
    the completing turn); ``complete`` is its negation. ``end_iso`` falsy → now.

    Returns the ``token_usage_for_window`` keys plus ``tokens`` (input + output +
    cache creation, cache reads excluded), ``turns``, ``complete``,
    ``excludes_current_turn``, ``includes_prerun``; None when nothing is measurable."""
    if not session_id:
        return None
    start = _parse_ts(start_iso, naive_is_local=False)
    end = _parse_ts(end_iso, naive_is_local=False) or datetime.now(timezone.utc)
    if start is None:
        return None
    rows = _session_usage_rows(session_id, db_path)
    if not rows:
        return None
    taken = [r for r in rows if start <= r[0] <= end]
    after = [r for r in rows if r[0] > end]
    closing: list[tuple] = []
    turn_end = _parse_ts(turn_end_iso, naive_is_local=False)
    if turn_end is not None:
        closing = [r for r in after if r[0] <= turn_end][:1]
    elif after:
        boundary = _first_user_message_after(session_id, end, db_path)
        if boundary is None or after[0][0] <= boundary:
            closing = after[:1]
    usage = _sum_usage(taken + closing)
    if usage is None:
        return None
    usage.update({
        "tokens": usage["input_tokens"] + usage["output_tokens"] + usage["cache_creation_tokens"],
        "turns": usage["rows"],
        "complete": bool(closing),
        "excludes_current_turn": not closing,
        "includes_prerun": True,  # the first counted turn always spans created_at
    })
    return usage


# ── E1: initial intent ───────────────────────────────────────────────────────

def _message_text(content: Any) -> str:
    """Substantive text of a stored message: JSON block list → text blocks that are
    not injected meta (UI state, task notifications, resume history…)."""
    blocks: Any = content
    if isinstance(content, str):
        try:
            blocks = json.loads(content)
        except (ValueError, TypeError):
            blocks = content
    if isinstance(blocks, str):
        texts = [blocks]
    elif isinstance(blocks, list):
        texts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text"
                 and isinstance(b.get("text"), str)]
    elif isinstance(blocks, dict) and isinstance(blocks.get("text"), str):
        texts = [blocks["text"]]
    else:
        texts = []
    kept = [t.strip() for t in texts
            if t.strip() and not t.lstrip().startswith(_META_PREFIXES)]
    return "\n".join(kept).strip()


def _is_ack(text: str) -> bool:
    """Not a request: an explicit ack ("继续", "ok"), ≤2 chars, or an option pick
    answering a clarification ("选 A"). Short real requests ("修复登录bug") pass."""
    t = text.strip()
    if len(t) <= 2 or t.lower().rstrip("。.!！~～ ,，") in _ACKS:
        return True
    return bool(_OPTION_PICK_RE.match(t))


_TEXT_FIELD_RE = re.compile(r'"text"\s*:\s*("(?:[^"\\]|\\.)*")')


def _message_text_bounded(content: Any) -> str:
    """``_message_text`` for content read with a length cap: a cut JSON block list
    no longer parses, so fall back to the complete ``"text": "…"`` fields in it
    (never the raw JSON)."""
    if isinstance(content, str) and re.match(r"\s*(?:\[\s*\{|\{\s*\")", content):
        try:
            json.loads(content)
        except (ValueError, TypeError):
            texts = []
            for m in _TEXT_FIELD_RE.finditer(content):
                try:
                    texts.append({"type": "text", "text": json.loads(m.group(1))})
                except ValueError:
                    continue
            return _message_text(texts)
    return _message_text(content)


def capture_intent(session_id, before_iso, db_path=None) -> Optional[tuple[str, str]]:
    """Latest substantive ``role='user'`` message of *session_id* strictly before
    *before_iso* (UTC ISO; message stamps are naive local), skipping acks and option
    picks so the message that STARTED the pipeline wins over a later "选 A". Unsent
    pending rows (``sent=0``) are excluded; only the first 20,000 chars of each row
    are read. Returns ``(text[:2000], "user_message")`` or None."""
    if not session_id:
        return None
    before = _parse_ts(before_iso, naive_is_local=False)
    if before is None:
        return None
    try:
        conn = _connect_ro(db_path)
        if conn is None:
            return None
        try:
            sent_clause = " AND (sent IS NULL OR sent != 0)" if "sent" in _columns(conn, "messages") else ""
            rows = conn.execute(
                "SELECT substr(content, 1, ?), created_at FROM messages WHERE session_id = ? "
                f"AND role = 'user'{sent_clause} ORDER BY created_at DESC LIMIT 500",
                (_MAX_INTENT_SCAN, str(session_id)),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("capture_intent: %s", exc)
        return None
    candidates = []
    for content, created in rows:
        dt = _parse_ts(created, naive_is_local=True)
        if dt is not None and dt < before:
            candidates.append((dt, content))
    candidates.sort(key=lambda x: x[0], reverse=True)
    for _dt, content in candidates:
        text = _message_text_bounded(content)
        if text and not _is_ack(text):
            return text[:2000], "user_message"
    return None


# ── E1: file changes ─────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> Optional[str]:
    """Run ``git -C <repo> <args>`` list-form, read-only. None on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True,
            timeout=_GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _parse_name_status_z(out: str) -> list[tuple[str, str, Optional[str]]]:
    """``-z`` name-status → [(status_letter, path, old_path)]."""
    parts = out.split("\0")
    res, i = [], 0
    while i < len(parts):
        code = parts[i].strip()
        if not code:
            i += 1
            continue
        letter = code[0].upper()
        if letter in ("R", "C") and i + 2 < len(parts):
            res.append(("R" if letter == "R" else "A", parts[i + 2], parts[i + 1]))
            i += 3
        elif i + 1 < len(parts):
            res.append((letter if letter in "AMD" else "M", parts[i + 1], None))
            i += 2
        else:
            break
    return res


def _parse_numstat_z(out: str) -> dict[str, tuple[Optional[int], Optional[int]]]:
    """``-z`` numstat → {new_path: (added, removed)}; binary files → (None, None)."""
    parts = out.split("\0")
    res: dict[str, tuple[Optional[int], Optional[int]]] = {}
    i = 0
    while i < len(parts):
        head = parts[i].lstrip("\n")
        if not head.strip():
            i += 1
            continue
        fields = head.split("\t")
        if len(fields) < 3:
            i += 1
            continue
        a = int(fields[0]) if fields[0].isdigit() else None
        d = int(fields[1]) if fields[1].isdigit() else None
        if fields[2] == "" and i + 2 < len(parts):  # rename: path empty, then old\0new
            res[parts[i + 2]] = (a, d)
            i += 3
        else:
            res[fields[2]] = (a, d)
            i += 1
    return res


_SKIP_PREFIXES = (".aidlc/deliverables/",)  # the run's own REPORT copy — linked separately


def _commit_changes(commits: list, repo_root: Optional[Path]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for commit in commits or []:
        if not isinstance(commit, dict):
            continue
        sha = str(commit.get("sha") or "")
        if not SHA_RE.match(sha):
            continue
        repo_s = str(commit.get("repo") or repo_root or "").strip()
        if not repo_s:  # Path("") is "." → never fall back to the cwd's repo
            continue
        repo = Path(repo_s)
        if not repo.is_dir():
            continue
        ns = _git(repo, "show", "-M", "-z", "--name-status", "--format=", sha)
        nm = _git(repo, "show", "-M", "-z", "--numstat", "--format=", sha)
        if ns is None:
            continue
        lines = _parse_numstat_z(nm or "")
        for status, path, old in _parse_name_status_z(ns):
            if path.startswith(_SKIP_PREFIXES):
                continue
            a, d = lines.get(path, (None, None))
            abs_path = str((repo / path).resolve())  # key: same rel path in 2 repos ≠ 1 file
            cur = merged.get(abs_path)
            if cur is None:
                merged[abs_path] = {
                    "path": path, "abs_path": abs_path,
                    "status": status, "added": a, "removed": d,
                    "base_ref": None if status == "A" else f"{sha}^",
                    **({"old_path": old} if old else {}),
                }
                continue
            if status == "A" or cur["status"] == "A":
                cur["status"], cur["base_ref"] = "A", None  # added in this run: no base
            else:
                cur["status"] = status
            for key, val in (("added", a), ("removed", d)):
                if val is not None:
                    cur[key] = (cur[key] or 0) + val
    return merged


def _count_lines(path: Path) -> Optional[int]:
    try:
        if path.stat().st_size > 5_000_000:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None  # binary
    return data.count(b"\n") + (0 if not data or data.endswith(b"\n") else 1)


def _toplevel(path: Path, cache: dict) -> Optional[Path]:
    """Git toplevel containing *path* (nearest existing ancestor dir), cached per dir.
    None when the file is not inside any repository."""
    probe = path if path.is_dir() else path.parent
    while not probe.is_dir() and probe != probe.parent:
        probe = probe.parent
    key = str(probe)
    if key not in cache:
        top = _git(probe, "rev-parse", "--show-toplevel")
        cache[key] = Path(top.strip()).resolve() if top and top.strip() else None
    return cache[key]


def _repo_state(root: Path, cache: dict) -> tuple[dict, dict]:
    """``(status_map, numstat)`` of one repo's worktree vs HEAD, cached per root."""
    key = str(root)
    if key in cache:
        return cache[key]
    status_map: dict[str, str] = {}
    porcelain = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    if porcelain:
        parts = porcelain.split("\0")
        i = 0
        while i < len(parts):
            entry = parts[i]
            if len(entry) < 4:
                i += 1
                continue
            xy, p = entry[:2], entry[3:]
            if "?" in xy or "A" in xy:
                status_map[p] = "A"
            elif "D" in xy:
                status_map[p] = "D"
            elif "R" in xy:
                status_map[p] = "R"
                i += 1  # skip the old path that follows a rename
            else:
                status_map[p] = "M"
            i += 1
    numstat = _parse_numstat_z(_git(root, "diff", "-z", "--numstat", "HEAD") or "")
    cache[key] = (status_map, numstat)
    return cache[key]


def _worktree_changes(files_touched: list, repo_root: Optional[Path]) -> dict[str, dict]:
    """No commits (gitignored project / uncommitted run): files_touched + worktree.

    Each file is resolved against its OWN repository, never a guessed one:
    - absolute path → ``git rev-parse --show-toplevel`` of its directory; the label
      is relative to that root (a file outside every repo keeps its absolute path);
    - relative path → *repo_root* only, and only when the file exists there or git
      reports it there; otherwise (no root / not found) it is listed as plain text
      (``abs_path`` empty → the renderer emits no link)."""
    paths = [str(p) for p in (files_touched or []) if isinstance(p, str) and p.strip()]
    if not paths:
        return {}
    given = Path(repo_root).resolve() if repo_root else None
    tops: dict = {}
    states: dict = {}
    out: dict[str, dict] = {}
    for raw in paths:
        p = Path(raw)
        if p.is_absolute():
            abs_p = p.resolve()
            root = _toplevel(abs_p, tops)
        else:
            root = given
            abs_p = (given / p).resolve() if given else None
        if root is not None and abs_p is not None:
            try:
                rel = abs_p.relative_to(root).as_posix()
            except ValueError:
                rel, root = abs_p.as_posix(), None
        else:
            rel = abs_p.as_posix() if abs_p is not None else p.as_posix()
        if rel.startswith(_SKIP_PREFIXES):
            continue
        status_map, numstat = _repo_state(root, states) if root is not None else ({}, {})
        if not p.is_absolute() and (root is None or not (abs_p.exists() or rel in status_map)):
            key = f"plain:{rel}"  # unresolvable relative path: no link, no guessing
            if key not in out:
                out[key] = {"path": rel, "abs_path": "", "status": "M",
                            "added": None, "removed": None, "base_ref": None}
            continue
        key = str(abs_p)
        if key in out:
            continue
        status = status_map.get(rel) or ("M" if abs_p.exists() else "D")
        a, d = numstat.get(rel, (None, None))
        if status == "A" and a is None:
            a, d = _count_lines(abs_p), 0
        out[key] = {"path": rel, "abs_path": key, "status": status,
                    "added": a, "removed": d, "base_ref": None}
    return out


def file_changes(commits, files_touched, repo_root=None) -> list[dict]:
    """Per-file changes of a run: ``{path, abs_path, status A|M|D|R, added, removed,
    base_ref}`` (base_ref ``<sha>^`` for committed M/D/R, None for A and for the
    uncommitted fallback). Multiple commits merge per path: first base_ref, summed
    lines, A wins. No usable commit → files_touched + ``git status``/``git diff``,
    each file against its own toplevel. ``repo_root=None`` is valid: commits use
    their own ``repo``; relative files_touched with no root become plain text."""
    root = Path(repo_root) if repo_root else None
    merged = _commit_changes(commits if isinstance(commits, list) else [], root)
    if not merged:
        merged = _worktree_changes(files_touched if isinstance(files_touched, list) else [], root)
    return list(merged.values())


# ── E2: renderer ─────────────────────────────────────────────────────────────

_ICON = {"A": "🆕", "M": "✏️", "D": "🗑️", "R": "↪️"}
_OPEN_STATUSES = ("assumed", "needs-clarification")


_INLINE_MD_RE = re.compile(r"([\[\]()!<>*_`])")


def _txt(value: Any, limit: Optional[int] = None) -> str:
    """Neutralize user/model text for one markdown line: truncate RAW text first
    (so an escape is never cut in half), then ``_md_cell`` (``\\`` and ``|``
    escaped, newline → ⏎), then ``━`` → ``─`` (the block owns its two rules), then
    backslash-escape every inline construct char ``[ ] ( ) ! < > * _ ` `` so no
    link, image, autolink/HTML, emphasis or code span can be forged, and escape a
    leading ``#``/``-``/``+``/``=`` so no heading or list can start."""
    raw = str(value if value is not None else "").strip()
    cut = False
    if limit is not None and len(raw) > limit:
        raw, cut = raw[:limit].rstrip(), True
    s = _md_cell(raw).replace("━", "─").strip()
    s = _INLINE_MD_RE.sub(r"\\\1", s)
    if s[:1] in ("#", "-", "+", "="):
        s = "\\" + s
    return s + ("…" if cut and s else "")


def _code(value: Any, limit: int) -> str:
    """Text for INSIDE a backtick code span (escapes are literal there): only drop
    backticks, newlines and ``━`` — nothing inside a span can form markup."""
    raw = str(value if value is not None else "").strip()[:limit]
    return re.sub(r"[`\r\n]", "", raw).replace("━", "─").replace("|", "/")


def _human(n: Any) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "?"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            q = v / div
            return f"{q:.1f}{suf}" if q < 10 else f"{q:.0f}{suf}"
    return f"{v:.0f}"


def _link(text: str, target: str) -> str:
    """Markdown link ``[text](target)``. Unsafe targets degrade to plain text."""
    label = _txt(text)  # already escapes [ ] ( ) ! < > * _ `
    if not target or any(c in target for c in "\n\r<>|`"):
        return label
    if any(c in target for c in " ()"):
        target = f"<{target}>"
    return f"[{label}]({target})"


def _lines_cell(fc: dict) -> str:
    a, d = fc.get("added"), fc.get("removed")
    if a is None and d is None:
        return "—"
    parts = []
    if a:
        parts.append(f"+{a}")
    if d:
        parts.append(f"−{d}")
    return " ".join(parts) or "±0"


def _sort_key(fc: dict):
    churn = (fc.get("added") or 0) + (fc.get("removed") or 0)
    return (0 if fc.get("status") == "A" else 1, -churn, str(fc.get("path") or ""))


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x: Any) -> list:
    return x if isinstance(x, list) else []


def _duration_minutes(run_state: dict) -> Optional[float]:
    start = _parse_ts(run_state.get("created_at"), naive_is_local=False)
    end = _parse_ts(run_state.get("completed_at") or run_state.get("updated_at"),
                    naive_is_local=False)
    if not start or not end or end < start:
        return None
    return (end - start).total_seconds() / 60.0


def _slowest_stage(stage_gaps: Any) -> Optional[tuple[str, float]]:
    best = None
    for stage, info in _d(stage_gaps).items():
        mins = _d(info).get("wall_minutes")
        if isinstance(mins, (int, float)) and (best is None or mins > best[1]):
            best = (str(stage), float(mins))
    return best


def _header(run_state: dict, usage: Optional[dict], stage_gaps, estimate_tokens) -> str:
    status = str(run_state.get("status") or "unknown")
    icon, label = {"completed": ("✅", "Pipeline 完成"), "failed": ("❌", "Pipeline 失败"),
                   "cancelled": ("⛔", "Pipeline 已取消")}.get(status, ("⏸️", "Pipeline"))
    if icon == "⏸️":
        label = f"Pipeline {_txt(status, 30)}"
    run_id = _code(run_state.get("id") or "run_?", 60)
    parts = [f"**{icon} {label}**", f"`{run_id}`"]
    if run_state.get("profile"):
        parts.append(_txt(run_state.get("profile"), 30))
    mins = _duration_minutes(run_state)
    if mins is not None:
        dur = f"⏱ {mins:.0f} 分钟"
        slow = _slowest_stage(stage_gaps)
        if slow:
            dur += f"（{_txt(slow[0].upper(), 20)} 最长，{slow[1]:.0f} 分钟）"
        parts.append(dur)
    if usage:
        parts.append(_usage_label(usage))
    elif estimate_tokens:
        parts.append(f"💰 估算 {int(estimate_tokens):,} tokens")
    return " · ".join(parts)


def _usage_label(usage: dict) -> str:
    """Measured-usage label that says what it covers (``run_token_usage`` flags).
    A legacy dict without the flags renders as before (treated as complete)."""
    cost = f"${float(usage.get('cost_usd') or 0):.2f}"
    detail = (f"输出 {_human(usage.get('output_tokens'))} tokens · "
              f"缓存读 {_human(usage.get('cache_read_tokens'))}")
    notes = []
    if usage.get("excludes_current_turn") is True:
        notes.append("不含当前一轮")
    if usage.get("includes_prerun") is True:
        notes.append("含建 run 前的同一轮")
    tail = ("；" + "，".join(notes)) if notes else ""
    if usage.get("excludes_current_turn") is True:
        tokens = usage.get("tokens")
        if tokens is None:
            tokens = sum(int(usage.get(k) or 0) for k in
                         ("input_tokens", "output_tokens", "cache_creation_tokens"))
        return f"💰 已结算 {_human(tokens)} tokens · {cost}（{detail}{tail}）"
    return f"💰 {cost}（{detail}{tail}）"


def _requirements_block(eval_artifact: dict, deliver_artifact: dict) -> list[str]:
    reqs = [r for r in _l(eval_artifact.get("requirements")) if isinstance(r, dict)]
    audit = _d(deliver_artifact.get("completion_audit"))
    met, total = audit.get("requirements_met"), audit.get("requirements_total")
    items: list[str] = []
    if reqs:
        for r in reqs:
            st = str(r.get("status") or "").strip()
            icon = "⚠️" if st in _OPEN_STATUSES else "✅"
            rid = _txt(r.get("id") or "", 20)
            items.append(f"- {icon} {rid} {_txt(r.get('statement') or '', 160)}".rstrip())
    else:  # legacy run: free-text acceptance criteria
        icon = "✅" if audit.get("all_green") is True else "▫️"
        for ac in _l(eval_artifact.get("acceptance_criteria")):
            text = ac.get("statement") or ac.get("text") if isinstance(ac, dict) else ac
            if text:
                items.append(f"- {icon} {_txt(text, 160)}")
    if not items:
        return []
    n = total if isinstance(total, int) and total > 0 else len(items)
    head = f"**最终需求**（{n} 条，{met} 条达成）" if isinstance(met, int) else f"**最终需求**（{n} 条）"
    out = [head] + items[:MAX_REQS]
    if len(items) > MAX_REQS:
        out.append(f"- …另 {len(items) - MAX_REQS} 条见报告")
    return out


def _clar_answer(c: dict) -> str:
    """Self-answered clarification → readable answer; a bare option letter
    ("B") is expanded to that option's text."""
    ans = str(c.get("answer") or "").strip()
    if len(ans) <= 3:
        letter = ans.rstrip(".").upper()
        for opt in _l(c.get("options")):
            if isinstance(opt, str) and letter and opt.strip().upper().startswith(letter + "."):
                return opt.strip()[len(letter) + 1:].strip() or ans
    return re.sub(r"^[A-Z]\.\s+", "", ans) or ans  # "B. 中文…" → "中文…"


def _ai_decisions_line(eval_artifact: dict) -> Optional[str]:
    """Same triggers as pipeline_validator.render_audit_trail_md leftovers."""
    items: list[str] = []
    for r in _l(eval_artifact.get("requirements")):
        if not isinstance(r, dict):
            continue
        kind = str(_d(r.get("source")).get("kind") or "").strip()
        if kind == "assumption" or str(r.get("status") or "").strip() in _OPEN_STATUSES:
            items.append(f"{_txt(r.get('id') or '?', 20)} {_txt(r.get('statement') or '', 60)}")
    for c in _l(eval_artifact.get("clarifications")):
        if isinstance(c, dict) and str(c.get("answered_by") or "").strip() == "self":
            items.append(f"{_txt(c.get('qid') or '?', 20)} {_txt(_clar_answer(c), 60)}")
    if not items:
        return None
    shown = " · ".join(items[:MAX_REQS])
    more = f" · …另 {len(items) - MAX_REQS} 处见报告" if len(items) > MAX_REQS else ""
    return f"⚠️ **AI 替你拍板 {len(items)} 处**：{shown}{more}"


def _files_block(run_state: dict, changes: list) -> list[str]:
    rows = sorted([c for c in _l(changes) if isinstance(c, dict) and c.get("path")], key=_sort_key)
    if not rows:
        return []
    plus = sum(c.get("added") or 0 for c in rows)
    minus = sum(c.get("removed") or 0 for c in rows)
    shas = [str(c.get("sha"))[:7] for c in _l(run_state.get("commits"))
            if isinstance(c, dict) and SHA_RE.match(str(c.get("sha") or ""))]
    commit_txt = ("commit " + ", ".join(f"`{s}`" for s in shas[:5])
                  + (f" 等 {len(shas)} 个" if len(shas) > 5 else "")) if shas else "未提交"
    out = [f"**改动** · {len(rows)} 个文件 · +{plus} / −{minus} · {commit_txt}", "",
           "| | 文件 | 行数 |", "|---|---|---|"]
    for c in rows[:MAX_FILES]:
        status = str(c.get("status") or "M")
        target = str(c.get("abs_path") or "")
        base = str(c.get("base_ref") or "")
        if status != "A" and target and base.endswith("^") and SHA_RE.match(base[:-1]):
            target += f"#base={base}"
        out.append(f"| {_ICON.get(status, '✏️')} | {_link(str(c['path']), target)} | {_lines_cell(c)} |")
    if len(rows) > MAX_FILES:
        out += ["", f"另 {len(rows) - MAX_FILES} 个文件见报告 §8"]
    return out


def _exit_criteria(run_state: dict) -> Optional[tuple[int, int]]:
    """Latest stage record carrying ``exit_criteria`` (design §4-G1) → (met, total)."""
    for rec in reversed(_l(run_state.get("stages"))):
        crit = [c for c in _l(_d(rec).get("exit_criteria")) if isinstance(c, dict)]
        if crit:
            met = sum(1 for c in crit if c.get("met") is True
                      or str(c.get("status") or "").lower() in ("met", "pass", "passed", "satisfied"))
            return met, len(crit)
    return None


def _quality_line(run_state: dict, deliver: dict, test_info: dict) -> Optional[str]:
    parts: list[str] = []
    total, failed, passed = test_info.get("tests_total"), test_info.get("failed"), test_info.get("passed")
    if isinstance(total, int) and total > 0:
        if isinstance(passed, int) and not isinstance(passed, bool):
            ok = passed
        else:
            ok = total - (failed if isinstance(failed, int) else 0)
        parts.append(f"测试 {ok}/{total} 通过")
    adv = _d(deliver.get("adversarial_review"))
    ft, ff = adv.get("findings_total"), adv.get("findings_fixed")
    if isinstance(ft, int):
        if ft == 0:
            parts.append("Gate-2 无发现")
        elif isinstance(ff, int) and ff >= ft:
            parts.append(f"Gate-2 {ft} 条发现全部修复")
        else:
            parts.append(f"Gate-2 {ft} 条发现，修复 {ff if isinstance(ff, int) else '?'} 条")
    ec = _exit_criteria(run_state)
    if ec:
        parts.append(f"结束条件 {ec[0]}/{ec[1]} 满足")
    pr = _push_ready(run_state, deliver)
    if pr is True:
        parts.append("push-ready")
    elif pr is False:
        parts.append("未 push-ready")
    if any(_d(c).get("forced_push_ready") is True for c in _l(run_state.get("commits"))):
        parts.append("强制提交")
    return ("**质量** " + " · ".join(parts)) if parts else None


MAX_APPROACH = 4
MAX_LEFTOVERS = 6
UNRECORDED = "未记录"
NO_LEFTOVERS = "无（DELIVER 未记录任何遗留）"


def _raw(value: Any) -> str:
    """Raw text with surrounding whitespace removed; emptiness is judged HERE, on
    the raw value, never on ``_txt`` output (which may turn blanks into ``⏎``)."""
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _raw_list(value: Any) -> list[str]:
    return [t for t in (_raw(v) for v in _l(value)) if t]


_TEXT_KEYS = ("finding", "summary", "description", "title", "issue")
_DONE_PREFIXES = ("fixed", "resolved", "accepted", "closed", "done")


def _item_text(item: Any) -> str:
    """Text of a leftover item: a string, or a dict's first non-blank text field.
    Dict items are the gates' own output shape ({severity, description, ...}) —
    dropping them is how a run with recorded risks rendered as clean."""
    if isinstance(item, dict):
        return next((t for t in (_raw(item.get(k)) for k in _TEXT_KEYS) if t), "")
    return _raw(item)


def _item_done(item: Any) -> bool:
    """A dict item marked done: truthy ``resolved`` (every other reader's rule), or a
    status / resolution that starts with fixed / resolved / accepted / closed / done."""
    if not isinstance(item, dict):
        return False
    if item.get("resolved"):
        return True
    return any(_raw(item.get(k)).lower().startswith(_DONE_PREFIXES) for k in ("status", "resolution"))


def _deliver_record(run_state: dict) -> dict:
    for rec in _l(run_state.get("stages")):
        if _d(rec).get("stage") == "deliver":
            return _d(rec)
    return {}


def _push_ready(run_state: dict, deliver: dict) -> Optional[bool]:
    """The push-ready verdict, one source for every line of the block: the deliver
    STAGE RECORD (written by the gate, read by run-commit) wins over the artifact."""
    rec = _deliver_record(_d(run_state)).get("push_ready")
    if isinstance(rec, bool):
        return rec
    art = _d(deliver).get("push_ready")
    return art if isinstance(art, bool) else None


def review_summary(run_state: Any, deliver: Any) -> dict:
    """PURE: ``{"solved": (before, now) | None, "approach": [str], "leftovers": [str]}``.

    Sources: ``deliver.outcome`` {problem_before, result_now, approach[]}; leftovers
    in priority order: unresolved adversarial findings, push_ready=false and its
    reason (deliver stage record, else the artifact), commits forced past the
    push_ready gate, ``attention_flags[]``, ``meta_review.risks[]``. Strings are RAW
    (each caller escapes for its own medium); blanks are dropped. ``solved`` is
    None unless BOTH halves are recorded — half a before/after is not a summary."""
    rs, dv = _d(run_state), _d(deliver)
    out = _d(dv.get("outcome"))
    before, now = _raw(out.get("problem_before")), _raw(out.get("result_now"))
    left: list[str] = []
    for f in _l(_d(dv.get("adversarial_review")).get("findings")):
        text = _item_text(f) if isinstance(f, dict) else ""
        if text and not _item_done(f):
            sev = _raw(f.get("severity")) or "?"
            left.append(f"未修复的 {sev} 发现：{text}")
    rec = _deliver_record(rs)
    if _push_ready(rs, dv) is False:
        why = _raw(rec.get("push_ready_reason")) or _raw(dv.get("push_ready_reason"))
        left.append(f"未通过 push-ready：{why or '原因未记录'}")
    for c in _l(rs.get("commits")):
        c = _d(c)
        if c.get("forced_push_ready") is True:
            left.append(f"commit {_raw(c.get('sha'))[:8] or '?'} 是用 run-commit --force "
                        "绕过 push-ready 检查强制提交的")
    for flag in _l(dv.get("attention_flags")):
        text = _item_text(flag)
        if text and not _item_done(flag):
            left.append(text)
    for r in _l(_d(dv.get("meta_review")).get("risks")):
        text = _item_text(r)
        if text and not _item_done(r):
            sev = _raw(r.get("severity")) if isinstance(r, dict) else ""
            left.append(f"{sev + ' ' if sev else ''}风险：{text}")
    return {"solved": (before, now) if before and now else None,
            "approach": _raw_list(out.get("approach")), "leftovers": left}


def render_review_lines(parts: dict, esc=None, caps: bool = True,
                        leftovers_inline: bool = True) -> list[str]:
    """Render the three parts. The ONE renderer for both doors: the chat block uses
    the defaults (``_txt`` escaping, capped lists, leftovers listed); the REPORT
    TL;DR passes its own one-line escaper, ``caps=False`` and
    ``leftovers_inline=False`` (§10 lists them in full)."""
    esc = esc or (lambda v, limit=None: _txt(v, limit))
    solved, approach, left = parts.get("solved"), parts.get("approach") or [], parts.get("leftovers") or []
    out = ["**解决了什么问题**"]
    out += ([f"- 原来：{esc(solved[0], 300)}", f"- 现在：{esc(solved[1], 300)}"]
            if solved else [f"- {UNRECORDED}"])
    shown = approach[:MAX_APPROACH] if caps else approach
    out += ["", "**怎么做的**"]
    out += [f"- {esc(a, 200)}" for a in shown] or [f"- {UNRECORDED}"]
    if len(approach) > len(shown):
        out.append(f"- …另 {len(approach) - len(shown)} 条见报告")
    if not left:
        return out + ["", "**遗留问题**", f"- {NO_LEFTOVERS}"]
    if not leftovers_inline:
        return out + ["", f"**遗留问题**（{len(left)} 条，见 §10）"]
    shown_l = left[:MAX_LEFTOVERS] if caps else left
    out += ["", f"**遗留问题**（{len(left)} 条）"] + [f"- {esc(x, 240)}" for x in shown_l]
    if len(left) > len(shown_l):
        out.append(f"- …另 {len(left) - len(shown_l)} 条见报告 §10")
    return out


def _report_line(report_path: Any) -> str:
    return f"📄 **{_link('REPORT.md', str(report_path or ''))}**：完整报告，点击在 Canvas 打开"


def render_delivery_summary_md(run_state, eval_artifact, deliver_artifact, test_info, usage,
                               file_changes, report_path, stage_gaps=None,
                               estimate_tokens=None) -> str:
    """PURE: render the §4-E chat block. Exactly two ``━`` lines (first + last), no
    code fences; every user/model string goes through ``_txt`` (``_md_cell``)."""
    run_state, ev = _d(run_state), _d(eval_artifact)
    deliver, tests = _d(deliver_artifact), _d(test_info)
    lines = [RULE_LINE, _header(run_state, usage if isinstance(usage, dict) else None,
                                stage_gaps, estimate_tokens), ""]
    intent = run_state.get("intent") or run_state.get("requirement")
    if intent:
        lines += ["**你要的**", f"> {_txt(intent, MAX_INTENT)}", ""]
    lines += render_review_lines(review_summary(run_state, deliver)) + [""]
    req = _requirements_block(ev, deliver)
    if req:
        lines += req + [""]
    ai = _ai_decisions_line(ev)
    if ai:
        lines += [ai, ""]
    files = _files_block(run_state, file_changes)
    if files:
        lines += files + [""]
    q = _quality_line(run_state, deliver, tests)
    if q:
        lines += [q, ""]
    lines += [_report_line(report_path), RULE_LINE]
    return "\n".join(lines)


# ── wiring ───────────────────────────────────────────────────────────────────

def _unwrap(artifact: Any) -> dict:
    """Artifacts may be stored bare or as ``{"data": {...}}`` envelopes."""
    a = _d(artifact)
    return a["data"] if isinstance(a.get("data"), dict) else a


def _stage_gaps(run_dir: Path) -> Optional[dict]:
    try:
        metrics = json.loads((run_dir / "METRICS.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    gaps = _d(metrics).get("derived_stage_gaps")
    return gaps if isinstance(gaps, dict) else None


def _estimate_tokens(run_state: dict) -> Optional[int]:
    total = sum(int(s.get("token_cost") or 0) for s in _l(run_state.get("stages"))
                if isinstance(s, dict) and isinstance(s.get("token_cost"), (int, float)))
    return total or None


def _minimal_summary(run_state: Any, report_path: Any) -> str:
    rs = _d(run_state)
    try:
        status = _txt(rs.get("status") or "unknown", 30)
        rid = _code(rs.get("id") or "run_?", 60)
    except Exception as exc:  # noqa: BLE001 — the fallback block must always render
        logger.warning("delivery summary: minimal header failed: %s", exc)
        status, rid = "unknown", "run_?"
    return "\n".join([RULE_LINE, f"**Pipeline {status}** · `{rid}`", "",
                      _report_line(report_path), RULE_LINE])


def build_delivery_summary(run_state, artifacts: dict, run_dir: Path, repo_root: Optional[Path] = None,
                           db_path=None, turn_end_iso=None) -> str:
    """Wire E1 data sources into the renderer for one run. Never raises: any internal
    error yields a minimal block (run id, status, REPORT link).

    ``repo_root`` may be None (non-SwarmAI project): files resolve per their own repo.
    Usage comes from ``run_token_usage`` (``turn_end_iso`` optionally extends the end
    bound); the stage-sum "估算" is used only when nothing is measured."""
    report_path: Any = ""
    try:
        report_path = str((Path(run_dir) / "REPORT.md").resolve())
    except Exception as exc:  # noqa: BLE001 — summary must never raise
        logger.warning("delivery summary: bad run_dir %r: %s", run_dir, exc)
        report_path = ""
    try:
        rs = _d(run_state)
        arts = _d(artifacts)
        run_dir = Path(run_dir)
        usage = run_token_usage(rs.get("session_id"), rs.get("created_at"),
                                rs.get("completed_at") or rs.get("updated_at"),
                                db_path=db_path, turn_end_iso=turn_end_iso)
        changes = file_changes(_l(rs.get("commits")), _l(rs.get("files_touched")), repo_root)
        return render_delivery_summary_md(
            rs, _unwrap(arts.get("evaluate")), _unwrap(arts.get("deliver")),
            _unwrap(arts.get("test")), usage, changes, report_path,
            stage_gaps=_stage_gaps(run_dir),
            estimate_tokens=None if usage else _estimate_tokens(rs),
        )
    except Exception as exc:  # the summary must never break run completion
        logger.warning("build_delivery_summary failed: %s", exc)
        return _minimal_summary(run_state, report_path)
