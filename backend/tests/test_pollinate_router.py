"""Tests for the /api/pollinate router: GET /works (registered works, Pollinate v2)
and GET /assets (the older run gallery).

/works: every field read from the registry and work.json; broken files skipped and
counted, never a 500; works outside the workspace; both handlers run their file work
on the nav pool, not on the event loop. The old detail and publish routes are gone.

/assets covers the Gate-1 findings that shaped the design (each asserts a real data reality):
- BLOCK-1: deliver/ walk is the source of truth — a run with NO run.json still yields a
  card from its deliver/ tree; a run whose run.json.formats/platforms is None but whose
  deliver/ has real files still surfaces those assets; the bare `deliver/{file}` layout
  (no platform subdir) is handled alongside `deliver/{platform}/{file}`.
- BLOCK-2: pollinate-local terminal check — a run with status='review' whose stages use
  name='REFLECT'(UPPERCASE)/status='done' is terminal (the pipeline is_terminal_run would
  miss it).
- HIGH: publish_status defaults to 'ready' when no publish-kit/frontmatter; only a
  `status: ready-to-publish` frontmatter upgrades it; 'published' is never fabricated.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_test_workspace = None


@pytest.fixture
def workspace(tmp_path):
    global _test_workspace
    _test_workspace = tmp_path
    (tmp_path / "Knowledge" / "Pollinate").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def client(workspace):
    with patch("routers.pollinate._get_swarmws", return_value=workspace):
        from main import app
        yield TestClient(app)


def _mk_run(ws: Path, name: str, *, run_json: dict | None = None, deliver: dict | None = None,
            bare_assets: list[str] | None = None, publish_kits: dict | None = None) -> Path:
    """Create a pollinate run dir.
    deliver: {platform_or_'': [file_names]} → deliver/{platform}/{file} (or deliver/{file} when key='')
    bare_assets: files directly in the run dir (no deliver/)
    publish_kits: {platform: frontmatter_status_or_None} → writes a publish-kit.md
    """
    d = ws / "Knowledge" / "Pollinate" / name
    d.mkdir(parents=True, exist_ok=True)
    if run_json is not None:
        (d / "run.json").write_text(json.dumps(run_json), encoding="utf-8")
    if deliver is not None:
        dv = d / "deliver"
        dv.mkdir(exist_ok=True)
        for platform, files in deliver.items():
            target = dv / platform if platform else dv
            target.mkdir(parents=True, exist_ok=True)
            for fn in files:
                (target / fn).write_bytes(b"\x89PNG\r\n" if fn.endswith(".png") else b"hi")
        if publish_kits:
            for platform, status in publish_kits.items():
                target = (dv / platform) if platform else dv
                target.mkdir(parents=True, exist_ok=True)
                if status is None:
                    (target / "publish-kit.md").write_text("# no frontmatter kit\nbody", encoding="utf-8")
                else:
                    (target / "publish-kit.md").write_text(
                        f"---\ntitle: x\nstatus: {status}\n---\nbody", encoding="utf-8")
    if bare_assets:
        for fn in bare_assets:
            (d / fn).write_bytes(b"\x89PNG\r\n")
    return d


# ---------- BLOCK-1: deliver/ walk is source of truth ----------

def test_run_without_run_json_still_yields_card(client, workspace):
    """A dir with NO run.json but a deliver/ tree must surface as a content card."""
    _mk_run(workspace, "2026-05-26-no-runjson",
            deliver={"xiaohongshu": ["poster_3x4.png", "caption.txt"]})
    r = client.get("/api/pollinate/assets")
    assert r.status_code == 200
    cards = r.json()["cards"]
    card = next(c for c in cards if c["run"] == "2026-05-26-no-runjson")
    assert card["has_run_json"] is False
    assert card["asset_count"] == 2
    assert card["topic"] == "2026-05-26-no-runjson"  # falls back to dir name


def test_none_formats_but_real_deliver_tree_surfaces_assets(client, workspace):
    """run.json.formats/platforms=None (a running run) but deliver/ has real files →
    the deliver walk wins (the KEY design claim, AC7)."""
    _mk_run(workspace, "2026-04-26-v2-launch",
            run_json={"topic": "v2 launch", "status": "running",
                      "formats": None, "platforms": None, "created_at": "2026-04-26T10:00:00"},
            deliver={"bilibili": ["poster_16x9.png"], "github": ["readme_section.md"],
                     "xiaohongshu": ["poster_3x4.png"]})
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-04-26-v2-launch")
    assert card["asset_count"] == 3
    assert set(card["platforms"]) == {"bilibili", "github", "xiaohongshu"}


def test_bare_deliver_layout_no_platform_subdir(client, workspace):
    """deliver/{file} (bare, no platform subdir) is handled (2026-07-04-pollinate-xhs)."""
    _mk_run(workspace, "2026-07-04-bare",
            deliver={"": ["poster_3x4.png", "platform_matrix.md"]})
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-07-04-bare")
    assert card["asset_count"] == 2  # both content (a QR file here would be noise-filtered)
    assert card["platforms"] == []  # honest: no platform known for bare files


def test_flat_run_dir_with_bare_images_no_deliver_is_scratch_excluded(client, workspace):
    """SPEC CHANGE (P3, run_a712b0be): a dir of loose images with NO run.json AND NO
    deliver/ AND NO tracks/ is a SCRATCH bucket (posters/, 2026-04-26-pollinate-poster/),
    NOT a produced topic — it must NOT render as a content card (previously it did, which
    made a 23-image scratch pile the largest card and diluted the gallery)."""
    _mk_run(workspace, "2026-04-26-poster", bare_assets=["poster_3x4.png"])
    r = client.get("/api/pollinate/assets")
    runs = [c["run"] for c in r.json()["cards"]]
    assert "2026-04-26-poster" not in runs


def test_bare_images_WITH_run_json_still_a_card(client, workspace):
    """Guard the scratch rule's boundary: loose images are excluded ONLY when there is no
    run.json — a run.json makes it a real (if unusual) topic, so it is KEPT."""
    _mk_run(workspace, "2026-04-27-hasjson", bare_assets=["poster_3x4.png"],
            run_json={"topic": "legit", "status": "completed", "created_at": "2026-04-27T00:00:00"})
    r = client.get("/api/pollinate/assets")
    runs = [c["run"] for c in r.json()["cards"]]
    assert "2026-04-27-hasjson" in runs


def test_tracks_layout_surfaces_assets(client, workspace):
    """tracks/{format}/{file} is an alternate real layout (swarmai-social-series):
    used when deliver/ is absent."""
    d = workspace / "Knowledge" / "Pollinate" / "2026-05-16-tracks"
    (d / "tracks" / "poster").mkdir(parents=True)
    (d / "tracks" / "poster" / "full-series.png").write_bytes(b"\x89PNG\r\n")
    (d / "tracks" / "poster" / "spec.md").write_text("x")
    (d / "run.json").write_text(json.dumps(
        {"topic": "series", "status": "running", "created_at": "2026-05-16T00:00:00"}))
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-05-16-tracks")
    assert card["asset_count"] == 2


def test_dateless_dir_sorts_last(client, workspace):
    """A dateless content dir must NOT bury dated newest content. (Uses a deliver-backed
    dateless dir — a bare-image dateless dir is now a scratch bucket, excluded entirely.)"""
    _mk_run(workspace, "dateless-topic", deliver={"": ["a.png", "b.png"]})
    _mk_run(workspace, "2026-08-01-recent",
            run_json={"topic": "recent", "status": "completed", "created_at": "2026-08-01T00:00:00"},
            deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    runs = [c["run"] for c in r.json()["cards"]]
    assert runs.index("2026-08-01-recent") < runs.index("dateless-topic")


def test_empty_deliver_no_runjson_dropped(client, workspace):
    """An empty deliver/ with no run.json is noise — dropped from the gallery."""
    d = workspace / "Knowledge" / "Pollinate" / "2026-07-13-empty"
    (d / "deliver").mkdir(parents=True)
    r = client.get("/api/pollinate/assets")
    runs = [c["run"] for c in r.json()["cards"]]
    assert "2026-07-13-empty" not in runs


# ---------- BLOCK-2: pollinate-local terminal check ----------

def test_review_status_with_uppercase_reflect_is_terminal():
    from routers.pollinate import _is_terminal_pollinate
    run = {"status": "review", "stages": [
        {"name": "EVALUATE", "status": "done"},
        {"name": "REFLECT", "status": "done"},
    ]}
    assert _is_terminal_pollinate(run) is True


def test_running_midpipeline_not_terminal():
    from routers.pollinate import _is_terminal_pollinate
    run = {"status": "running", "stages": [{"name": "EVALUATE", "status": "done"},
                                           {"name": "BUILD", "status": "running"}]}
    assert _is_terminal_pollinate(run) is False


def test_completed_status_is_terminal():
    from routers.pollinate import _is_terminal_pollinate
    assert _is_terminal_pollinate({"status": "completed", "stages": []}) is True


# ---------- HIGH: publish_status honesty ----------

def test_publish_status_defaults_ready_without_kit(client, workspace):
    _mk_run(workspace, "2026-05-03-nokit",
            deliver={"xiaohongshu": ["poster_3x4.png"]})
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-05-03-nokit")
    assert all(a["publish_status"] == "ready" for a in card["assets"])


def test_publish_kit_frontmatter_upgrades_to_ready_to_publish(client, workspace):
    _mk_run(workspace, "2026-04-26-kit",
            deliver={"xiaohongshu": ["poster_3x4.png", "caption.txt"]},
            publish_kits={"xiaohongshu": "ready-to-publish"})
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-04-26-kit")
    xhs = [a for a in card["assets"] if a["platform"] == "xiaohongshu"]
    assert any(a["publish_status"] == "ready-to-publish" for a in xhs)


def test_publish_kit_without_frontmatter_stays_ready(client, workspace):
    _mk_run(workspace, "2026-04-29-kitnofm",
            deliver={"xiaohongshu": ["poster_3x4.png"]},
            publish_kits={"xiaohongshu": None})
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-04-29-kitnofm")
    assert all(a["publish_status"] == "ready" for a in card["assets"])


# ---------- newest-first ordering ----------

def test_cards_sorted_newest_first(client, workspace):
    _mk_run(workspace, "old", run_json={"topic": "old", "status": "completed",
                                        "created_at": "2026-01-01T00:00:00"},
            deliver={"xiaohongshu": ["a.png"]})
    _mk_run(workspace, "new", run_json={"topic": "new", "status": "completed",
                                        "created_at": "2026-09-01T00:00:00"},
            deliver={"xiaohongshu": ["b.png"]})
    r = client.get("/api/pollinate/assets")
    runs = [c["run"] for c in r.json()["cards"]]
    assert runs.index("new") < runs.index("old")


# ---------- in_progress uses the terminal check (Gate-2: wire BLOCK-2) ----------

def test_in_progress_excludes_review_run_with_reflect_done(client, workspace):
    """A status='review' run whose REFLECT stage is done is TERMINAL → NOT in_progress
    (raw status-string check would wrongly count it)."""
    _mk_run(workspace, "2026-06-01-reviewdone",
            run_json={"topic": "t", "status": "review", "created_at": "2026-06-01T00:00:00",
                      "stages": [{"name": "EVALUATE", "status": "done"},
                                 {"name": "REFLECT", "status": "done"}]},
            deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    assert r.json()["overall"]["in_progress"] == 0


def test_no_runjson_dir_not_counted_in_progress(client, workspace):
    """A dir with assets but NO run.json is DONE (assets exist), not in-progress —
    an unknown status must never inflate the in-progress count."""
    _mk_run(workspace, "2026-06-03-norunjson",
            deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    assert r.json()["overall"]["in_progress"] == 0


def test_in_progress_counts_genuinely_running(client, workspace):
    import time
    recent = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 2 * 86400))
    _mk_run(workspace, "2026-06-02-running",
            run_json={"topic": "t", "status": "running", "created_at": recent,
                      "stages": [{"name": "BUILD", "status": "running"}]},
            deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    assert r.json()["overall"]["in_progress"] == 1  # recent + running → counted


# ---------- overall rollup ----------

def test_overall_rollup_counts(client, workspace):
    _mk_run(workspace, "r1", run_json={"topic": "t1", "status": "completed", "domain": "ai",
                                       "created_at": "2026-05-01T00:00:00"},
            deliver={"xiaohongshu": ["p.png"], "bilibili": ["v.png"]})
    r = client.get("/api/pollinate/assets")
    o = r.json()["overall"]
    assert o["card_count"] >= 1
    assert o["asset_count"] >= 2
    assert "xiaohongshu" in o["platform_dist"]



def test_overall_exposes_known_channels(client, workspace):
    """overall.known_channels carries the server SSOT channel universe so the frontend
    can grey out fully-neglected channels without hardcoding a drift-prone list."""
    _mk_run(workspace, "2026-06-10-kc", deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    kc = r.json()["overall"]["known_channels"]
    assert "xiaohongshu" in kc and "youtube" in kc  # incl a channel with 0 assets
    assert kc == sorted(kc)  # stable order


def test_is_noise_asset_unit():
    from routers.pollinate import _is_noise_asset
    # build toolchain / manifests / locks → noise
    for junk in ("build_deck.py", "build_deck.js", "render_visuals.js", "package.json",
                 "package-lock.json", "notes.json", "discovery.json", "setup.sh", "app.mjs"):
        assert _is_noise_asset(junk) is True, junk
    # QR images → noise (attachment, not content)
    for qr in ("qr-github.png", "xhs_qr.png", "QR-Github-Light.PNG"):
        assert _is_noise_asset(qr) is True, qr
    # genuine content deliverables → NOT noise (spares .md/.html metadata too)
    for real in ("poster_3x4.png", "slide-01-cover.html", "content_package.md",
                 "platform_matrix.md", "narrative.txt", "clip.mp4", "audio.wav",
                 "series.pdf", "deck.pptx", "captions.srt"):
        assert _is_noise_asset(real) is False, real


def test_deck_build_junk_excluded_from_assets(client, workspace):
    """A tracks/deck/ card carrying build scripts + manifests alongside real content:
    only the content survives; build_deck.py/.js/package.json/lockfile are dropped, and
    'other' in format_dist no longer counts toolchain."""
    d = workspace / "Knowledge" / "Pollinate" / "2026-05-26-three-layer-governance"
    deck = d / "tracks" / "deck"
    deck.mkdir(parents=True)
    for real in ("three-layer-governance.pptx", "outline.md"):
        (deck / real).write_bytes(b"content")
    (deck / "slide_01.png").write_bytes(b"\x89PNG\r\n")
    for junk in ("build_deck.py", "build_deck_v2.py", "build_deck.js", "render_visuals.js",
                 "package.json", "package-lock.json", "notes.json"):
        (deck / junk).write_text("x")
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-05-26-three-layer-governance")
    names = {a["file_name"] for a in card["assets"]}
    assert names == {"three-layer-governance.pptx", "outline.md", "slide_01.png"}
    assert card["asset_count"] == 3
    assert "package.json" not in names and "build_deck.py" not in names


def test_qr_not_a_content_format_in_grid(client, workspace):
    """QR images are excluded → 'qr' never appears as a content format in the rollup."""
    _mk_run(workspace, "2026-05-01-qr",
            deliver={"xiaohongshu": ["poster_3x4.png", "qr-github.png", "xhs_qr.png"]})
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-05-01-qr")
    assert card["asset_count"] == 1  # only the poster
    assert "qr" not in r.json()["overall"]["format_dist"]


def test_node_modules_never_ingested_regression(client, workspace):
    """Gate-1 must-fix: a node_modules/ under a walked format dir must contribute 0 assets.
    Today _walk_assets is depth-2 so node_modules (3+ deep) is unreached; this pins it so a
    future depth change can't silently ingest the npm tree (~250 .js). Belt: even IF reached,
    every .js/.json inside is _is_noise_asset → dropped."""
    d = workspace / "Knowledge" / "Pollinate" / "2026-05-26-nm"
    nm = d / "tracks" / "deck" / "node_modules" / "left-pad"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports=1")
    (nm / "package.json").write_text("{}")
    (d / "tracks" / "deck" / "real.png").write_bytes(b"\x89PNG\r\n")
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-05-26-nm")
    names = {a["file_name"] for a in card["assets"]}
    assert names == {"real.png"}  # zero node_modules files
    assert "index.js" not in names


# ---------- P3: scratch-dir exclusion ----------

def test_is_scratch_dir_unit(workspace):
    from routers.pollinate import _is_scratch_dir
    P = workspace / "Knowledge" / "Pollinate"
    # loose images, no run.json/deliver/tracks → scratch
    scratch = P / "posters"
    scratch.mkdir(parents=True)
    (scratch / "a.png").write_bytes(b"\x89PNG\r\n")
    assert _is_scratch_dir(scratch) is True
    # has run.json → not scratch
    hasjson = P / "with-json"
    hasjson.mkdir()
    (hasjson / "run.json").write_text("{}")
    (hasjson / "a.png").write_bytes(b"\x89PNG\r\n")
    assert _is_scratch_dir(hasjson) is False
    # has deliver/ → not scratch
    hasdeliver = P / "with-deliver"
    (hasdeliver / "deliver").mkdir(parents=True)
    (hasdeliver / "deliver" / "a.png").write_bytes(b"\x89PNG\r\n")
    assert _is_scratch_dir(hasdeliver) is False
    # has tracks/ → not scratch
    hastracks = P / "with-tracks"
    (hastracks / "tracks" / "poster").mkdir(parents=True)
    (hastracks / "tracks" / "poster" / "a.png").write_bytes(b"\x89PNG\r\n")
    assert _is_scratch_dir(hastracks) is False


def test_scratch_dir_not_the_largest_card(client, workspace):
    """A big loose-image scratch pile (posters) must not appear as (the largest) card,
    while a real deliver-backed topic does."""
    posters = workspace / "Knowledge" / "Pollinate" / "posters"
    posters.mkdir(parents=True)
    for i in range(23):
        (posters / f"iter-{i}.png").write_bytes(b"\x89PNG\r\n")
    _mk_run(workspace, "2026-08-01-real",
            run_json={"topic": "real", "status": "completed", "created_at": "2026-08-01T00:00:00"},
            deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    runs = [c["run"] for c in r.json()["cards"]]
    assert "posters" not in runs
    assert "2026-08-01-real" in runs


# ---------- P4: staleness gate on in_progress ----------

def test_run_is_stale_unit(workspace):
    from routers.pollinate import _run_is_stale
    import time
    now = time.time()
    old = "2026-01-01T00:00:00"     # >30d before any 2026-08 now
    recent_ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 5 * 86400))
    d = workspace / "Knowledge" / "Pollinate" / "x"
    d.mkdir(parents=True)
    # old timestamp → stale
    assert _run_is_stale({"status": "running", "created_at": old}, d, now=now) is True
    # fresh updated_at → NOT stale (even if created long ago)
    assert _run_is_stale({"status": "running", "created_at": old, "updated_at": recent_ts}, d, now=now) is False


def test_fresh_updated_at_beats_old_dir_mtime(client, workspace):
    """Gate-1 must-fix (run_a16d61ad class): a run.json with a FRESH updated_at must count
    as in_progress even if the dir mtime is old — run.json ts is PRIMARY, dir mtime fallback
    only. (Prevents in_progress silently collapsing to 0 after a bulk FS touch.)"""
    import time, os
    recent = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3 * 86400))
    d = _mk_run(workspace, "2026-01-01-old-dir-fresh-run",
                run_json={"topic": "t", "status": "running", "created_at": "2026-01-01T00:00:00",
                          "updated_at": recent, "stages": [{"name": "BUILD", "status": "running"}]},
                deliver={"xiaohongshu": ["p.png"]})
    old_epoch = time.time() - 200 * 86400
    os.utime(d, (old_epoch, old_epoch))  # force ancient dir mtime
    r = client.get("/api/pollinate/assets")
    assert r.json()["overall"]["in_progress"] == 1  # fresh updated_at wins


def test_stale_running_run_not_counted_in_progress(client, workspace):
    """A non-terminal run whose only timestamp is months old is NOT counted in_progress
    (still renders as a card with its real status — just not in the rollup)."""
    _mk_run(workspace, "2026-04-26-stale-running",
            run_json={"topic": "t", "status": "running", "created_at": "2026-04-26T00:00:00",
                      "stages": [{"name": "BUILD", "status": "running"}]},
            deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    assert r.json()["overall"]["in_progress"] == 0
    # but the card still renders
    assert any(c["run"] == "2026-04-26-stale-running" for c in r.json()["cards"])


# ---------- P5b: tracks format label + honest status ----------

def test_tracks_subdir_names_the_format(client, workspace):
    """tracks/deck/ files that the filename classifier can't type (outline.md, speaker_notes.md)
    read as 'deck' (the subdir format), not 'other'."""
    d = workspace / "Knowledge" / "Pollinate" / "2026-05-26-deck"
    deck = d / "tracks" / "deck"
    deck.mkdir(parents=True)
    (deck / "outline.md").write_text("x")
    (deck / "speaker_notes.md").write_text("x")
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-05-26-deck")
    assert all(a["format"] == "deck" for a in card["assets"])


def test_no_runjson_but_assets_status_completed(client, workspace):
    """A produced topic with no run.json reads status='completed' (its deliverables exist),
    not 'unknown' — so the frontend's running/review-keyed 'Produce more' won't offer a rerun."""
    _mk_run(workspace, "2026-05-17-nojson-done",
            deliver={"xiaohongshu": ["poster_3x4.png"]})
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-05-17-nojson-done")
    assert card["status"] == "completed"


# ---------- Gate-2 HIGH: podcast/shorts layout (video/ + shorts/) is a REAL topic ----------

def test_podcast_shorts_layout_is_a_real_card_not_scratch(client, workspace):
    """The aidlc-one-sentence-to-pr layout: NO run.json, NO deliver/, NO tracks/ — media lives
    in video/ (flat) + shorts/<name>/ (nested), plus content_package.md/REPORT.md markers.
    This is a produced topic and MUST render (Gate-2 meta-review HIGH: it was being false-excluded
    as scratch). The subdir names video/shorts are the formats."""
    d = workspace / "Knowledge" / "Pollinate" / "2026-04-26-aidlc-one-sentence-to-pr"
    (d / "video").mkdir(parents=True)
    (d / "video" / "podcast_audio.wav").write_bytes(b"RIFF")
    (d / "video" / "thumbnail_16x9.png").write_bytes(b"\x89PNG\r\n")
    (d / "video" / "podcast_audio.srt").write_text("1\n")
    (d / "video" / "bgm.mp3").write_bytes(b"ID3")
    (d / "shorts" / "short_crash_story").mkdir(parents=True)
    (d / "shorts" / "short_crash_story" / "short_audio.wav").write_bytes(b"RIFF")
    (d / "content_package.md").write_text("the message")
    (d / "REPORT.md").write_text("report")
    r = client.get("/api/pollinate/assets")
    cards = r.json()["cards"]
    card = next((c for c in cards if c["run"] == "2026-04-26-aidlc-one-sentence-to-pr"), None)
    assert card is not None, "podcast/shorts topic must NOT be excluded as scratch"
    names = {a["file_name"] for a in card["assets"]}
    assert "podcast_audio.wav" in names       # flat video/ file surfaced
    assert "short_audio.wav" in names          # nested shorts/<name>/ file surfaced
    # audio/srt files (classifier returns 'other') take the subdir format hint; an image
    # thumbnail correctly stays 'poster' (a real content format), not overridden by the hint.
    fmt_by_name = {a["file_name"]: a["format"] for a in card["assets"]}
    assert fmt_by_name["podcast_audio.wav"] == "video"
    assert fmt_by_name["short_audio.wav"] == "shorts"
    assert fmt_by_name["thumbnail_16x9.png"] == "poster"
    assert card["status"] == "completed"


def test_is_scratch_dir_spares_topic_markers(workspace):
    """_is_scratch_dir must NOT flag a dir carrying content_package.md/REPORT.md or a media
    subdir, even with no run.json/deliver/tracks."""
    from routers.pollinate import _is_scratch_dir
    P = workspace / "Knowledge" / "Pollinate"
    # content_package.md marker → not scratch
    m = P / "marker-topic"; m.mkdir(parents=True)
    (m / "content_package.md").write_text("x"); (m / "loose.png").write_bytes(b"\x89PNG\r\n")
    assert _is_scratch_dir(m) is False
    # video/ media subdir → not scratch
    v = P / "video-topic"; (v / "video").mkdir(parents=True)
    (v / "video" / "a.wav").write_bytes(b"RIFF")
    assert _is_scratch_dir(v) is False
    # truly loose images, no marker/subdir → still scratch
    s = P / "posters2"; s.mkdir(parents=True); (s / "x.png").write_bytes(b"\x89PNG\r\n")
    assert _is_scratch_dir(s) is True


# ================= Pollinate v2 (design 2026-11-25 §9, §10, §13.1) =================

import threading  # noqa: E402 — used only by the tests below


def _write_work(work_dir: Path, *, kind="deck", title="T", current="v001",
                versions=(("v001", "ok", "2026-11-20T10:00:00Z"),), files=()) -> Path:
    """A work folder with a work.json (the §13.1 shape) and the given output files.
    `versions` is (id, status, at) in the order the writer appended them; as work.py
    cut does (§13.2), `current` names the last version that passed."""
    work_dir.mkdir(parents=True, exist_ok=True)
    meta = {"schema": 1, "kind": kind, "title": title, "style": None,
            "size": {"w": 1920, "h": 1080}, "src": "src/deck.html", "slides": ["s-7f3a"],
            "current": current,
            "versions": [{"id": vid, "at": vat, "by": "agent", "status": vst,
                          "hashes": {}, "outputs": {}} for vid, vst, vat in versions]}
    (work_dir / "work.json").write_text(json.dumps(meta), encoding="utf-8")
    for rel in files:
        f = work_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    return work_dir


def _write_registry(ws: Path, entries) -> None:
    reg = ws / ".pollinate" / "works.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"schema": 1, "works": entries}), encoding="utf-8")


def test_works_lists_registered_works(client, workspace):
    # the realistic failed case: the newest cut (v003) failed, current stays on v002
    deck = _write_work(workspace / "Projects" / "AIDLC" / "assets" / "ai-deck", kind="deck",
                       title="AI deck", current="v002",
                       versions=(("v001", "ok", "2026-11-20T00:00:00Z"), ("v002", "ok", "2026-11-21T00:00:00Z"),
                                 ("v003", "failed", "2026-11-25T07:59:00Z")),
                       files=["out/deck.html", "out/deck.pdf",
                              "out/slides/01-s-7f3a.png", "out/slides/02-s-91c0.png"])
    poster = _write_work(workspace / "Knowledge" / "Pollinate" / "2026-11-24-launch", kind="poster",
                         title="Launch poster", current="v001",
                         files=["out/poster.png"])
    page = _write_work(workspace / "Knowledge" / "Pollinate" / "2026-11-01-report", kind="page",
                       title="Report", current=None, versions=(), files=[])
    _write_registry(workspace, [
        {"path": str(poster), "kind": "poster", "title": "x", "updated": "2026-11-24T09:00:00Z"},
        {"path": str(deck), "kind": "deck", "title": "x", "updated": "2026-11-25T08:00:00Z"},
        {"path": str(page), "kind": "page", "title": "x"},  # no date anywhere → last
    ])
    r = client.get("/api/pollinate/works")
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] == 0
    assert [w["title"] for w in body["works"]] == ["AI deck", "Launch poster", "Report"]
    d, p, g = body["works"]
    assert d == {
        "path": "Projects/AIDLC/assets/ai-deck", "in_workspace": True, "kind": "deck",
        "title": "AI deck", "current": "v002", "latest": "v003", "status": "failed",
        "updated": "2026-11-25T08:00:00Z",
        "thumbnail": "Projects/AIDLC/assets/ai-deck/out/slides/01-s-7f3a.png",
        "main_output": "Projects/AIDLC/assets/ai-deck/out/deck.html",
    }
    assert p["status"] == "ok" and p["current"] == "v001" and p["latest"] == "v001"
    assert p["thumbnail"] == p["main_output"] == "Knowledge/Pollinate/2026-11-24-launch/out/poster.png"
    # a work never cut: no version, no outputs → nulls, still listed
    assert g["current"] is None and g["latest"] is None and g["status"] is None
    assert g["updated"] is None
    assert g["thumbnail"] is None and g["main_output"] is None


def test_works_main_output_falls_back_and_updated_from_version(client, workspace):
    """A deck with only a PDF opens the PDF; with no registry date, updated is the current
    version's time."""
    deck = _write_work(workspace / "w" / "pdf-only", kind="deck",
                       versions=(("v001", "ok", "2026-11-02T00:00:00Z"),), files=["out/deck.pdf"])
    _write_registry(workspace, [{"path": str(deck), "kind": "deck", "title": "t"}])
    w = client.get("/api/pollinate/works").json()["works"][0]
    assert w["main_output"] == "w/pdf-only/out/deck.pdf"
    assert w["thumbnail"] is None
    assert w["updated"] == "2026-11-02T00:00:00Z"


