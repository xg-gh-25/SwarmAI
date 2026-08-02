"""Tests for the /api/pollinate endpoint (Content-Asset Gallery).

Covers the Gate-1 findings that shaped the design (each asserts a real data reality):
- BLOCK-1: deliver/ walk is the source of truth — a run with NO run.json still yields a
  card from its deliver/ tree; a run whose run.json.formats/platforms is None but whose
  deliver/ has real files still surfaces those assets; the bare `deliver/{file}` layout
  (no platform subdir) is handled alongside `deliver/{platform}/{file}`.
- BLOCK-2: pollinate-local terminal check — a run with status='review' whose stages use
  name='REFLECT'(UPPERCASE)/status='done' is terminal (the pipeline is_terminal_run would
  miss it).
- HIGH: publish_status defaults to 'ready' when no publish-kit/frontmatter; only a
  `status: ready-to-publish` frontmatter upgrades it; 'published' is never fabricated.
- Route ordering: GET /assets is not shadowed by GET /{run_name}.
- Traversal guard: `..`/absolute path param → 404, no escape.
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
            deliver={"": ["qr-github.png", "platform_matrix.md"]})
    r = client.get("/api/pollinate/assets")
    card = next(c for c in r.json()["cards"] if c["run"] == "2026-07-04-bare")
    assert card["asset_count"] == 2
    assert card["platforms"] == []  # honest: no platform known for bare files


def test_flat_run_dir_with_bare_images_no_deliver(client, workspace):
    """A run dir with bare image files and no deliver/ (2026-04-26-pollinate-poster)."""
    _mk_run(workspace, "2026-04-26-poster", bare_assets=["poster_3x4.png"])
    r = client.get("/api/pollinate/assets")
    runs = [c["run"] for c in r.json()["cards"]]
    assert "2026-04-26-poster" in runs


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
    """A dateless content dir (e.g. `posters/`) must NOT bury dated newest content."""
    _mk_run(workspace, "posters", bare_assets=["a.png", "b.png"])
    _mk_run(workspace, "2026-08-01-recent",
            run_json={"topic": "recent", "status": "completed", "created_at": "2026-08-01T00:00:00"},
            deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    runs = [c["run"] for c in r.json()["cards"]]
    assert runs.index("2026-08-01-recent") < runs.index("posters")


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
    _mk_run(workspace, "2026-06-02-running",
            run_json={"topic": "t", "status": "running", "created_at": "2026-06-02T00:00:00",
                      "stages": [{"name": "BUILD", "status": "running"}]},
            deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    assert r.json()["overall"]["in_progress"] == 1


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


# ---------- detail endpoint + route ordering ----------

def test_overall_exposes_known_channels(client, workspace):
    """overall.known_channels carries the server SSOT channel universe so the frontend
    can grey out fully-neglected channels without hardcoding a drift-prone list."""
    _mk_run(workspace, "2026-06-10-kc", deliver={"xiaohongshu": ["p.png"]})
    r = client.get("/api/pollinate/assets")
    kc = r.json()["overall"]["known_channels"]
    assert "xiaohongshu" in kc and "youtube" in kc  # incl a channel with 0 assets
    assert kc == sorted(kc)  # stable order


def test_detail_endpoint_returns_topic(client, workspace):
    _mk_run(workspace, "2026-05-03-detail",
            run_json={"topic": "detail topic", "status": "completed", "domain": "ai_architecture"},
            deliver={"xiaohongshu": ["poster.png"]})
    (workspace / "Knowledge" / "Pollinate" / "2026-05-03-detail" / "content_package.md").write_text(
        "the message", encoding="utf-8")
    r = client.get("/api/pollinate/2026-05-03-detail")
    assert r.status_code == 200
    body = r.json()
    assert body["topic"] == "detail topic"
    assert body["content_package"] == "the message"
    assert len(body["assets"]) == 1


def test_assets_route_not_shadowed_by_run_name(client, workspace):
    """GET /assets must resolve to the gallery, NOT be captured by /{run_name}."""
    r = client.get("/api/pollinate/assets")
    assert r.status_code == 200
    assert "overall" in r.json()  # the assets response shape, not a topic detail


# ---------- traversal guard ----------

def test_traversal_dotdot_denied(client, workspace):
    r = client.get("/api/pollinate/..%2f..%2fetc")
    assert r.status_code == 404


def test_traversal_via_encoded_path_denied(client, workspace):
    # A run_name with a slash / dotdot must never escape the Pollinate root.
    r = client.get("/api/pollinate/..")
    assert r.status_code == 404


def test_unknown_run_name_404(client, workspace):
    r = client.get("/api/pollinate/2026-99-99-does-not-exist")
    assert r.status_code == 404
