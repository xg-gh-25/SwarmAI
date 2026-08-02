"""Pollinate Content-Asset Gallery — read-only router (sibling of routers/pipelines.py).

Two endpoints, both retrospective (fetch-once, no live state — chat is the live surface):
  GET /api/pollinate/assets        — newest-first content cards + overall rollup
  GET /api/pollinate/{run_name}    — one content topic's full detail

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
  * publish_status defaults to 'ready' (unknown); 'ready-to-publish' only when a
    publish-kit.md frontmatter says so; 'published' is never fabricated (only 1/3 kits
    even have frontmatter — verified).

Thumbnails are served by the EXISTING GET /api/workspace/file/raw (FileResponse,
workspace-sandboxed) — no new media endpoint. The frontend points <img> at it.
"""

from __future__ import annotations

import logging
import re as _re
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

# Image extensions that <img src=/api/workspace/file/raw> can render inline.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

# Known channel names → used to detect the nested deliver/{platform}/ layout and to
# normalize a bare file's platform to '' (the design's honest "unknown platform").
_KNOWN_PLATFORMS = {"xiaohongshu", "bilibili", "github", "gongzhonghao", "youtube", "twitter", "linkedin"}


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

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(swarmws))
        except ValueError:
            return str(p)

    def _add(f: Path, platform: str) -> None:
        if not f.is_file() or f.name.startswith("."):
            return
        ext = f.suffix.lower()
        assets.append(
            PollinateAsset(
                platform=platform,
                format=_classify_format(f.name),
                file_path=_rel(f),
                file_name=f.name,
                is_image=ext in _IMAGE_EXTS,
                publish_status=_publish_status_for(deliver, platform) if deliver.is_dir() else "ready",
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

    if _non_empty_dir(deliver):
        asset_root = deliver
    elif _non_empty_dir(run_dir / "tracks"):
        asset_root = run_dir / "tracks"
    else:
        asset_root = deliver  # (empty/absent) → falls to the bare-file branch below
    if asset_root.is_dir():
        for child in sorted(asset_root.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir():  # nested {platform-or-format}/{file}
                # In deliver/ the subdir is a platform; in tracks/ it's a format —
                # keep the subdir name as the label either way (honest, no guessing).
                label = child.name
                for f in sorted(child.iterdir()):
                    _add(f, label if label in _KNOWN_PLATFORMS else "")
            else:  # bare {root}/{file}
                _add(child, "")
    else:
        # No deliver/ or tracks/ — pick up bare media assets in the run dir root.
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


def _build_card(run_dir: Path, materialize: bool) -> Optional[PollinateContentCard]:
    """Build one content card. run.json is optional metadata; deliver/ walk is truth."""
    run_name = run_dir.name
    run = _read_run_json(run_dir)
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
        if run_meta is not None and not _is_terminal_pollinate(run_meta):
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


@router.get("/{run_name}", response_model=PollinateTopicDetail)
async def pollinate_topic_detail(run_name: str):
    """One content topic's full detail. run_name validated + containment-checked
    against the Pollinate root before any FS read (traversal guard)."""
    from fastapi.responses import JSONResponse

    if not _RUN_NAME_RE.match(run_name):
        return JSONResponse(status_code=404, content={"detail": "not found"})

    root = _get_swarmws() / _POLLINATE_ROOT
    run_dir = (root / run_name).resolve()
    # Containment: the resolved path MUST stay under the Pollinate root.
    try:
        run_dir.relative_to(root.resolve())
    except ValueError:
        return JSONResponse(status_code=404, content={"detail": "not found"})
    if not run_dir.is_dir():
        return JSONResponse(status_code=404, content={"detail": "not found"})

    run = _read_run_json(run_dir)
    assets = _walk_assets(run_dir)
    topic = run_name
    domain = None
    status = "unknown"
    created_at = None
    if run:
        topic = run.get("topic") or run.get("message") or run_name
        domain = run.get("domain")
        status = run.get("status") or "unknown"
        created_at = run.get("created_at") or run.get("updated_at")

    content_package = None
    cp = run_dir / "content_package.md"
    try:
        if cp.is_file():
            content_package = cp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        content_package = None

    return PollinateTopicDetail(
        run=run_name,
        topic=topic,
        domain=domain,
        status=status,
        created_at=created_at,
        content_package=content_package,
        assets=assets,
    )
