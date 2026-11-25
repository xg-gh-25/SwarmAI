"""Pydantic response models for the Pollinate overlay: the registered works
(`GET /api/pollinate/works`, Pollinate v2) and the older run gallery
(`GET /api/pollinate/assets`).

The run gallery:

Sibling of `schemas/pipeline_run.py`, but ASSET-CENTRIC: the first-class object is a
produced media asset (a file under a run's `deliver/` tree), not the run. A pollinate
run's `run.json` is optional metadata — the `deliver/` walk is the source of truth for
what was actually produced (verified: 5/14 run dirs have run.json, 10/14 have deliver/,
and `run.json.formats/platforms` are often None on running runs while the deliver/ tree
holds the real files). See the design doc:
Knowledge/Designs/2026-08-02-pollinate-content-asset-gallery-navcard-design.md
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PollinateAsset(BaseModel):
    """One produced deliverable file under `deliver/[{platform}/]{file}`."""

    platform: str = Field(description="xiaohongshu / bilibili / github / gongzhonghao / '' (bare)")
    format: str = Field(description="poster / narrative / video / caption / readme / qr / other")
    file_path: str = Field(description="workspace-relative path (for /api/workspace/file/raw)")
    file_name: str
    is_image: bool = Field(description="True → renderable via <img src=/api/workspace/file/raw>")
    publish_status: str = Field(
        default="ready",
        description="ready (default/unknown) | ready-to-publish | published — from a "
        "publish-kit.md frontmatter only",
    )


class PollinateContentCard(BaseModel):
    """One content topic (one pollinate run) + all the assets it produced."""

    run: str = Field(description="run dir name, e.g. 2026-05-03-memory-is-the-moat")
    topic: str = Field(description="topic || message || run dir name (fallback chain)")
    domain: Optional[str] = None
    status: str = Field(default="unknown", description="run.json status, or 'unknown' if no run.json")
    created_at: Optional[str] = None
    has_run_json: bool = Field(default=False, description="False → card synthesized from deliver/ only")
    asset_count: int = 0
    platforms: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    published_count: int = 0
    ready_count: int = 0
    assets: list[PollinateAsset] = Field(default_factory=list)


class PollinateOverall(BaseModel):
    """Top-of-gallery rollup across all content cards."""

    card_count: int = 0
    asset_count: int = 0
    platform_dist: dict[str, int] = Field(default_factory=dict)
    format_dist: dict[str, int] = Field(default_factory=dict)
    domain_dist: dict[str, int] = Field(default_factory=dict)
    published: int = 0
    ready: int = 0
    in_progress: int = Field(default=0, description="cards whose run status is running/review")
    known_channels: list[str] = Field(
        default_factory=list,
        description="The known-channel UNIVERSE (from the server's _KNOWN_PLATFORMS SSOT). "
        "Lets the Insights by-channel view surface a FULLY-neglected channel (0 assets "
        "anywhere) that platform_dist alone can never reveal — without the frontend "
        "hardcoding a drift-prone duplicate list.",
    )


class PollinateAssetsResponse(BaseModel):
    """`GET /api/pollinate/assets` — newest-first content cards + overall rollup.

    `overall` is computed over the WHOLE corpus (all counts honest). `cards` is the
    newest-N PAYLOAD slice (the cap bounds only the returned list size, not the
    rollup) — the frontend renders newest-first, so older cards beyond the cap are
    reflected in `overall` but not shipped as individual cards.
    """

    overall: PollinateOverall
    cards: list[PollinateContentCard] = Field(default_factory=list)


class PollinateWork(BaseModel):
    """One registered work (a folder with a work.json, design §13.1).

    Paths are workspace-relative when the work is inside the workspace (what
    /api/workspace/file/raw and the Canvas open take), absolute otherwise."""

    path: str = Field(description="the work folder")
    in_workspace: bool = Field(description="False → Canvas cannot open it; paths are absolute")
    kind: str = Field(description="deck | poster | page | copy (or what work.json says)")
    title: str
    current: Optional[str] = Field(
        default=None, description="current version id, e.g. v003 — the last version that passed")
    latest: Optional[str] = Field(
        default=None, description="the newest version id (the last cut, passed or not)")
    status: Optional[str] = Field(
        default=None,
        description="ok | failed — the NEWEST version's check result (a failed cut leaves "
        "`current` on the last passing version); None when unknown")
    updated: Optional[str] = Field(default=None, description="ISO time of the last change")
    thumbnail: Optional[str] = Field(
        default=None, description="out/slides/01-*.png, out/poster.png or out/page.png when present")
    main_output: Optional[str] = Field(
        default=None, description="the file to open: out/deck.html, out/poster.png, … when present")


class PollinateWorksResponse(BaseModel):
    """`GET /api/pollinate/works` — registered works, newest first."""

    works: list[PollinateWork] = Field(default_factory=list)
    skipped: int = Field(
        default=0, description="registry entries left out because a file was missing or broken")
