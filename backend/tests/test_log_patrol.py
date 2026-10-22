"""Daily log patrol (core/log_patrol + jobs/handlers/log_patrol; design S5).

What is tested:
- signatures: a daemon WARNING/ERROR masks ids, numbers, quoted values and paths
  (two lines differing only in those are one kind); a hook timeout is its own
  kind per hook; a 4xx/5xx access line is ``HTTP <status> <METHOD> <route>``;
  INFO lines and 2xx requests are not problems;
- compare: new kinds (only once a baseline exists), kinds at >= 2x their daily
  mean (and >= 10), hook timeouts over the daily limit even when not new;
- the handler: the first run records a baseline and notifies nothing; the next
  run reads only what the files gained (offsets), notifies once with the new
  kinds, and a rotated (shrunk) file is read from its start.
"""
from __future__ import annotations

from core.log_patrol import HOOK_TIMEOUT_DAILY_LIMIT, compare, count, signature
from jobs.handlers.log_patrol import run_log_patrol

D = "2026-10-23 01:13:49,699 [46881] - "


def test_signature_masks_variable_parts_and_keeps_the_kind():
    a = signature(D + "core.x - WARNING - session s-1a2b failed after 3.2s at /a/b/c.py: 'boom'")
    b = signature(D + "core.x - WARNING - session s-9f9f failed after 15s at /d/e/f.py: 'other'")
    assert a == b and a.startswith("WARNING core.x: session <id> failed after <n>s at <path>")
    assert signature(D + "core.x - INFO - fine") is None
    assert signature(D + "core.hook_builder - WARNING - Hook 'worktree_write_gate' timed out after 5.0s — skipping") \
        == "hook timeout: worktree_write_gate"
    assert signature('INFO:     127.0.0.1:55556 - "GET /api/chat/sessions/086356df-f92c-49f1-ab46-5491fd413d98'
                     '/messages?limit=2000 HTTP/1.1" 400 Bad Request') == "HTTP 400 GET /api/chat/sessions/<id>/messages"
    assert signature('INFO:     127.0.0.1:1 - "GET /health HTTP/1.1" 200 OK') is None


def test_compare_new_growing_and_hook_timeouts():
    hist = [{"WARNING a: x": 3, "hook timeout: g": 30}] * 7
    today = {"WARNING a: x": 9, "ERROR b: y": 1, "hook timeout: g": HOOK_TIMEOUT_DAILY_LIMIT}
    kinds = {(f.kind, f.signature) for f in compare(today, hist)}
    assert kinds == {("new", "ERROR b: y"), ("hook_timeouts", "hook timeout: g")}
    assert {(f.kind, f.signature) for f in compare({"WARNING a: x": 10}, hist)} == {("growing", "WARNING a: x")}
    assert compare({"ERROR b: y": 1}, []) == []        # no baseline: nothing is "new"


def test_handler_baseline_then_only_new_bytes_then_rotation(tmp_path):
    log = tmp_path / "backend-daemon.log"
    log.write_text(D + "core.x - WARNING - old kind 1\n")
    sent = []
    r = run_log_patrol(log_dir=tmp_path, notifier=lambda **k: sent.append(k), today="2026-10-21")
    assert r["baseline_only"] and not sent
    with log.open("a") as fh:
        fh.write(D + "core.x - WARNING - old kind 2\n" + D + "core.y - ERROR - brand new 7\n")
    r = run_log_patrol(log_dir=tmp_path, notifier=lambda **k: sent.append(k), today="2026-10-22")
    assert [f["signature"] for f in r["findings"]] == ["ERROR core.y: brand new <n>"]
    assert r["events"] == 2 and len(sent) == 1 and "brand new" in sent[0]["message"]
    log.write_text(D + "core.y - ERROR - brand new 8\n")            # rotated: smaller file
    r = run_log_patrol(log_dir=tmp_path, notifier=lambda **k: sent.append(k), today="2026-10-23")
    assert r["events"] == 1 and r["findings"] == [] and len(sent) == 1
    # three days later the run compares per-day rates: 30 lines over 3 days =
    # 10/day, 10x the 1/day before -> growing (not 30/day)
    with log.open("a") as fh:
        fh.write((D + "core.y - ERROR - brand new 1\n") * 30)
    r = run_log_patrol(log_dir=tmp_path, notifier=lambda **k: sent.append(k), today="2026-10-26")
    assert [(f["kind"], f["count"]) for f in r["findings"]] == [("growing", 10.0)]
    assert count([D + "core.y - ERROR - brand new 9\n"]) == {"ERROR core.y: brand new <n>": 1}
