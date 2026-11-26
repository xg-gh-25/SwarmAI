"""Pydantic response models for the Pollinate overlay (design: the Pollinate overlay
mockup of 2026-11-26, Knowledge/Mockups/2026-11-26-pollinate-overlay-redesign.html):

- ``GET /api/pollinate/works`` — one row per registered work (PollinateWorksResponse);
- ``GET /api/pollinate/works/view?path=`` — one work's big card (PollinateWorkView);
- ``GET /api/pollinate/eval`` — the Eval tab (PollinateEval).

All of them are built by ``core/pollinate_works.py`` from the works' own files. Paths are
workspace-relative for a work inside the workspace (what /api/workspace/file/raw and the
Canvas open take), absolute otherwise.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PollinateOpenRevision(BaseModel):
    """The revision ``work.py open`` started and no passing cut has closed yet."""

    scope: list[str] = Field(default_factory=list, description="slide ids it may change")
    remove: list[str] = Field(default_factory=list, description="slide ids it may delete")
    add: int = 0
    note: Optional[str] = None
    at: Optional[str] = None
    base: Optional[str] = Field(default=None, description="the version the revision started from")
    session: Optional[str] = Field(default=None, description="the chat session that opened it")


class PollinateVersion(BaseModel):
    """One entry of work.json ``versions``, in the writer's order."""

    id: str
    by: str = Field(description="user | agent")
    status: Optional[str] = Field(default=None, description="ok | failed; None for a version with no check")
    at: Optional[str] = None
    scope: list[str] = Field(default_factory=list)
    base: Optional[str] = None
    note: Optional[str] = None
    found_on_disk: bool = Field(default=False, description="a hand edit absorbed by work.py open")
    session: Optional[str] = None


class PollinateProgress(BaseModel):
    """How far a video has got, read from its files."""

    stage: Optional[str] = Field(default=None, description="brief | storyboard | scenes | film; null with none of their files")
    scenes: int = Field(default=0, description="the storyboard's scene count")
    framed: int = Field(default=0, description="how many of those scenes have a frame")


class PollinateWork(BaseModel):
    """One registered work (a folder with a work.json)."""

    path: str = Field(description="the work folder")
    in_workspace: bool = Field(description="False → Canvas cannot open it; paths are absolute")
    kind: str = Field(description="deck | poster | page | copy | video")
    title: str
    brain: str = Field(description="the project of a work under Projects/<P>/, else General")
    style: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    slide_count: Optional[int] = Field(default=None, description="a deck's slide count")
    current: Optional[str] = Field(default=None, description="the last version that passed")
    latest: Optional[str] = Field(default=None, description="the newest version (the last cut, passed or not)")
    status: Optional[str] = Field(default=None, description="ok | failed — the newest version's result")
    updated: Optional[str] = Field(default=None, description="ISO time of the last change")
    open: Optional[PollinateOpenRevision] = None
    versions: list[PollinateVersion] = Field(default_factory=list)
    thumbnail: Optional[str] = Field(default=None, description="the cover image")
    main_output: Optional[str] = Field(default=None, description="the file to open: out/deck.html, an .mp4, …")
    duration_s: Optional[float] = Field(default=None, description="a video's length, from its storyboard")
    scene_count: Optional[int] = Field(default=None, description="a video's scene count")
    in_progress: bool = Field(default=False, description="being made: an open revision or no passed version yet; a video with no film")
    progress: Optional[PollinateProgress] = Field(default=None, description="a video's stage; null for other kinds")
    session: Optional[str] = Field(default=None, description="the chat that registered a video")


class PollinateWorksResponse(BaseModel):
    """``GET /api/pollinate/works`` — registered works, newest first."""

    works: list[PollinateWork] = Field(default_factory=list)
    skipped: int = Field(default=0, description="registry entries left out because a file was missing or broken")


class PollinateVersionDetail(PollinateVersion):
    guard_ok: Optional[bool] = Field(default=None, description="the guard's result for this cut")
    fail_counts: dict[str, int] = Field(default_factory=dict, description="fail-level findings per check")


class PollinateFinding(BaseModel):
    check: str
    level: str = Field(description="fail | warn")
    slide: Optional[str] = None
    msg: Optional[str] = None
    carried: bool = Field(default=False, description="the slide already had it before this version")


class PollinateGuardViolation(BaseModel):
    slide: Optional[str] = None
    kind: Optional[str] = None
    detail: Optional[str] = None


class PollinateGuard(BaseModel):
    ok: bool
    violations: list[PollinateGuardViolation] = Field(default_factory=list)


class PollinateOutput(BaseModel):
    path: Optional[str] = None
    name: str
    bytes: Optional[int] = None
    files: Optional[int] = Field(default=None, description="the file count of a folder (out/slides/)")


class PollinateSlide(BaseModel):
    n: int
    id: str
    image: Optional[str] = None
    changed: bool = Field(default=False, description="in the open revision's scope, else the newest version's")


class PollinateScene(BaseModel):
    id: str
    title: Optional[str] = None
    start_s: float = 0
    duration_s: Optional[float] = None
    image: Optional[str] = None


class PollinateAction(BaseModel):
    key: str = Field(description="open_canvas | open_session | continue_in_chat")
    primary: bool = False
    path: Optional[str] = None
    session: Optional[str] = None


class PollinateWorkView(BaseModel):
    """``GET /api/pollinate/works/view`` — one work's big card."""

    work: PollinateWork
    versions: list[PollinateVersionDetail] = Field(default_factory=list)
    findings: list[PollinateFinding] = Field(default_factory=list, description="the newest version's checks")
    guard: Optional[PollinateGuard] = None
    brief: Optional[str] = Field(default=None, description="brief.md as written")
    outputs: list[PollinateOutput] = Field(default_factory=list)
    slides: list[PollinateSlide] = Field(default_factory=list)
    scenes: list[PollinateScene] = Field(default_factory=list)
    session: Optional[str] = None
    actions: list[PollinateAction] = Field(default_factory=list, description="in order; the first is primary")


class PollinateRate(BaseModel):
    passed: int
    revisions: int
    pct: int


class PollinatePair(BaseModel):
    cur: Optional[float] = None
    prev: Optional[float] = None


class PollinateRatePair(BaseModel):
    cur: Optional[PollinateRate] = None
    prev: Optional[PollinateRate] = None


class PollinateDay(BaseModel):
    date: str
    first_pass: int = 0
    later: int = 0
    not_passed: int = 0


class PollinateCheckHit(BaseModel):
    check: str
    count: int
    top_title: str
    top_count: int


class PollinateGuardBlock(BaseModel):
    path: str
    title: str
    version: str
    at: Optional[str] = None
    violations: list[str] = Field(default_factory=list)


class PollinateEval(BaseModel):
    """``GET /api/pollinate/eval`` — the last ``days`` days against the ``days`` before."""

    days: int
    now: str
    rate: PollinateRatePair
    daily: list[PollinateDay] = Field(default_factory=list, description="2×days entries, oldest first")
    works_changed: PollinatePair
    versions_median: PollinatePair
    person_versions: PollinatePair
    guard_blocks: PollinatePair
    check_hits: list[PollinateCheckHit] = Field(default_factory=list)
    guard_list: list[PollinateGuardBlock] = Field(default_factory=list)