def test_works_status_is_the_newest_versions(client, workspace):
    """current only moves on a passing cut (§13.2), so the mark must come from the newest
    version: a passing cut after a failed one clears it; a first cut that failed sets it
    with no current yet; a newest version with no check result (a -user version made
    by work.py open) gives no status."""
    cases = {
        "fixed": dict(current="v003", versions=(("v002", "failed", "2026-11-02T00:00:00Z"),
                                                ("v003", "ok", "2026-11-03T00:00:00Z"))),
        "first-failed": dict(current=None, versions=(("v001", "failed", "2026-11-04T00:00:00Z"),)),
        "user-opened": dict(current="v001", versions=(("v001", "ok", "2026-11-05T00:00:00Z"),
                                                      ("v001-user", None, "2026-11-06T00:00:00Z"))),
    }
    _write_registry(workspace, [
        {"path": str(_write_work(workspace / "w" / name, **kw))} for name, kw in cases.items()])
    rows = {w["path"]: w for w in client.get("/api/pollinate/works").json()["works"]}
    assert (rows["w/fixed"]["status"], rows["w/fixed"]["latest"]) == ("ok", "v003")
    assert (rows["w/first-failed"]["status"], rows["w/first-failed"]["current"],
            rows["w/first-failed"]["latest"]) == ("failed", None, "v001")
    assert (rows["w/user-opened"]["status"], rows["w/user-opened"]["latest"]) == (None, "v001-user")


