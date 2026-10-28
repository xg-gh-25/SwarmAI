"""The host side of Brain sync (design docs/design/2026-10-28-project-agent-flow.md §6):
push a Brain to a Hive and compare what each side has.

- ``local_brains(workspace)``: every Brain on this machine (``Projects/*`` and the
  General Brain) with its Project Agent status, whether it allows the Hive, and its
  content digest (computed here, from ``brain_sync.collect``).
- ``hive_brains(inst)``: what the Hive reports (``GET /api/hive/brains``), reduced to
  validated digests, times and counts; a Hive without the endpoint (404) is reported
  as too old to sync, so one update makes it syncable.
- ``sync(inst, brain)``: package, push (``PUT /api/hive/brains/<brain>``) and check
  the Hive answered with the same digest. A Brain other than the two default ones
  (SwarmAI and General) leaves this machine only when its Project Agent is reviewed
  and allows the Hive: a draft does not go anywhere.
- ``status(hive_ref)``: the console's rows for one Hive, and ``sync_defaults`` (the
  SwarmAI and General Brains, synced when missing or out of date).

Nothing the Hive writes is shown as text: only digests, ISO times and counts, each
checked for its shape.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from core import brain_sync
from core.project_agent import GENERAL_BRAIN, PROJECT_AGENT_FILE, general_brain_dir, normalize, validate

logger = logging.getLogger(__name__)

DEFAULT_BRAINS = ("SwarmAI", GENERAL_BRAIN)
SYNC_TIMEOUT_S = 120.0
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


class SyncRefused(ValueError):
    """The host will not (or cannot) sync this Brain to this Hive."""


# what each refusal of the install endpoint means (routers/hive_brains); the Hive's own
# text is never shown
_REFUSALS = {
    404: "this Hive's version cannot receive Brains; update it once, then sync",
    423: "the Hive is busy with {brain} (a run is using it, or it is being installed); "
         "sync again when it finishes",
    409: "the Hive's copy of {brain} cannot be written (a link or a file where a directory "
         "belongs, or a Brain whose name differs only in case); fix it on the Hive, then sync",
    413: "{brain} is larger than the Hive accepts",
    400: "the Hive did not accept the package of {brain} (its content did not match its digest)",
}


def _brain_dir(workspace: Path, brain: str) -> tuple[Path, bool]:
    return brain_sync.brain_root(workspace, brain)


def _agent(root: Path) -> dict[str, Any]:
    """{status: reviewed | draft | invalid | None, hive: allowed}."""
    try:
        doc = yaml.safe_load((root / PROJECT_AGENT_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"status": None, "hive": False}
    except (OSError, UnicodeError, yaml.YAMLError):
        return {"status": "invalid", "hive": False}
    if not isinstance(doc, dict) or validate(doc, root):
        return {"status": "invalid", "hive": False}
    n = normalize(doc)
    return {"status": "reviewed" if n.get("status") == "reviewed" else "draft",
            "hive": "hive" in (n.get("placements") or [])}


def local_brain(workspace: Path, brain: str) -> dict[str, Any]:
    root, general = _brain_dir(workspace, brain)
    row: dict[str, Any] = {"brain": brain, **_agent(root), "digest": None, "files": 0, "error": None}
    try:
        files = brain_sync.collect(root, general)
        row["digest"] = brain_sync.content_digest(files)
        row["files"] = len(files)
    except brain_sync.SyncError as exc:
        row["error"] = str(exc)
    return row


def local_brains(workspace: Path) -> list[dict[str, Any]]:
    names = [GENERAL_BRAIN]
    projects = Path(workspace) / "Projects"
    if projects.is_dir():
        names += sorted(p.name for p in projects.iterdir()
                        if p.is_dir() and not p.is_symlink() and (p / ".project.json").is_file()
                        and brain_sync.BRAIN_NAME_RE.fullmatch(p.name) and p.name != GENERAL_BRAIN)
    return [local_brain(workspace, n) for n in names]


def _clean_remote(row: Any) -> Optional[dict[str, Any]]:
    if not isinstance(row, dict) or not isinstance(row.get("brain"), str) \
            or not brain_sync.BRAIN_NAME_RE.fullmatch(row["brain"]):
        return None
    digest = row.get("digest")
    current = row.get("current_digest")
    at = row.get("installed_at")
    changed, running = row.get("changed_files"), row.get("running")
    return {"brain": row["brain"],
            "digest": digest if isinstance(digest, str) and _HEX64.fullmatch(digest) else None,
            "current_digest": current if isinstance(current, str) and _HEX64.fullmatch(current) else None,
            "installed_at": at if isinstance(at, str) and _ISO.fullmatch(at) else None,
            "changed_files": changed if isinstance(changed, int) and 0 <= changed < 10 ** 6 else None,
            "running": running if isinstance(running, int) and 0 <= running < 10 ** 4 else None}


async def hive_brains(inst: dict[str, Any]) -> dict[str, Any]:
    """``{supported, brains: {name: row}}``; supported False when the Hive's version
    has no Brains endpoint."""
    from core.swarm_out import _client
    async with _client(inst) as client:
        r = await client.get("/api/hive/brains")
    if r.status_code == 404:
        return {"supported": False, "brains": {}}
    if r.status_code != 200:
        raise SyncRefused(f"the Hive answered {r.status_code} for its Brains")
    try:
        data = r.json()
    except ValueError:
        raise SyncRefused("the Hive's list of Brains cannot be read") from None
    rows = [_clean_remote(x) for x in (data.get("brains") if isinstance(data, dict) else None) or []]
    return {"supported": True, "brains": {x["brain"]: x for x in rows if x}}


def _package(workspace: Path, brain: str, default: bool) -> tuple[bytes, str, int]:
    root, general = _brain_dir(workspace, brain)
    if not root.is_dir():
        raise SyncRefused(f"there is no Brain {brain} on this machine")
    agent = _agent(root)
    if not default and not (agent["status"] == "reviewed" and agent["hive"]):
        raise SyncRefused(f"{brain} goes to a Hive only once its Project Agent is confirmed "
                          "with the Hive allowed")
    files = brain_sync.collect(root, general)
    data = brain_sync.pack(files)
    if len(data) > brain_sync.MAX_PACKAGE_BYTES:
        raise SyncRefused(f"{brain} is larger than a Hive accepts (20 MB packed)")
    return data, brain_sync.content_digest(files), len(files)


async def sync(inst: dict[str, Any], brain: str, workspace: Optional[Path] = None) -> dict[str, Any]:
    """Push *brain* to the Hive; returns ``{brain, digest, files, installed_at}``."""
    from core.project_registry import get_swarmws
    from core.swarm_out import _client
    ws = workspace or get_swarmws()
    try:
        data, digest, n = await asyncio.to_thread(_package, ws, brain, brain in DEFAULT_BRAINS)
    except brain_sync.SyncError as exc:
        raise SyncRefused(str(exc)) from None
    async with _client(inst, SYNC_TIMEOUT_S) as client:
        r = await client.put(f"/api/hive/brains/{brain}", content=data,
                             headers={"X-Brain-Digest": digest, "Content-Type": "application/gzip"})
    if r.status_code != 200:
        raise SyncRefused(_REFUSALS.get(r.status_code, "the Hive refused {brain} ({code})").format(
            brain=brain, code=r.status_code))
    try:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict) or body.get("digest") != digest:
        raise SyncRefused(f"the Hive did not confirm {brain} at this version")
    at = body.get("installed_at")
    logger.info("brain sync: %s -> %s (%s, %d files)", brain, inst.get("name"), digest[:12], n)
    return {"brain": brain, "digest": digest, "files": n,
            "installed_at": at if isinstance(at, str) and _ISO.fullmatch(at) else None}


def _row(local: dict[str, Any], remote: Optional[dict[str, Any]], supported: bool) -> dict[str, Any]:
    """One console row: the local version, the Hive's, and whether it can be sent."""
    hive_digest = remote.get("digest") if remote else None
    synced = bool(local["digest"]) and hive_digest == local["digest"]
    reasons = []
    if not supported:
        reasons.append("this Hive's version is too old to receive Brains")
    if local["status"] != "reviewed":
        reasons.append("its Project Agent is a draft" if local["status"] == "draft"
                       else "it has no valid Project Agent")
    elif not local["hive"]:
        reasons.append("its Project Agent does not allow the Hive")
    if supported and not synced:
        reasons.append("not synced" if remote is None else "out of date")
    modified = None
    if remote and remote.get("current_digest") and remote.get("digest"):
        modified = remote.get("changed_files") if remote["current_digest"] != remote["digest"] else 0
    return {"brain": local["brain"], "agent": local["status"], "local_digest": local["digest"],
            "local_error": local["error"], "hive_digest": hive_digest,
            "installed_at": remote.get("installed_at") if remote else None,
            "synced": synced, "hive_changed_files": modified,
            "running": remote.get("running") if remote else None,
            "dispatchable": not reasons, "reasons": reasons,
            "default": local["brain"] in DEFAULT_BRAINS,
            "syncable": supported and (local["brain"] in DEFAULT_BRAINS
                                       or (local["status"] == "reviewed" and local["hive"]))}


