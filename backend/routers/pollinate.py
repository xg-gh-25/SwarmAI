"""Pollinate Content-Asset Gallery — router (sibling of routers/pipelines.py).

Mostly-read gallery + ONE write path (mark-published, P1 run_b290eb6f):
  GET  /api/pollinate/assets              — newest-first content cards + overall rollup
  GET  /api/pollinate/{run_name}          — one content topic's full detail
  POST /api/pollinate/{run_name}/publish  — mark an asset published/unpublished (sidecar write)

ASSET-CENTRIC (Gate-1 BLOCKs baked in):
  * The `deliver/` DIR WALK is the source of truth for what was produced — NOT
    run.json.formats/platforms (often None on running runs) and NOT the presence of
    run.json at all (only 5/14 dirs have one). A card is synthesized from the deliver/
    tree (or bare asset files) even when run.json is absent. run.json is an optional
    metadata overlay (topic/domain/status/created_at).
  * deliver/ layout is non-uniform: `deliver/{platform}/{file}` (nested) AND
    `deliver/{file}` (bare, no platform subdir). Both are handled.
  * Terminal detection uses a POLLINATE-LOCAL check — is_terminal_run (pipelines) reads
    stage='...'/status='completed', but pollinate stages use name='REFLECT'(UPPERCASE)/
    status in {done,completed}, so the pipeline helper's stage branch never fires. Reusing
    it would mis-gate the metadata cache on review/running-but-done runs.
  * publish_status precedence: sidecar 'published' (the P1 publish-state.json, user-marked
    via POST /publish — the AUTHORITY) > 'ready-to-publish' (a publish-kit.md frontmatter
    says so) > 'ready' (default/unknown). Before P1 'published' was never reachable; it is
    now set ONLY by an explicit user mark-published write, never fabricated from content.

Thumbnails are served by the EXISTING GET /api/workspace/file/raw (FileResponse,
workspace-sandboxed) — no new media endpoint. The frontend points <img> at it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re as _re
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from jobs.paths import SWARMWS
from schemas.pollinate import (
    PollinateAsset,
    PollinateAssetsResponse,
    PollinateContentCard,
    PollinateOverall,
    PollinateTopicDetail,
    PublishRequest,
    PublishResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# The Pollinate corpus root (flat: one dir per content topic).
_POLLINATE_ROOT = "Knowledge/Pollinate"

# Newest N cards get their assets materialized; older cards still counted in overall.
# The corpus is tens of items today; this cap is a pre-mortem guard against a
# thousand-run future (mirrors pipelines' newest-N materialize).
_ASSET_MATERIALIZE_CAP = 60

# run_name is a user-controlled path param → reject traversal BEFORE touching the FS.
# Real dir names are `2026-05-03-memory-is-the-moat` (digits/letters/hyphens); the dot
# is admitted for safety but no real dir uses one. Looser than pipelines' `run_...`
# regex because pollinate dirs are date-name slugs, not run_<hex> ids.
_RUN_NAME_RE = _re.compile(r"^[A-Za-z0-9._-]+$")

# The publish endpoint's asset_id is a sha1 hexdigest. Constraining it to 40-hex means a
# crafted asset_id can NEVER encode a path — it is a pure sidecar-JSON key, never a filename.
_ASSET_ID_RE = _re.compile(r"^[0-9a-f]{40}$")

# Image extensions that <img src=/api/workspace/file/raw> can render inline.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

# Known channel names → used to detect the nested deliver/{platform}/ layout and to
# normalize a bare file's platform to '' (the design's honest "unknown platform").
_KNOWN_PLATFORMS = {"xiaohongshu", "bilibili", "github", "gongzhonghao", "youtube", "twitter", "linkedin"}

# Build-toolchain / metadata files that get written INTO a content tree (esp. tracks/deck/:
# build_deck.js, render_visuals.js, build_deck.py, package.json, package-lock.json, notes.json)
# but are NOT content assets. A file is NOISE if its extension is here (a script/manifest/lock)
# OR it is a QR image (an attachment, not a content deliverable). This is a NEGATIVE filter —
# it never drops a genuine content deliverable: every real Pollinate asset is an image/av/doc
# (png/jpg/webp/svg/mp4/mov/webm/wav/mp3/srt/pdf/pptx/md/html/txt), none of which are below.
# NOTE: this deliberately does NOT filter by _classify_format 'other' — content_package.md /
# platform_matrix.md classify as 'other' but are real (.md), so an extension filter spares them.
_NOISE_EXTS = {".js", ".ts", ".py", ".pyc", ".json", ".lock", ".lockb", ".sh", ".mjs", ".cjs"}

# Tracks-subdir names that are genuine CONTENT formats (the tracks/{format}/ layout). Used to
# label a tracks/ card's format honestly (the subdir is a FORMAT, never a platform — do NOT
# fabricate a platform from it).
_TRACKS_CONTENT_FORMATS = {"poster", "pdf", "deck", "video", "narrative", "readme", "caption", "shorts"}

# Content-root subdirs for the podcast/shorts layout (2026-04-26-aidlc-one-sentence-to-pr):
# a produced topic whose media lives in video/ (flat: podcast .wav/.mp3 + thumbnails) and
# shorts/<name>/ (nested: short audio) rather than deliver/ or tracks/. These are REAL
# deliverables — a dir carrying them is a topic, NOT a scratch bucket. The subdir name is
# the FORMAT (both are in _TRACKS_CONTENT_FORMATS above).
_MEDIA_SUBDIRS = {"video", "shorts"}

# A non-terminal run whose newest timestamp is older than this is treated as abandoned:
# it still renders as a card (with its real status), but is NOT counted in overall.in_progress.
_STALE_DAYS = 30


def _is_noise_asset(file_name: str) -> bool:
    """True if a file under a content tree is build-toolchain / metadata / a QR attachment
    rather than a content deliverable. Extension-based (spares .md/.html metadata) + a QR-image
    special case. NEVER matches a genuine content asset (image/av/doc)."""
    n = file_name.lower()
    ext = Path(n).suffix
    if ext in _NOISE_EXTS:
        return True
    # QR codes are attachments (a scan target), not a content deliverable to browse.
    if ext in _IMAGE_EXTS and "qr" in n:
        return True
    return False


def _get_swarmws() -> Path:
    """Indirection so tests can monkeypatch the corpus root (mirrors pipelines)."""
    return SWARMWS


def _is_terminal_pollinate(run: dict) -> bool:
    """Pollinate-local terminal check (Gate-1 BLOCK-2).

    A pollinate run is terminal when EITHER the top-level status is an explicit
    terminal value, OR a REFLECT/DELIVER stage is done. Pollinate stages use key
    'name' (UPPERCASE) and status in {done, completed} — deliberately NOT the
    pipeline schema (stage='reflect'/status='completed'), so we cannot reuse
    is_terminal_run here.
    """
    if not isinstance(run, dict):
        return False
    status = (run.get("status") or "").lower()
    if status in ("completed", "complete", "abandoned", "failed", "cancelled"):
        return True
    stages = run.get("stages") or []
    if not isinstance(stages, list):
        return False
    for s in stages:
        if not isinstance(s, dict):
            continue
        name = (s.get("name") or s.get("stage") or "").upper()
        st = (s.get("status") or "").lower()
        if name in ("REFLECT", "DELIVER") and st in ("done", "completed"):
            return True
    return False


def _parse_iso_epoch(ts: str) -> Optional[float]:
    """Best-effort ISO-8601 → epoch seconds. Returns None on anything unparseable
    (a date-only 'YYYY-MM-DD' is accepted). Never raises."""
    if not ts or not isinstance(ts, str):
        return None
    from datetime import datetime, timezone

    def _epoch(dt: datetime) -> float:
        # A naive timestamp (no offset) or the date-only fallback would otherwise be
        # interpreted in the SERVER's local tz by .timestamp() — an up-to-N-hour skew vs a
        # UTC-intended value, material only for a run within hours of the 30d boundary.
        # Normalize naive → UTC so staleness is tz-stable regardless of who wrote the run.json.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()

    s = ts.strip().replace("Z", "+00:00")
    try:
        return _epoch(datetime.fromisoformat(s))
    except (ValueError, TypeError):
        pass
    # date-only fallback
    try:
        return _epoch(datetime.strptime(s[:10], "%Y-%m-%d"))
    except (ValueError, TypeError):
        return None


def _run_is_stale(run: dict, run_dir: Path, now: Optional[float] = None) -> bool:
    """True if a non-terminal run is old enough to be treated as abandoned (not counted
    in overall.in_progress). PRIMARY signal = run.json created_at/updated_at (Gate-1 must-fix:
    dir mtime alone would flip EVERY run to stale after a bulk FS touch — git checkout / rsync /
    restore — the run_a16d61ad data-loss class). dir mtime is a LAST-RESORT fallback only when
    the run.json carries no timestamp. A run with a fresh updated_at is NEVER stale regardless
    of dir mtime."""
    import time as _time

    if now is None:
        now = _time.time()
    cutoff = now - _STALE_DAYS * 86400
    newest = None
    if isinstance(run, dict):
        for key in ("updated_at", "created_at"):
            e = _parse_iso_epoch(run.get(key) or "")
            if e is not None and (newest is None or e > newest):
                newest = e
    if newest is None:
        # No usable run.json timestamp → fall back to dir mtime (last resort).
        try:
            newest = run_dir.stat().st_mtime
        except OSError:
            return False  # can't tell → do NOT hide it from in_progress
    return newest < cutoff


def _classify_format(file_name: str) -> str:
    """Best-effort content-format label from a deliverable file name."""
    n = file_name.lower()
    ext = Path(n).suffix
    if ext in _IMAGE_EXTS:
        if "qr" in n:
            return "qr"
        return "poster"
    if ext in (".mp4", ".mov", ".webm"):
        return "video"
    if "narrative" in n:
        return "narrative"
    if "readme" in n or "readme_section" in n:
        return "readme"
    if "caption" in n or "publish-kit" in n or "dynamic_text" in n:
        return "caption"
    if "platform_matrix" in n or "content_package" in n:
        return "other"
    return "other"


def _publish_status_for(deliver_dir: Path, platform: str) -> str:
    """Read publish state honestly. Default 'ready' (unknown). Only a publish-kit.md
    with `status:` frontmatter upgrades it. A posted-URL 'published' state does not
    exist yet (no distribution) — never fabricated. (Gate-1 HIGH.)"""
    # Look for a publish-kit.md in the platform subdir (nested layout) or the
    # deliver/ root (bare layout).
    candidates = []
    if platform:
        candidates.append(deliver_dir / platform / "publish-kit.md")
    candidates.append(deliver_dir / "publish-kit.md")
    for kit in candidates:
        try:
            if not kit.is_file():
                continue
            head = kit.read_text(encoding="utf-8", errors="replace")[:600]
            if head.lstrip().startswith("---"):
                for line in head.splitlines():
                    ls = line.strip().lower()
                    if ls.startswith("status:"):
                        val = ls.split(":", 1)[1].strip()
                        if "publish" in val:  # ready-to-publish / published
                            return "published" if val == "published" else "ready-to-publish"
        except OSError:
            continue
    return "ready"


# --- P1 publish-state sidecar (run_b290eb6f) -------------------------------------------
# A per-run publish-state.json is the AUTHORITY for user-marked 'published' state. It is
# keyed on the asset's STABLE LOGICAL id (below), NOT its physical path — a path key would
# orphan the mark when the deliver↔tracks root-flip changes an asset's parent dir.
_PUBLISH_STATE_FILE = "publish-state.json"


def _asset_id(platform: str, fmt: str, file_name: str) -> str:
    """Stable logical id for an asset = sha1(platform/format/file_name).

    ⚠️ CONTRACT: this id is a function of the CLASSIFIER output (platform + _classify_format
    /_TRACKS_CONTENT_FORMATS), NOT the physical path — that is deliberate (it survives the
    deliver↔tracks root-flip). The flip side: editing _classify_format or the platform/format
    labelling will re-hash existing assets and ORPHAN their stored publish-state entries.
    Treat the (platform, format, file_name) → id mapping as a stable contract.
    """
    return hashlib.sha1(
        f"{platform}/{fmt}/{file_name}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()


def _publish_state_path(run_dir: Path) -> Path:
    return run_dir / _PUBLISH_STATE_FILE


def _load_publish_state(run_dir: Path) -> dict:
    """Read the per-run publish-state sidecar. GUARDED (mirrors _read_run_json): a missing,
    malformed, or half-written file degrades to {} — the gallery/detail must NEVER 500 on a
    bad sidecar (the detail endpoint has no per-card try/except, so the loader itself is safe).
    Returns {asset_id: {"published": bool, "posted_url": str|None}}."""
    p = _publish_state_path(run_dir)
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        return {}
    return {}


def _write_publish_state(run_dir: Path, state: dict) -> None:
    """Atomically write the sidecar (tempfile + os.replace — the codebase idiom, ddd_brain
    _write_watermark). CONCURRENCY POSTURE: single-user, low-frequency (the overlay is a
    manual mark-as-posted toggle) → last-write-wins is ACCEPTED; no flock. The atomic replace
    guarantees a reader never sees a half-written file, only never-lost-update across a rare
    concurrent double-POST — an accepted trade-off at this scale, documented deliberately."""
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(run_dir), prefix=".publish-state.", suffix=".tmp")
    try:
        # Guard the raw fd: mkstemp returns an OPEN descriptor; os.fdopen ADOPTS it (and its
        # context-manager closes it). But if fdopen itself raises before adopting, the raw fd
        # would leak — close it explicitly on that narrow path (textbook mkstemp+fdopen pitfall).
        try:
            fh = os.fdopen(tmp_fd, "w", encoding="utf-8")
        except BaseException:
            os.close(tmp_fd)
            raise
        with fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, str(_publish_state_path(run_dir)))
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _walk_assets(run_dir: Path) -> list[PollinateAsset]:
    """Enumerate produced assets from the deliver/ tree — the SOURCE OF TRUTH.

    Handles BOTH layouts (Gate-1 BLOCK-1):
      deliver/{platform}/{file}   (nested — v2-launch, memory-is-the-moat)
      deliver/{file}              (bare  — 2026-07-04-pollinate-xhs)
    If there is no deliver/ dir, also picks up bare media files in the run dir root
    (the design's `2026-04-26-pollinate-poster` case: flat poster files).
    """
    assets: list[PollinateAsset] = []
    swarmws = _get_swarmws()
    deliver = run_dir / "deliver"
    # Load the publish-state sidecar ONCE per run (not per-asset) — closed over in _add.
    publish_state = _load_publish_state(run_dir)

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(swarmws))
        except ValueError:
            return str(p)

    def _add(f: Path, platform: str, format_hint: str = "") -> None:
        if not f.is_file() or f.name.startswith("."):
            return
        if _is_noise_asset(f.name):  # build script / manifest / lock / QR attachment — not content
            return
        ext = f.suffix.lower()
        fmt = _classify_format(f.name)
        # tracks/{format}/ layout: the subdir names the FORMAT. Use it only when the
        # filename classifier fell through to 'other' (so a poster.png stays 'poster',
        # but a deck's outline.md / speaker_notes.md read honestly as 'deck', not 'other').
        if fmt == "other" and format_hint in _TRACKS_CONTENT_FORMATS:
            fmt = format_hint
        aid = _asset_id(platform, fmt, f.name)
        # Precedence: sidecar 'published' (AUTHORITY) > kit 'ready-to-publish' > 'ready'.
        status = _publish_status_for(deliver, platform) if deliver.is_dir() else "ready"
        posted_url = None
        entry = publish_state.get(aid)
        if isinstance(entry, dict) and entry.get("published"):
            status = "published"
            pu = entry.get("posted_url")
            posted_url = pu if isinstance(pu, str) and pu else None
        assets.append(
            PollinateAsset(
                platform=platform,
                format=fmt,
                file_path=_rel(f),
                file_name=f.name,
                is_image=ext in _IMAGE_EXTS,
                publish_status=status,
                asset_id=aid,
                posted_url=posted_url,
            )
        )

    # The asset root is `deliver/` when NON-EMPTY, else `tracks/` (an alternate
    # layout in the real corpus: tracks/{format}/{file}, e.g. swarmai-social-series
    # which has BOTH an empty deliver/ AND a populated tracks/), else bare media
    # files in the run dir. All three are REAL layouts seen in the corpus.
    def _non_empty_dir(p: Path) -> bool:
        try:
            return p.is_dir() and any(c for c in p.iterdir() if not c.name.startswith("."))
        except OSError:
            return False

    media_subdirs = [run_dir / d for d in sorted(_MEDIA_SUBDIRS) if _non_empty_dir(run_dir / d)]
    if _non_empty_dir(deliver):
        asset_root = deliver
    elif _non_empty_dir(run_dir / "tracks"):
        asset_root = run_dir / "tracks"
    else:
        asset_root = deliver  # (empty/absent) → falls to the media/bare-file branches below
    if asset_root.is_dir():
        for child in sorted(asset_root.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir():  # nested {platform-or-format}/{file}
                # In deliver/ the subdir is a platform; in tracks/ it's a format.
                # A known platform → platform label; otherwise pass it as a format hint
                # (honest: tracks subdirs are formats, never fabricated platforms).
                label = child.name
                is_platform = label in _KNOWN_PLATFORMS
                for f in sorted(child.iterdir()):
                    _add(f, label if is_platform else "", format_hint="" if is_platform else label.lower())
            else:  # bare {root}/{file}
                _add(child, "")
    elif media_subdirs:
        # No deliver/ or tracks/ — the podcast/shorts layout: media lives in video/ (flat)
        # and shorts/<name>/ (one nesting level deeper). The subdir name is the FORMAT.
        # Recurse ONE extra level so shorts/<name>/<file> surfaces (video/ is flat).
        for sub in media_subdirs:
            fmt = sub.name.lower()
            for child in sorted(sub.iterdir()):
                if child.name.startswith("."):
                    continue
                if child.is_file():
                    _add(child, "", format_hint=fmt)
                elif child.is_dir():  # shorts/<name>/<file>
                    for f in sorted(child.iterdir()):
                        _add(f, "", format_hint=fmt)
    else:
        # No deliver/tracks/media subdirs — pick up bare media assets in the run dir root.
        for f in sorted(run_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTS:
                _add(f, "")
    return assets


def _read_run_json(run_dir: Path) -> Optional[dict]:
    import json

    rj = run_dir / "run.json"
    try:
        if rj.is_file():
            return json.loads(rj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return None


def _dir_has_content(run_dir: Path, assets: list, materialize: bool) -> bool:
    """A no-run.json dir is a real content card only if it actually produced assets.
    When materialized we have the real asset list; when not, cheaply check for a
    NON-EMPTY deliver/ or bare media files (an empty deliver/ dir is noise)."""
    if materialize:
        return bool(assets)
    for sub in ("deliver", "tracks"):
        root = run_dir / sub
        if root.is_dir():
            try:
                return any(c for c in root.iterdir() if not c.name.startswith("."))
            except OSError:
                return False
    try:
        return any(f.suffix.lower() in _IMAGE_EXTS for f in run_dir.iterdir() if f.is_file())
    except OSError:
        return False


def _is_scratch_dir(run_dir: Path) -> bool:
    """A scratch dir is a loose bucket of images with NO run.json, NO structured content
    subdir (deliver/ tracks/ video/ shorts/), and NO topic marker file — an ad-hoc collection
    (posters/, 2026-04-26-pollinate-poster/), not a produced pollinate topic. A real topic
    always carries EITHER a run.json, OR a structured content subdir, OR a topic marker
    (content_package.md / REPORT.md). Excluding only true scratch de-noises the gallery
    without hiding a genuine run (e.g. the podcast/shorts topic aidlc-one-sentence-to-pr,
    whose media is in video/ + shorts/ and which carries content_package.md + REPORT.md).
    """
    if (run_dir / "run.json").is_file():
        return False
    for marker in ("content_package.md", "REPORT.md"):
        if (run_dir / marker).is_file():
            return False
    for sub in ("deliver", "tracks", *_MEDIA_SUBDIRS):
        p = run_dir / sub
        try:
            if p.is_dir() and any(c for c in p.iterdir() if not c.name.startswith(".")):
                return False
        except OSError:
            return False
    return True


def _build_card(run_dir: Path, materialize: bool) -> Optional[PollinateContentCard]:
    """Build one content card. run.json is optional metadata; deliver/ walk is truth."""
    run_name = run_dir.name
    run = _read_run_json(run_dir)

    # Scratch dirs (loose images, no run.json/deliver/tracks) are ad-hoc buckets, not
    # produced topics — never a content card (P3). Checked before the walk so a 23-image
    # scratch pile never becomes the largest card.
    if run is None and _is_scratch_dir(run_dir):
        return None

    assets = _walk_assets(run_dir) if materialize else []

    # A dir with neither run.json NOR any produced asset is not a content card
    # (real corpus has dirs with an EMPTY deliver/ and no run.json — pure noise).
    if run is None and not _dir_has_content(run_dir, assets, materialize):
        return None

    topic = run_name
    domain = None
    status = "unknown"
    created_at = None
    if run:
        topic = run.get("topic") or run.get("message") or run_name
        domain = run.get("domain")
        status = run.get("status") or "unknown"
        created_at = run.get("created_at") or run.get("updated_at")
    elif materialize and assets:
        # No run.json but real assets were produced → the topic is DONE, not 'unknown'
        # (its deliverables exist). Honest status so the frontend's running/review-keyed
        # "Produce more" affordance doesn't offer to re-run a finished topic.
        status = "completed"

    if not created_at:
        # Fall back to the date prefix in the dir name (YYYY-MM-DD-...).
        m = _re.match(r"^(\d{4}-\d{2}-\d{2})", run_name)
        if m:
            created_at = m.group(1)

    platforms = sorted({a.platform for a in assets if a.platform})
    formats = sorted({a.format for a in assets})
    published = sum(1 for a in assets if a.publish_status == "published")
    ready = sum(1 for a in assets if a.publish_status in ("ready", "ready-to-publish"))

    return PollinateContentCard(
        run=run_name,
        topic=topic,
        domain=domain,
        status=status,
        created_at=created_at,
        has_run_json=run is not None,
        asset_count=len(assets),
        platforms=platforms,
        formats=formats,
        published_count=published,
        ready_count=ready,
        assets=assets,
    )


def _list_run_dirs() -> list[Path]:
    root = _get_swarmws() / _POLLINATE_ROOT
    if not root.is_dir():
        return []
    return [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]


@router.get("/assets", response_model=PollinateAssetsResponse)
async def pollinate_assets() -> PollinateAssetsResponse:
    """Newest-first content cards + overall rollup. Always 200 (a bad run dir is
    skipped, never 500s the gallery). The newest N cards materialize their assets;
    the overall counts reflect ALL cards."""
    run_dirs = _list_run_dirs()

    # Sort newest-first by created_at, falling back to the YYYY-MM-DD dir-name prefix.
    # A dir with NEITHER (e.g. a dateless `posters/` bucket) sorts LAST — it must not
    # bury genuinely-newest content at the top (real-data smoke: `posters` string-sorted
    # above every 2026-... dir). Key = (has_date, date_string) so dated dirs win.
    def _sort_key(d: Path) -> tuple[int, str]:
        run = _read_run_json(d)
        if run:
            ts = run.get("created_at") or run.get("updated_at")
            if ts:
                return (1, ts)
        m = _re.match(r"^(\d{4}-\d{2}-\d{2})", d.name)
        if m:
            return (1, m.group(1))
        return (0, d.name)  # dateless → group 0 → sorts last under reverse=True

    run_dirs.sort(key=_sort_key, reverse=True)

    overall = PollinateOverall()
    # The known-channel universe (SSOT = _KNOWN_PLATFORMS) so the Insights by-channel
    # view can grey out a FULLY-neglected channel (0 assets anywhere) — which
    # platform_dist (only channels that HAVE assets) structurally cannot reveal.
    overall.known_channels = sorted(_KNOWN_PLATFORMS)
    cards: list[PollinateContentCard] = []

    # Every card is fully materialized (walked) so the OVERALL rollup is HONEST —
    # card_count AND asset/platform/format/publish counts all reflect the WHOLE
    # corpus (Gate-2 MED: counting only the capped slice made "Assets/Ready" lie
    # once the corpus exceeds the cap). At tens-of-items scale walking all is
    # cheap (O008 — no measured perf problem). The cap bounds ONLY the returned
    # `cards[]` PAYLOAD size (the frontend renders newest-first anyway); if the
    # corpus ever reaches thousands, move the walk behind a cheap file-count.
    for idx, run_dir in enumerate(run_dirs):
        try:
            card = _build_card(run_dir, materialize=True)
        except Exception:  # noqa: BLE001 — one bad dir must not sink the gallery
            logger.warning("pollinate: failed to build card for %s", run_dir.name, exc_info=True)
            continue
        if card is None:
            continue

        overall.card_count += 1
        # in_progress = has a run.json that is NOT terminal (the Gate-1 BLOCK-2
        # semantic: a status='review' run whose REFLECT stage is done is DONE, not
        # in-progress — a raw status-string check would mis-count it).
        # _is_terminal_pollinate reads the real pollinate stage schema (name=UPPERCASE
        # / status in {done,completed}). A dir with NO run.json but real assets is
        # DONE (the assets exist), NOT in-progress — an unknown status is not a
        # running one, so we only count runs that explicitly report non-terminal.
        run_meta = _read_run_json(run_dir)
        if (
            run_meta is not None
            and not _is_terminal_pollinate(run_meta)
            and not _run_is_stale(run_meta, run_dir)
        ):
            overall.in_progress += 1
        if card.domain:
            overall.domain_dist[card.domain] = overall.domain_dist.get(card.domain, 0) + 1
        overall.asset_count += card.asset_count
        overall.published += card.published_count
        overall.ready += card.ready_count
        for a in card.assets:
            if a.platform:
                overall.platform_dist[a.platform] = overall.platform_dist.get(a.platform, 0) + 1
            overall.format_dist[a.format] = overall.format_dist.get(a.format, 0) + 1

        if idx < _ASSET_MATERIALIZE_CAP:
            cards.append(card)

    return PollinateAssetsResponse(overall=overall, cards=cards)


def _resolve_run_dir(run_name: str) -> Optional[Path]:
    """Validate a user-supplied run_name and resolve it to a contained run dir, or None.

    ⚠️ _RUN_NAME_RE ALONE IS NOT A TRAVERSAL GUARD — it admits '.'/'..'. The real guard is
    the resolve() + relative_to(root) containment check below. Both the read (detail) and the
    write (publish) endpoints route through here so the guard can't drift between them."""
    if not run_name or not _RUN_NAME_RE.match(run_name):
        return None
    root = _get_swarmws() / _POLLINATE_ROOT
    run_dir = (root / run_name).resolve()
    try:
        run_dir.relative_to(root.resolve())
    except ValueError:
        return None
    if not run_dir.is_dir():
        return None
    return run_dir


@router.get("/{run_name}", response_model=PollinateTopicDetail)
async def pollinate_topic_detail(run_name: str):
    """One content topic's full detail. run_name validated + containment-checked
    against the Pollinate root before any FS read (traversal guard)."""
    from fastapi.responses import JSONResponse

    run_dir = _resolve_run_dir(run_name)
    if run_dir is None:
        return JSONResponse(status_code=404, content={"detail": "not found"})

    # 3 blocking FS ops (run.json read + asset dir walk + content_package.md read) —
    # run them together in ONE worker thread off the event loop (run_b2d3ece0).
    def _read_detail():
        run = _read_run_json(run_dir)
        assets = _walk_assets(run_dir)
        content_package = None
        cp = run_dir / "content_package.md"
        try:
            if cp.is_file():
                content_package = cp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content_package = None
        return run, assets, content_package

    run, assets, content_package = await asyncio.to_thread(_read_detail)
    topic = run_name
    domain = None
    status = "unknown"
    created_at = None
    if run:
        topic = run.get("topic") or run.get("message") or run_name
        domain = run.get("domain")
        status = run.get("status") or "unknown"
        created_at = run.get("created_at") or run.get("updated_at")

    return PollinateTopicDetail(
        run=run_name,
        topic=topic,
        domain=domain,
        status=status,
        created_at=created_at,
        content_package=content_package,
        assets=assets,
    )


@router.post("/{run_name}/publish", response_model=PublishResponse)
async def pollinate_mark_published(run_name: str, req: PublishRequest):
    """Mark ONE asset published/unpublished — the sole write path (P1). Persists to the
    per-run publish-state.json sidecar (atomic), keyed on the asset's stable logical id.
    Traversal-guarded: run_name via _resolve_run_dir containment, asset_id via 40-hex."""
    from fastapi.responses import JSONResponse

    run_dir = _resolve_run_dir(run_name)
    if run_dir is None:
        return JSONResponse(status_code=404, content={"detail": "not found"})
    if not _ASSET_ID_RE.match(req.asset_id or ""):
        return JSONResponse(status_code=422, content={"detail": "invalid asset_id (expect 40-hex sha1)"})

    state = _load_publish_state(run_dir)
    if req.published:
        state[req.asset_id] = {"published": True, "posted_url": req.posted_url or None}
        new_status, posted = "published", (req.posted_url or None)
    else:
        # Un-publish → drop the entry so the asset falls back to its kit/default status.
        state.pop(req.asset_id, None)
        new_status, posted = "ready", None
    try:
        _write_publish_state(run_dir, state)
    except OSError as e:
        logger.warning("pollinate: publish-state write failed for %s: %s", run_dir.name, e)
        return JSONResponse(status_code=500, content={"detail": "failed to persist publish state"})

    return PublishResponse(asset_id=req.asset_id, publish_status=new_status, posted_url=posted)