def test_works_skips_and_counts_broken_entries(client, workspace):
    good = _write_work(workspace / "w" / "good", kind="poster", files=["out/poster.png"])
    no_json = workspace / "w" / "no-json"
    no_json.mkdir(parents=True)
    bad_json = workspace / "w" / "bad-json"
    bad_json.mkdir(parents=True)
    (bad_json / "work.json").write_text("{ not json", encoding="utf-8")
    list_json = workspace / "w" / "list-json"
    list_json.mkdir(parents=True)
    (list_json / "work.json").write_text("[1, 2]", encoding="utf-8")
    _write_registry(workspace, [
        {"path": str(workspace / "w" / "missing")},   # folder gone
        {"path": str(no_json)},                       # no work.json
        {"path": str(bad_json)},                      # not JSON
        {"path": str(list_json)},                     # JSON, not an object
        {"path": "w/good"},                           # relative: §13.1 wants absolute
        "not-an-object",
        {"kind": "deck"},                             # no path
        {"path": str(good), "kind": "poster", "title": "Good"},
        {"path": str(good), "kind": "poster", "title": "Good"},  # registered twice → one row
    ])
    r = client.get("/api/pollinate/works")
    assert r.status_code == 200
    body = r.json()
    assert [w["path"] for w in body["works"]] == ["w/good"]
    assert body["skipped"] == 7