async def status(hive_ref: str) -> dict[str, Any]:
    from core.project_registry import get_swarmws
    from core.swarm_out import _instance
    inst = await _instance(hive_ref)
    local, remote = await asyncio.gather(asyncio.to_thread(local_brains, get_swarmws()), hive_brains(inst))
    return {"hive": inst["name"], "supported": remote["supported"],
            "brains": [_row(b, remote["brains"].get(b["brain"]), remote["supported"]) for b in local]}


async def sync_defaults(hive_ref: str) -> dict[str, Any]:
    """Sync the SwarmAI and General Brains to the Hive when they are missing there or
    out of date. Returns ``{synced: [...], skipped: {brain: why}}``."""
    from core.project_registry import get_swarmws
    from core.swarm_out import _instance
    inst = await _instance(hive_ref)
    remote = await hive_brains(inst)
    out: dict[str, Any] = {"synced": [], "skipped": {}}
    if not remote["supported"]:
        out["skipped"] = {b: "this Hive's version is too old to receive Brains" for b in DEFAULT_BRAINS}
        return out
    ws = get_swarmws()
    for brain in DEFAULT_BRAINS:
        local = await asyncio.to_thread(local_brain, ws, brain)
        have = remote["brains"].get(brain)
        if local["digest"] is None:
            out["skipped"][brain] = local["error"] or "not on this machine"
        elif have and have.get("digest") == local["digest"]:
            out["skipped"][brain] = "up to date"
        else:
            try:
                await sync(inst, brain, ws)
                out["synced"].append(brain)
            except SyncRefused as exc:
                out["skipped"][brain] = str(exc)
    return out
