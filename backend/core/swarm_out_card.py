"""The dispatch card: what a Swarm Out dispatch did, shown in the chat (design
docs/design/2026-10-30-swarm-out-dispatch-card.md §3).

The streaming orchestrator watches a Bash call of ``scripts.swarm_out dispatch``
(``watched``) and, when it returns, asks ``card`` for the block. A dispatch that went
out is shown from **this machine's dispatch log row** with the ``dispatch_id`` the call
printed (``from_row``), never from the printed text: what the card says is what was
sent. A refused dispatch (``{"ok": false, "detail": ...}``) is a failure card with the
detail and the Brain / title the command named; it says nothing was sent. A dispatch the
Hive may have taken without naming the session (``"maybe_sent": true``, the router's 504
or the CLI's own timeout) says it may be running there instead.

The block carries ``text``, the same card as plain lines with no channel markup, for a
channel reply (the gateway appends ``channel_text``: the whole card in a DM, its first and
last line in a group; a channel cannot render the card or open the Hive tab). It is not
conversation (core/context_injector) and is persisted as its own row.

- ``watched(command)`` — the call is a Swarm Out dispatch
- ``card(command, output, since)`` — the block for its result, or None
- ``channel_text(block, direct)`` — what a channel reply carries
- ``from_row(row)`` / ``failure(...)`` / ``text_of(block)`` / ``hive_reason_text(code)``
"""
from __future__ import annotations

import json
import re
import shlex
from datetime import datetime
from typing import Any, Optional

BLOCK_TYPE = "swarm_out_dispatch"
_DISPATCH_RE = re.compile(r"scripts[./]swarm_out(?:\.py)?\s+dispatch\b")
_ID_RE = re.compile(r"so_[0-9a-f]{12}")
_DETAIL_MAX = 600
_FIELD_MAX = 300


def watched(command: Any) -> bool:
    return isinstance(command, str) and bool(_DISPATCH_RE.search(command))


def _args(command: str) -> dict[str, str]:
    """``--brain`` / ``--title`` / ``--hive`` of the dispatch call (for a failure card)."""
    m = _DISPATCH_RE.search(command)
    tail = command[m.end():] if m else ""
    try:
        words = shlex.split(tail, posix=True)
    except ValueError:
        words = tail.split()
    out: dict[str, str] = {}
    for i, w in enumerate(words):
        if w in ("&&", "||", ";", "|"):
            break
        key = w[2:] if w.startswith("--") else None
        if key in ("brain", "title", "hive") and i + 1 < len(words):
            out[key] = words[i + 1][:_FIELD_MAX]
    return out


def _json_head(output: str) -> Optional[dict]:
    """The first JSON object that starts a line of the call's output: what the CLI
    printed (one object, ``indent=2`` for a reply). Anything the command prints after
    it (an ``echo``, a stderr line) neither breaks the parse nor replaces it."""
    text = output or ""
    dec = json.JSONDecoder()
    for m in re.finditer(r"(?m)^\{", text):
        try:
            v, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(v, dict):
            return v
    return None


#: what argparse prints when the command line was wrong: nothing ran
_USAGE_RE = re.compile(r"^usage: swarm_out\b", re.M)
#: the dispatch log's clock vs the moment the call was seen (the same machine)
_SKEW_S = 5.0