def test_works_broken_registry_is_not_a_500(client, workspace):
    # no registry at all → empty, nothing skipped
    assert client.get("/api/pollinate/works").json() == {"works": [], "skipped": 0}
    reg = workspace / ".pollinate" / "works.json"
    reg.parent.mkdir(parents=True)
    reg.write_text("{ broken", encoding="utf-8")
    r = client.get("/api/pollinate/works")
    assert r.status_code == 200 and r.json() == {"works": [], "skipped": 1}
    reg.write_text(json.dumps({"schema": 1, "works": {"not": "a list"}}), encoding="utf-8")
    assert client.get("/api/pollinate/works").json() == {"works": [], "skipped": 1}


def test_works_registry_too_large_is_skipped(client, workspace):
    from routers import pollinate as mod
    reg = workspace / ".pollinate" / "works.json"
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"works": [], "pad": "x" * 64}), encoding="utf-8")
    with patch.object(mod, "_MAX_JSON_BYTES", 32):
        assert client.get("/api/pollinate/works").json() == {"works": [], "skipped": 1}


def test_works_outside_workspace(client, workspace, tmp_path_factory):
    outside = _write_work(tmp_path_factory.mktemp("desktop") / "AI-Native" / "deck", kind="deck",
                          files=["out/deck.html", "out/slides/01-s-7f3a.png"])
    _write_registry(workspace, [{"path": str(outside), "kind": "deck", "title": "Out"}])
    w = client.get("/api/pollinate/works").json()["works"][0]
    assert w["in_workspace"] is False
    assert w["path"] == str(outside.resolve())
    assert w["main_output"] == str(outside.resolve() / "out" / "deck.html")


