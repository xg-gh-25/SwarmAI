"""One-shot cleanup of stored chat history in ``messages`` (data.db).

New rows are already written lean (2026-10-22): ``content_seg`` holds only
searchable words (core/message_search_text.py), thinking blocks carry no
``signature``, and attachment bytes live in files (core/message_attachments.py).
This script brings the EXISTING rows to the same shape:

- image/document base64 -> ``<data dir>/attachments/<sha256>`` + reference;
- ``signature`` removed from thinking blocks (nothing replays thinking from
  these rows: resume reads the SDK transcript);
- ``content_seg`` recomputed, which also re-indexes ``messages_fts`` through the
  table's update trigger.

Rows are rewritten only when something changes, in small committed chunks with
a pause between them, so the running daemon keeps writing. The file does not
shrink by itself (freed pages are reused); ``VACUUM`` needs the daemon stopped
and is not done here.

Usage::

    python scripts/cleanup_message_history.py [--db PATH] [--apply] [--chunk 300]

Without ``--apply``: counts only, writes nothing. With it: first a verified
online backup (``<db>.pre-msg-cleanup-<UTC>.db``, owner-only), then the rewrite.

Public: ``clean_content(content, store_dir) -> str | None``,
``needs_content_change(content) -> bool``, ``main()``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.message_attachments import attachments_dir, externalize_attachments  # noqa: E402
from core.message_search_text import message_content_seg  # noqa: E402


def _blocks(content: "str | None") -> "list | None":
    try:
        blocks = json.loads(content or "null")
    except (TypeError, ValueError):
        return None
    return blocks if isinstance(blocks, list) else None


def needs_content_change(content: "str | None") -> bool:
    """True when the row has a thinking signature or an inline base64 attachment."""
    for b in _blocks(content) or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "thinking" and "signature" in b:
            return True
        src = b.get("source")
        if b.get("type") in ("image", "document") and isinstance(src, dict) \
                and src.get("type") == "base64":
            return True
    return False


def clean_content(content: "str | None", store_dir: Path) -> "str | None":
    """New ``content`` JSON for a row (writes attachment files), or None if unchanged."""
    blocks = _blocks(content)
    if blocks is None or not needs_content_change(content):
        return None
    blocks = [{k: v for k, v in b.items() if k != "signature"}
              if isinstance(b, dict) and b.get("type") == "thinking" else b
              for b in blocks]
    blocks = externalize_attachments(blocks, store_dir)
    return json.dumps(blocks, ensure_ascii=False)


def _backup(db: Path) -> Path:
    need = int(db.stat().st_size * 1.2)
    if shutil.disk_usage(db.parent).free < need:
        raise OSError(f"refusing to back up: need ~{need // 1_000_000} MB free")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = db.with_name(f"{db.stem}.pre-msg-cleanup-{stamp}.db")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst, pages=4096, sleep=0.02)
    finally:
        dst.close()
        src.close()
    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        ok = check.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        check.close()
    if ok != "ok":
        raise OSError(f"backup failed quick_check: {ok}")
    os.chmod(target, 0o600)  # a full copy of every chat message
    return target


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=Path.home() / ".swarm-ai" / "data.db")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--chunk", type=int, default=300)
    ap.add_argument("--pause", type=float, default=0.1)
    args = ap.parse_args(argv)

    store = attachments_dir(args.db)
    if args.apply:
        print("backup ->", _backup(args.db), flush=True)
    conn = sqlite3.connect(str(args.db), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")

    rowids = [r[0] for r in conn.execute("SELECT rowid FROM messages ORDER BY rowid")]
    n_content = n_seg = before = after = 0
    t0 = time.time()
    for i in range(0, len(rowids), args.chunk):
        batch = rowids[i:i + args.chunk]
        marks = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT rowid, content, content_seg FROM messages WHERE rowid IN ({marks})",
            batch).fetchall()
        updates = []
        for rowid, content, seg in rows:
            if args.apply:
                new_content = clean_content(content, store)
            else:
                new_content = content if needs_content_change(content) else None
            target = content if new_content is None else new_content
            new_seg = message_content_seg(target)
            n_content += new_content is not None
            n_seg += new_seg != seg
            before += len(content or "") + len(seg or "")
            after += len(target or "") + len(new_seg or "")
            if args.apply and (new_content is not None or new_seg != seg):
                updates.append((target, new_seg, rowid))
        if updates:
            conn.executemany(
                "UPDATE messages SET content = ?, content_seg = ? WHERE rowid = ?", updates)
            conn.commit()
            time.sleep(args.pause)
        if (i // args.chunk) % 100 == 0:
            print(f"  {i + len(batch)}/{len(rowids)} rows, {time.time() - t0:.0f}s", flush=True)
    conn.close()
    mb = 1024 * 1024
    verb = "rewrote" if args.apply else "would rewrite"
    print(f"{verb}: content {n_content} rows, content_seg {n_seg} rows of {len(rowids)}; "
          f"content+seg {before // mb} MB -> {after // mb} MB"
          + ("" if args.apply else " (dry run: attachment bytes still counted inline)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
