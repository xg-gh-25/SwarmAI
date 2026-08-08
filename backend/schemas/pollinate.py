"""Pydantic response models for the Pollinate Content-Asset Gallery.

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
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class PollinateAsset(BaseModel):
    """One produced deliverable file under `deliver/[{platform}/]{file}`."""

    platform: str = Field(description="xiaohongshu / bilibili / github / gongzhonghao / '' (bare)")
    format: str = Field(description="poster / narrative / video / caption / readme / qr / other")
    file_path: str = Field(description="workspace-relative path (for /api/workspace/file/raw)")
    file_name: str
    is_image: bool = Field(description="True → renderable via <img src=/api/workspace/file/raw>")
    publish_status: str = Field(
        default="ready",
        description="ready (default/unknown) | ready-to-publish (kit present) | published",
    )
    asset_id: str = Field(
        default="",
        description="Stable LOGICAL id = sha1(platform/format/file_name). The key the "
        "publish-state sidecar uses + the frontend passes to POST /publish. Stable across "
        "the deliver↔tracks root-flip (a physical-path key would orphan the mark).",
    )
    posted_url: Optional[str] = Field(
        default=None, description="The public URL where this asset was posted (set when published)."
    )


class PublishRequest(BaseModel):
    """`POST /api/pollinate/{run_name}/publish` body — mark one asset published/unpublished."""

    asset_id: str = Field(description="The asset's stable logical id (40-hex sha1).")
    published: bool = Field(description="True = mark published; False = revert to ready.")
    posted_url: Optional[str] = Field(
        default=None, max_length=2048, description="Optional public URL of the post (http/https only)."
    )

    @field_validator("posted_url")
    @classmethod
    def _validate_posted_url(cls, v: Optional[str]) -> Optional[str]:
        """Reject a non-http(s) URL. posted_url is stored verbatim and later rendered as an
        <a href> in the overlay — an un-scheme-checked value permits a stored javascript:/data:
        XSS (there is no CSP). Allow only http/https; empty → None. (Security-gate, 422 on fail.)"""
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        parts = urlsplit(v)
        # Require BOTH an http(s) scheme AND a network location (netloc) — i.e. the full
        # `http(s)://host` form. Checking scheme alone would accept `https:x` / `http:foo`
        # (no `//`), which the frontend render guard (/^https?:\/\//) rejects → a stored value
        # the UI silently won't render. Requiring netloc makes backend + frontend agree.
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            raise ValueError("posted_url must be a full http:// or https:// URL")
        return v


class PublishResponse(BaseModel):
    """`POST /api/pollinate/{run_name}/publish` result — the asset's new publish state."""

    asset_id: str
    publish_status: str = Field(description="'published' or 'ready' after the write.")
    posted_url: Optional[str] = None


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


class PollinateTopicDetail(BaseModel):
    """`GET /api/pollinate/{run_name}` — one content topic's full detail."""

    run: str
    topic: str
    domain: Optional[str] = None
    status: str = "unknown"
    created_at: Optional[str] = None
    content_package: Optional[str] = Field(default=None, description="content_package.md body if present")
    assets: list[PollinateAsset] = Field(default_factory=list)
