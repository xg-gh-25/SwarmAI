"""Tests for EvalService.growth_report — the "what I changed / evolved / grew"
self-evolution growth surface (run_448a4f7f, D2/D3).

The growth report is the mentor-facing window into autonomous self-shaping: it
surfaces what the agent recorded/judged/proposed about itself AND — the headline
— any constitution (SOUL/AGENT/STEERING) writes, git-tracked and visible. This
replaces "ask permission to record" with "report after the fact." The pure
formatter is unit-testable on synthetic inputs; the git-gather is a thin adapter.
"""

from core.eval_service import EvalService


class TestGrowthReportFormatter:
    """Pure formatter: (records, proposals, constitution_commits) -> report dict."""

    def test_constitution_change_is_headline(self):
        """A SOUL/AGENT/STEERING commit MUST surface as a headline — this is the
        agent's self-shaping made visible+reversible (the mentor's mirror)."""
        report = EvalService._format_growth_report(
            autonomous_records=[],
            proposals=[],
            constitution_commits=[
                {"hash": "abc1234", "file": "AGENT.md",
                 "subject": "add R16b pre-write reflex", "date": "2026-06-25"},
            ],
        )
        assert report["constitution_changes"], "constitution commit must appear"
        assert report["constitution_changes"][0]["file"] == "AGENT.md"
        assert report["has_constitution_change"] is True
        # headline ranking: constitution changes are the lead section
        assert report["headline"] and "AGENT.md" in report["headline"]

    def test_no_constitution_change_no_headline(self):
        report = EvalService._format_growth_report(
            autonomous_records=[{"class": "CLASS_A", "count": 5}],
            proposals=[],
            constitution_commits=[],
        )
        assert report["has_constitution_change"] is False
        # still reports growth (records), just no constitution headline
        assert report["autonomous_records"]

    def test_surfaces_autonomous_records_and_proposals(self):
        report = EvalService._format_growth_report(
            autonomous_records=[{"class": "CLASS_A", "count": 5}],
            proposals=[{"id": "CLASS_A:rule", "kind": "rule", "source_class": "CLASS_A"}],
            constitution_commits=[],
        )
        assert any(r["class"] == "CLASS_A" for r in report["autonomous_records"])
        assert any(p["id"] == "CLASS_A:rule" for p in report["proposals"])

    def test_empty_growth_is_honest_not_fabricated(self):
        """No activity → an explicit empty report, not invented progress."""
        report = EvalService._format_growth_report(
            autonomous_records=[], proposals=[], constitution_commits=[],
        )
        assert report["has_constitution_change"] is False
        assert report["autonomous_records"] == []
        assert report["proposals"] == []
        assert report["headline"] == "" or "no" in report["headline"].lower()


class TestConstitutionChurnFilter:
    """The growth report must show DELIBERATE self-writes, not auto-bundled
    refresh-churn (framework:/chore:/project:/content: prefixes). Surfacing churn
    as 'what I grew' is the gauge-reads-polluted-data disease — 100% of the live
    7d window was auto-bundle (run_448a4f7f SMOKE)."""

    def test_churn_prefixes_are_filtered(self, tmp_path):
        """A git repo whose constitution commits are all auto-bundle churn yields
        ZERO growth-report constitution changes."""
        import subprocess
        from core.eval_service import EvalService
        repo = tmp_path / "ws"
        ctx = repo / ".context"
        ctx.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
        (ctx / "AGENT.md").write_text("v1\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        # churn-prefixed commit (the auto-bundle hook shape)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                        "framework: framework (4), chore (1)"], check=True)
        (ctx / "AGENT.md").write_text("v2\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        # a DELIBERATE self-write
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                        "governance(AGENT): add R-foo pre-write reflex"], check=True)
        svc = EvalService.__new__(EvalService)  # bypass __init__ (no full workspace)
        commits = EvalService._constitution_commits(svc, since_days=3650,
                                                     workspace_root=repo)
        subjects = [c["subject"] for c in commits]
        assert any("R-foo" in s for s in subjects), "deliberate write must surface"
        assert not any(s.startswith("framework:") for s in subjects), \
            "auto-bundle churn must be filtered out"