def _sent_at(row: dict) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(row.get("sent_at")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def card(command: str, output: str, since: Optional[float] = None) -> Optional[dict]:
    """The card for a dispatch call's result, or None when nothing is to be shown.

    A row is shown only when this machine's log holds its ``dispatch_id`` and, with
    ``since`` (when the call was seen, epoch seconds), it was sent after the call
    started: an older dispatch's id in the output (printed again, echoed) is no card of
    this call. A call that printed no JSON (the Bash tool stopped it at its timeout, or
    it was sent to the background) may still have gone out: a "not sure" card. A wrong
    command line (argparse usage) ran nothing: no card."""
    got = _json_head(output)
    if got is None:
        if _USAGE_RE.search(output or ""):
            return None
        a = _args(command)
        return failure("the dispatch command gave no answer (it was stopped or is still running)",
                       a.get("title"), a.get("brain"), a.get("hive"), maybe_sent=True)
    did = got.get("dispatch_id")
    if isinstance(did, str) and _ID_RE.fullmatch(did):
        from core import swarm_out
        row = next((r for r in swarm_out._read_log() if r.get("dispatch_id") == did), None)
        if not row:
            return None
        at = _sent_at(row)
        if since is not None and (at is None or at < since - _SKEW_S):
            return None
        return from_row(row)
    if got.get("ok") is False:
        a = _args(command)
        return failure(str(got.get("detail") or "the dispatch was refused"), a.get("title"),
                       a.get("brain"), a.get("hive"), maybe_sent=got.get("maybe_sent") is True)
    return None


def _s(v: Any, n: int = _FIELD_MAX) -> Optional[str]:
    return " ".join(v.split())[:n] if isinstance(v, str) and v.strip() else None


def from_row(row: dict) -> dict:
    """The card of a dispatch that went out, from its dispatch log row."""
    pa = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else {}
    notes = {n.get("url"): n for n in row.get("repo_notes") or [] if isinstance(n, dict)}
    repos = []
    for r in row.get("repos") or []:
        if not isinstance(r, dict):
            continue
        n = notes.get(r.get("url")) or {}
        name = _s(n.get("name")) or (str(r.get("url") or "").rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
        repos.append({"name": name, "ref": _s(r.get("ref"), 100), "sha": _s(r.get("sha"), 64),
                      "dirty": n.get("dirty") if isinstance(n.get("dirty"), int) else 0})
    block = {
        "type": BLOCK_TYPE, "ok": True, "maybeSent": False,
        "dispatchId": row.get("dispatch_id"), "sessionId": row.get("session_id"),
        "title": _s(row.get("title")) or "", "brief": _s(row.get("brief"), 400),
        "hive": _s(row.get("hive")), "hiveReason": _s(row.get("hive_reason"), 80),
        "brain": _s(pa.get("brain"), 80), "mode": _s(pa.get("mode"), 80),
        "profile": _s(pa.get("profile"), 20), "revision": _s(pa.get("revision"), 64),
        "brainSync": _s(row.get("brain_sync"), 120), "repos": repos,
        "acceptance": _s(pa.get("acceptance"), 600), "delivery": _s(pa.get("delivery"), 40),
        "error": None, "at": row.get("sent_at") or datetime.now().astimezone().isoformat(),
    }
    block["text"] = text_of(block)
    return block


def failure(detail: str, title: Optional[str], brain: Optional[str], hive: Optional[str],
            maybe_sent: bool = False) -> dict:
    """A dispatch that did not go out, or (``maybe_sent``: no answer in time) may have."""
    block = {"type": BLOCK_TYPE, "ok": False, "maybeSent": maybe_sent, "dispatchId": None, "sessionId": None,
             "title": _s(title) or "", "brief": None, "hive": _s(hive), "hiveReason": None,
             "brain": _s(brain, 80), "mode": None, "profile": None, "revision": None,
             "brainSync": None, "repos": [], "acceptance": None, "delivery": None,
             "error": " ".join(detail.split())[:_DETAIL_MAX],
             "at": datetime.now().astimezone().isoformat()}
    block["text"] = text_of(block)
    return block


def hive_reason_text(code: Optional[str]) -> Optional[str]:
    """Why this Hive, in words (``swarm_out.pick_hive``'s code); None for a named one."""
    if not code or code == "named":
        return None
    if code == "only_free":
        return "the only one free"
    if code == "idle":
        return "idle"
    m = re.fullmatch(r"least_busy:(\d+)", code)
    return f"the least busy ({m.group(1)} running)" if m else code


_DELIVERY = {"bundle": "commits come back as a bundle; you decide whether they land",
             "branch-pr": "a branch and a pull request"}


def channel_text(b: dict, direct: bool) -> str:
    """What a channel reply carries: the whole card in a direct message; in a group,
    channel or thread only its first and last line (what and the dispatch id), so the
    brief, acceptance and repositories are not posted to everyone there."""
    text = b.get("text") if isinstance(b.get("text"), str) else ""
    if direct or not text:
        return text
    lines = text.split("\n")
    return "\n".join([lines[0], lines[-1]] if len(lines) > 1 and b.get("ok") else lines[:2])


def _plain(s: str) -> str:
    """No channel markup: Slack reads ``<!channel>`` / ``<url|label>`` in a reply, and
    an error can carry a Hive's text."""
    return s.replace("<", "‹").replace(">", "›")


def text_of(b: dict) -> str:
    """The card as plain lines (a channel reply), with no channel markup."""
    return _plain(_text_of(b))


def _text_of(b: dict) -> str:
    title = f": {b['title']}" if b.get("title") else ""
    if not b.get("ok") and b.get("maybeSent"):
        lines = [f"? Not sure it was dispatched{title}", b.get("error") or "",
                 "It may be running on the Hive: look in Swarm Out before sending it again."]
        return "\n".join(x for x in lines if x)
    if not b.get("ok"):
        lines = [f"✕ Not dispatched{title}", b.get("error") or "", "Nothing was sent to a Hive."]
        return "\n".join(x for x in lines if x)
    lines = [f"✓ Dispatched to a Hive: {b.get('title')}"]
    if b.get("brief"):
        lines.append(b["brief"])
    hive = b.get("hive") or "?"
    why = hive_reason_text(b.get("hiveReason"))
    lines.append(f"• Hive: {hive}" + (f" ({why})" if why else ""))
    agent = " / ".join(x for x in (b.get("mode"), b.get("profile") and f"profile {b['profile']}") if x)
    lines.append(f"• Brain: {b.get('brain') or '?'}" + (f" · Project Agent: {agent}" if agent else ""))
    for r in b.get("repos") or []:
        sha = (r.get("sha") or "")[:8]
        line = f"• Repository: {r.get('name')} @ {r.get('ref') or '?'}" + (f" {sha}" if sha else "")
        if r.get("dirty"):
            line += f" ({r['dirty']} uncommitted files not sent)"
        lines.append(line)
    if b.get("acceptance"):
        lines.append(f"• Done when: {b['acceptance']}")
    if b.get("delivery"):
        lines.append(f"• Delivery: {_DELIVERY.get(b['delivery'], b['delivery'])}")
    lines.append(f"Progress and results: Swarm Out in SwarmAI (dispatch {b.get('dispatchId')})")
    return "\n".join(lines)
