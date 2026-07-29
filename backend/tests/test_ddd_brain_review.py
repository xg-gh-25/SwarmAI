"""Tests for the DDD Brain Hub Review tab endpoints (Run 2).

Methodology: integration tests against a REAL temporary git repo (no mocks of
git / diff parsing) — the Review tab projects real git-diff state, so the tests
drive real `git` subprocesses on a throwaway workspace.

Key invariants under test:
  - AC1: GET /brains/{name}/review returns tagged hunks computed live from a
    scoped `git diff <last-reviewed-sha>..HEAD -- Projects/<ddd>/`, plus pending
    risky proposals. No stored metric (R30#4).
  - AC2: POST /review/approve advances the per-DDD last-reviewed-ref watermark
    file to HEAD.
  - AC3: POST /review/reject {file, hunk_signature} reverts ONLY that hunk via
    `git apply -R`; a separate edit to the SAME file survives. NEVER
    `git checkout <file>` (GUI127). Forced-execution test (R28).
  - AC3-drift: reject identifies the hunk by CONTENT SIGNATURE, not position
    index (Gate-1 point #2) — a stale index must not silently revert the wrong
    hunk.
"""

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ─── Temp git-workspace fixture ──────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def ddd_repo(tmp_path, monkeypatch):
    """A tmp SwarmWS-shaped git repo with one DDD project 'Demo'."""
    ws = tmp_path / "SwarmWS"
    proj = ws / "Projects" / "Demo"
    (proj / "2-understanding").mkdir(parents=True)
    (proj / ".project.json").write_text('{"name": "Demo"}\n')
    (proj / ".artifacts").mkdir()
    doc = proj / "2-understanding" / "TECH.md"
    doc.write_text("\n".join(f"line{i}" for i in range(1, 41)) + "\n")

    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t.co")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    base_sha = _git(ws, "rev-parse", "HEAD")

    # Point the router's workspace resolver at this tmp tree.
    monkeypatch.setattr("routers.ddd_brain._workspace_root", lambda: ws)

    return {"ws": ws, "proj": proj, "doc": doc, "base_sha": base_sha}


@pytest.fixture
def client():
    from routers.ddd_brain import router

    app = FastAPI()
    app.include_router(router, prefix="/api/ddd")
    return TestClient(app)


def _edit_and_commit(repo, line_idx, new_text, msg):
    doc = repo["doc"]
    lines = doc.read_text().splitlines()
    lines[line_idx] = new_text
    doc.write_text("\n".join(lines) + "\n")
    _git(repo["ws"], "add", "-A")
    _git(repo["ws"], "commit", "-qm", msg)


# ─── AC1: review endpoint returns live tagged hunks ──────────────────────────