class TestConstitutionCommitsCache:
    """The git-log subprocess in _constitution_commits is the briefing hot-path
    bottleneck (215ms warm / 940ms cold) and spikes under cross-tab git-lock
    contention. A process-level TTL cache makes N parallel tabs share ONE git
    spawn per (since_days, workspace_root) per window. Must stay hermetic: the
    cache keys on workspace_root so a test with its own tmp repo never collides,
    and use_cache=False bypasses entirely."""

    def _make_repo(self, tmp_path, marker="R-foo"):
        import subprocess
        repo = tmp_path / "ws"
        ctx = repo / ".context"
        ctx.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
        (ctx / "AGENT.md").write_text("v1\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                        f"governance(AGENT): add {marker}"], check=True)
        return repo

    def test_second_call_within_ttl_does_not_respawn_git(self, tmp_path):
        """Two calls with the same (since_days, workspace_root) inside the TTL
        window spawn git exactly ONCE — the cache hit serves the second."""
        from unittest import mock
        from core import eval_service as es
        es._clear_constitution_cache()  # isolate from other tests
        repo = self._make_repo(tmp_path)
        svc = es.EvalService.__new__(es.EvalService)
        real_run = es.subprocess.run
        with mock.patch.object(es.subprocess, "run", side_effect=real_run) as spy:
            r1 = es.EvalService._constitution_commits(svc, since_days=3650, workspace_root=repo)
            r2 = es.EvalService._constitution_commits(svc, since_days=3650, workspace_root=repo)
        assert spy.call_count == 1, f"expected 1 git spawn (cache hit), got {spy.call_count}"
        assert r1 == r2
        assert any("R-foo" in c["subject"] for c in r1)

    def test_different_workspace_spawns_its_own_git(self, tmp_path):
        """Cache keys on workspace_root — two different repos each spawn git
        (no cross-workspace cache poisoning)."""
        from unittest import mock
        from core import eval_service as es
        es._clear_constitution_cache()
        repo_a = self._make_repo(tmp_path / "a", marker="R-aaa")
        repo_b = self._make_repo(tmp_path / "b", marker="R-bbb")
        real_run = es.subprocess.run
        with mock.patch.object(es.subprocess, "run", side_effect=real_run) as spy:
            ra = es.EvalService._constitution_commits(es.EvalService.__new__(es.EvalService),
                                                      since_days=3650, workspace_root=repo_a)
            rb = es.EvalService._constitution_commits(es.EvalService.__new__(es.EvalService),
                                                      since_days=3650, workspace_root=repo_b)
        assert spy.call_count == 2, "different workspaces must not share a cache entry"
        assert any("R-aaa" in c["subject"] for c in ra)
        assert any("R-bbb" in c["subject"] for c in rb)

    def test_use_cache_false_bypasses(self, tmp_path):
        """use_cache=False always spawns git — the hermetic escape hatch."""
        from unittest import mock
        from core import eval_service as es
        es._clear_constitution_cache()
        repo = self._make_repo(tmp_path)
        real_run = es.subprocess.run
        with mock.patch.object(es.subprocess, "run", side_effect=real_run) as spy:
            es.EvalService._constitution_commits(es.EvalService.__new__(es.EvalService),
                                                 since_days=3650, workspace_root=repo, use_cache=False)
            es.EvalService._constitution_commits(es.EvalService.__new__(es.EvalService),
                                                 since_days=3650, workspace_root=repo, use_cache=False)
        assert spy.call_count == 2, "use_cache=False must bypass the cache every call"

    def test_transient_git_failure_is_not_cached(self, tmp_path):
        """Gate-2 adversarial #3: a git FAILURE (non-repo / error / timeout) must
        NOT be cached. A non-git dir returns [], but the NEXT call (after the dir
        becomes a real repo with a commit) must see the real commit — not a stale
        empty served from cache for 300s."""
        from core import eval_service as es
        es._clear_constitution_cache()
        svc = es.EvalService.__new__(es.EvalService)
        not_a_repo = tmp_path / "ws"   # has .context but NO git → git log fails
        (not_a_repo / ".context").mkdir(parents=True)
        # 1st call: git fails (not a repo) → returns [] to caller, caches NOTHING
        r1 = es.EvalService._constitution_commits(svc, since_days=3650, workspace_root=not_a_repo)
        assert r1 == [], "git failure surfaces as empty list to the caller"
        # Now make the SAME dir a real repo with a deliberate constitution commit
        import subprocess
        subprocess.run(["git", "init", "-q", str(not_a_repo)], check=True)
        subprocess.run(["git", "-C", str(not_a_repo), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(not_a_repo), "config", "user.name", "t"], check=True)
        (not_a_repo / ".context" / "AGENT.md").write_text("v1\n")
        subprocess.run(["git", "-C", str(not_a_repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(not_a_repo), "commit", "-q", "-m",
                        "governance(AGENT): add R-recovered"], check=True)
        # 2nd call MUST see the commit (failure was not cached)
        r2 = es.EvalService._constitution_commits(svc, since_days=3650, workspace_root=not_a_repo)
        assert any("R-recovered" in c["subject"] for c in r2), \
            "transient git failure must not poison the cache for 300s"

    def test_genuine_empty_IS_cached(self, tmp_path):
        """A git repo with ZERO constitution commits returns [] (genuine empty) —
        that IS cacheable (no commits = no commits). Distinguishes the cacheable
        empty from the uncacheable failure of the test above."""
        import subprocess
        from unittest import mock
        from core import eval_service as es
        es._clear_constitution_cache()
        repo = tmp_path / "ws"
        (repo / ".context").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
        # a commit that touches NO .context file → zero constitution commits
        (repo / "README.md").write_text("x\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "docs: readme"], check=True)
        svc = es.EvalService.__new__(es.EvalService)
        real_run = es.subprocess.run
        with mock.patch.object(es.subprocess, "run", side_effect=real_run) as spy:
            r1 = es.EvalService._constitution_commits(svc, since_days=3650, workspace_root=repo)
            r2 = es.EvalService._constitution_commits(svc, since_days=3650, workspace_root=repo)
        assert r1 == [] and r2 == []
        assert spy.call_count == 1, "genuine-empty result must be cached (1 git spawn)"

    def test_cache_hit_returns_copy_not_shared_list(self, tmp_path):
        """Gate-2 adversarial #1/#8: a cache hit must return a COPY, so a caller
        mutating the returned list cannot corrupt future hits (4 concurrent tabs
        share the cache entry)."""
        from core import eval_service as es
        es._clear_constitution_cache()
        repo = self._make_repo(tmp_path)
        svc = es.EvalService.__new__(es.EvalService)
        r1 = es.EvalService._constitution_commits(svc, since_days=3650, workspace_root=repo)
        r1.append({"hash": "POISON", "subject": "POISON", "date": "", "file": ""})
        r2 = es.EvalService._constitution_commits(svc, since_days=3650, workspace_root=repo)
        assert not any(c["hash"] == "POISON" for c in r2), \
            "mutating a returned list must not corrupt the cached entry"
        assert r1 is not r2, "each hit returns a distinct list object"


class TestGrowthReportBriefingLines:
    """The briefing surface: constitution changes render as a flagged headline."""

    def test_briefing_lines_flag_constitution_change(self):
        report = {
            "has_constitution_change": True,
            "constitution_changes": [
                {"hash": "abc1234", "file": "SOUL.md",
                 "subject": "refine P1", "date": "2026-06-25"},
            ],
            "autonomous_records": [],
            "proposals": [],
            "headline": "1 constitution change: SOUL.md",
        }
        lines = EvalService._growth_briefing_lines(report)
        assert any("SOUL.md" in ln for ln in lines)
        # constitution change must be visually flagged (not buried)
        assert any("constitution" in ln.lower() or "🧬" in ln or "grew" in ln.lower()
                   for ln in lines)

    def test_briefing_lines_empty_when_no_growth(self):
        report = {
            "has_constitution_change": False, "constitution_changes": [],
            "autonomous_records": [], "proposals": [], "headline": "",
        }
        lines = EvalService._growth_briefing_lines(report)
        assert lines == []