def _thread_recorder(monkeypatch, target: str) -> list[str]:
    """Wrap routers.pollinate.<target> to record which thread ran it."""
    from routers import pollinate as mod
    seen: list[str] = []
    real = getattr(mod, target)

    def wrapped(*a, **kw):
        seen.append(threading.current_thread().name)
        return real(*a, **kw)

    monkeypatch.setattr(mod, target, wrapped)
    return seen


def test_assets_runs_through_run_nav(client, workspace, monkeypatch):
    """The folder walk of /assets runs on the nav pool (a 'swarm-nav' thread), not on the
    thread that runs the event loop — and the payload is unchanged."""
    _mk_run(workspace, "2026-05-03-a", run_json={"topic": "t", "status": "completed"},
            deliver={"xiaohongshu": ["poster.png"]})
    seen = _thread_recorder(monkeypatch, "_list_run_dirs")
    r = client.get("/api/pollinate/assets")
    assert r.status_code == 200
    assert [c["run"] for c in r.json()["cards"]] == ["2026-05-03-a"]
    assert seen and all(name.startswith("swarm-nav") for name in seen), seen


def test_works_runs_through_run_nav(client, workspace, monkeypatch):
    good = _write_work(workspace / "w" / "good", kind="poster", files=["out/poster.png"])
    _write_registry(workspace, [{"path": str(good)}])
    seen = _thread_recorder(monkeypatch, "_work_row")
    assert len(client.get("/api/pollinate/works").json()["works"]) == 1
    assert seen and all(name.startswith("swarm-nav") for name in seen), seen


def test_detail_and_publish_routes_are_gone(client, workspace):
    """GET /{run_name} (no caller) and POST /{run_name}/publish (never used) are deleted."""
    _mk_run(workspace, "2026-05-03-gone", run_json={"topic": "t"}, deliver={"xiaohongshu": ["p.png"]})
    assert client.get("/api/pollinate/2026-05-03-gone").status_code == 404
    r = client.post("/api/pollinate/2026-05-03-gone/publish", json={"asset_id": "a" * 40, "published": True})
    assert r.status_code in (404, 405)
    from routers.pollinate import router
    assert sorted(getattr(rt, "path", "") for rt in router.routes) == ["/assets", "/works"]
    # the publish-state sidecar no longer shapes an asset
    a = client.get("/api/pollinate/assets").json()["cards"][0]["assets"][0]
    assert "asset_id" not in a and "posted_url" not in a