class TestReviewList:
    def _set_watermark(self, ddd_repo, sha):
        """Explicitly set the reviewed watermark (simulates a prior review)."""
        wm = ddd_repo["proj"] / ".artifacts" / ".last-reviewed-sha"
        wm.parent.mkdir(parents=True, exist_ok=True)
        wm.write_text(sha + "\n")

    def test_review_returns_hunks_since_watermark(self, client, ddd_repo):
        # Reviewed up to base; then two committed edits land → both are unreviewed.
        self._set_watermark(ddd_repo, ddd_repo["base_sha"])
        _edit_and_commit(ddd_repo, 4, "CULTIVATED_line5", "edit line5")
        _edit_and_commit(ddd_repo, 34, "CULTIVATED_line35", "edit line35")

        resp = client.get("/api/ddd/brains/Demo/review")
        assert resp.status_code == 200
        data = resp.json()
        assert "hunks" in data and "last_reviewed_sha" in data and "head_sha" in data
        assert data["last_reviewed_sha"] == ddd_repo["base_sha"]
        # Two far-apart edits = two separate hunks.
        assert len(data["hunks"]) >= 2
        h0 = data["hunks"][0]
        assert h0["file"].endswith("TECH.md")
        assert "signature" in h0 and h0["signature"]  # content signature, not index
        assert "tag" in h0
        assert "diff_text" in h0 and "@@" in h0["diff_text"]

    def test_review_empty_when_no_changes(self, client, ddd_repo):
        # No commits since base → watermark defaults to last commit (HEAD) → 0 hunks.
        resp = client.get("/api/ddd/brains/Demo/review")
        assert resp.status_code == 200
        assert resp.json()["hunks"] == []

    def test_review_404_unknown_brain(self, client, ddd_repo):
        assert client.get("/api/ddd/brains/Nope/review").status_code == 404

    def test_stale_watermark_falls_back_not_silent_empty(self, client, ddd_repo):
        """Gate-2 correctness: a watermark pointing at a gc'd/nonexistent SHA must
        NOT make review silently return [] (review-bypass) — it falls back to the
        default base and still surfaces the unreviewed change."""
        _edit_and_commit(ddd_repo, 4, "REAL_UNREVIEWED_CHANGE", "edit line5")
        # Plant a stale watermark: a syntactically-valid but nonexistent SHA.
        wm = ddd_repo["proj"] / ".artifacts" / ".last-reviewed-sha"
        wm.parent.mkdir(parents=True, exist_ok=True)
        wm.write_text("0" * 40 + "\n")

        data = client.get("/api/ddd/brains/Demo/review").json()
        # Must NOT be silently empty — the real change still shows.
        assert len(data["hunks"]) >= 1
        assert any("REAL_UNREVIEWED_CHANGE" in h["diff_text"] for h in data["hunks"])
        # And the returned watermark is NOT the stale zero-sha.
        assert data["last_reviewed_sha"] != "0" * 40

    def test_traversal_name_is_contained(self, client, ddd_repo):
        """Gate-2 security defense-in-depth: a name that would resolve outside
        Projects/ is refused (404), never a path-escape. (Starlette already 404s
        URL-encoded `../`; this asserts the server-side containment guard too.)"""
        # A name whose .project.json-carrying resolution is NOT a direct child of
        # Projects/ must 404 — verified via the resolver directly (routing layer
        # already blocks the URL form).
        from routers.ddd_brain import _resolve_brain_dir
        # Names that RESOLVE OUTSIDE Projects/ → refused.
        assert _resolve_brain_dir("../Demo") is None          # escapes to Projects-parent
        assert _resolve_brain_dir("../../etc") is None         # escapes the workspace
        assert _resolve_brain_dir("sub/Demo") is None          # not a direct child
        # Names that resolve back INSIDE Projects/ as the same dir → allowed
        # (containment holds; `Demo/../Demo` IS Projects/Demo).
        assert _resolve_brain_dir("Demo/../Demo") is not None
        assert _resolve_brain_dir("Demo") is not None          # the legit one resolves


# ─── AC2: approve advances the watermark ─────────────────────────────────────

class TestReviewApprove:
    def test_approve_advances_watermark_to_head(self, client, ddd_repo):
        _edit_and_commit(ddd_repo, 4, "CULTIVATED_line5", "edit line5")
        head = _git(ddd_repo["ws"], "rev-parse", "HEAD")

        resp = client.post("/api/ddd/brains/Demo/review/approve")
        assert resp.status_code == 200
        assert resp.json()["last_reviewed_sha"] == head

        # Watermark file now equals HEAD → a fresh review shows 0 hunks.
        assert client.get("/api/ddd/brains/Demo/review").json()["hunks"] == []


# ─── AC3: reject reverts ONLY that hunk (forced-execution, R28) ──────────────

class TestReviewRejectHunk:
    def test_reject_reverts_only_target_hunk(self, client, ddd_repo):
        # Reviewed up to base; two far-apart edits land = two separate hunks.
        wm = ddd_repo["proj"] / ".artifacts" / ".last-reviewed-sha"
        wm.parent.mkdir(parents=True, exist_ok=True)
        wm.write_text(ddd_repo["base_sha"] + "\n")
        _edit_and_commit(ddd_repo, 4, "CULTIVATED_line5", "edit line5")
        _edit_and_commit(ddd_repo, 34, "SURVIVOR_line35", "edit line35")

        review = client.get("/api/ddd/brains/Demo/review").json()
        # Find the hunk that carries the line5 edit.
        target = next(h for h in review["hunks"] if "CULTIVATED_line5" in h["diff_text"])

        resp = client.post(
            "/api/ddd/brains/Demo/review/reject",
            json={"file": target["file"], "hunk_signature": target["signature"]},
        )
        assert resp.status_code == 200
        assert resp.json()["reverted"] is True

        text = ddd_repo["doc"].read_text()
        # line5 reverted back to original; line35 edit SURVIVES.
        assert "CULTIVATED_line5" not in text
        assert "line5" in text
        assert "SURVIVOR_line35" in text

    def test_reject_correct_hunk_when_identical_change_appears_twice(self, client, ddd_repo):
        """REVIEW CRITICAL regression: TWO hunks with byte-identical +/- content
        at different file locations must get DISTINCT signatures (old-side span
        disambiguates), so rejecting the 2nd reverts only the 2nd — not the first
        match. A TRUE collision needs identical removed AND added text on both."""
        # Seed a doc where two far-apart lines have the SAME original text, so an
        # identical edit produces byte-identical hunks (same '-DUP\n+DUP_EDITED').
        doc = ddd_repo["doc"]
        lines = [f"line{i}" for i in range(1, 41)]
        lines[4] = "DUP"
        lines[34] = "DUP"
        doc.write_text("\n".join(lines) + "\n")
        _git(ddd_repo["ws"], "add", "-A")
        _git(ddd_repo["ws"], "commit", "-qm", "seed dup lines")
        seed_sha = _git(ddd_repo["ws"], "rev-parse", "HEAD")

        wm = ddd_repo["proj"] / ".artifacts" / ".last-reviewed-sha"
        wm.parent.mkdir(parents=True, exist_ok=True)
        wm.write_text(seed_sha + "\n")

        # Now edit BOTH DUP lines identically → byte-identical hunks.
        lines[4] = "DUP_EDITED"
        lines[34] = "DUP_EDITED"
        doc.write_text("\n".join(lines) + "\n")
        _git(ddd_repo["ws"], "add", "-A")
        _git(ddd_repo["ws"], "commit", "-qm", "edit both dup lines identically")

        review = client.get("/api/ddd/brains/Demo/review").json()
        hunks = review["hunks"]
        assert len(hunks) >= 2, f"expected 2 hunks, got {len(hunks)}"
        # DISTINCT signatures despite byte-identical +/- content (the fix).
        sigs = {h["signature"] for h in hunks}
        assert len(sigs) == len(hunks), "byte-identical hunks collided to one signature"

        # Reject the LATER hunk (higher old-side start line) → only line35 reverts.
        later = max(hunks, key=lambda h: int(h["diff_text"].split("@@ -")[1].split(",")[0]))
        resp = client.post(
            "/api/ddd/brains/Demo/review/reject",
            json={"file": later["file"], "hunk_signature": later["signature"]},
        )
        assert resp.status_code == 200
        out = doc.read_text().splitlines()
        assert out[34] == "DUP", "later hunk (line35) should be reverted"
        assert out[4] == "DUP_EDITED", "earlier hunk (line5) must survive"

    def test_reject_bad_signature_is_loud(self, client, ddd_repo):
        _edit_and_commit(ddd_repo, 4, "CULTIVATED_line5", "edit line5")
        resp = client.post(
            "/api/ddd/brains/Demo/review/reject",
            json={"file": "2-understanding/TECH.md", "hunk_signature": "deadbeef-nonexistent"},
        )
        # Fail-loud: a signature that matches no current hunk must NOT silently
        # succeed (it must not revert a wrong hunk).
        assert resp.status_code == 404

    def test_reject_never_uses_git_checkout(self):
        """Source guard: the reject path must never `git checkout <file>` (GUI127)."""
        src = Path(__file__).resolve().parents[1] / "routers" / "ddd_brain.py"
        text = src.read_text()
        # No `checkout` of a file in the review/reject implementation.
        assert '"checkout"' not in text and "'checkout'" not in text
