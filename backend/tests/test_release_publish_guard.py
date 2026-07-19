"""Tests for release_publish_guard PreToolUse Bash gate (run_9fec1fb1, 2026-07-04).

Code-enforced half of s_swarm-release Stage 7b. `gh release create` publishes a
GitHub Release (tag + DMG — irreversible star/download side effects); it must NOT
run on a HEAD that CI has not validated (the v1.24.0 miss: published, then CI red).

Design A (marker-based): the guard ALLOWS `gh release create` ONLY when a CI-green
marker exists AND marker.head_sha == the current git HEAD. The marker is written
exclusively by `artifact_cli.py release-gate --poll`. The guard does NO network
call — it reads a local file + `git rev-parse HEAD` — so it CANNOT reintroduce the
foreground-timeout hang trap that the 7b runbook poll (and `gh run watch`) had.

Invariants:
  - DENY  `gh release create` when marker absent / unreadable / HEAD-mismatch (stale)
  - ALLOW `gh release create` when marker.head_sha == current HEAD
  - ALLOW everything else (non-Bash, non-create gh verbs, unrelated commands)
  - SWARM_RELEASE_GATE_FORCE=1 → ALLOW (logged escape hatch)
Methodology: monkeypatch the guard's HEAD-resolver + marker path so tests are
hermetic (no real git/gh), then assert the allow/deny decision.
"""

import asyncio
import json

import pytest

from core import security_hooks
from core.security_hooks import release_publish_guard


def _run(command, tool_name="Bash"):
    return asyncio.run(
        release_publish_guard(
            {"tool_name": tool_name, "tool_input": {"command": command}}, None, None
        )
    )


def _is_deny(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


@pytest.fixture
def marker_env(tmp_path, monkeypatch):
    """Point the guard at a temp marker + a fixed HEAD. Returns (write_marker, HEAD)."""
    HEAD = "a" * 40
    marker = tmp_path / ".release-ci-green.json"

    # The guard resolves the marker via config.get_app_data_dir()/SwarmWS/... —
    # override by pointing SWARM_WORKSPACE at tmp and materializing the tree, OR
    # patch the helper directly. Patch the helper's two IO points for hermeticity.
    def fake_authorizes(published_tag=None):
        # re-implement the real predicate against our temp marker + fixed HEAD,
        # exercising the SAME allow/deny logic paths. Accepts published_tag for
        # signature-compat with the real predicate (these HEAD-anchored tests write
        # no `tag`, so the arg is unused here — tag-anchored paths are covered by
        # TestTagAnchoredMarker against the REAL predicate).
        if not marker.exists():
            return False, "no CI-green marker"
        try:
            data = json.loads(marker.read_text())
        except Exception:
            return False, "marker unreadable"
        if data.get("head_sha") != HEAD:
            return False, f"marker HEAD {data.get('head_sha','')[:8]} != current {HEAD[:8]}"
        return True, f"CI green on HEAD {HEAD[:8]}"

    def write_marker(head_sha):
        marker.write_text(json.dumps({"head_sha": head_sha, "run_id": 123,
                                       "conclusion": "success", "ts": "t"}))

    monkeypatch.setattr(security_hooks, "_release_marker_authorizes_head", fake_authorizes)
    monkeypatch.delenv("SWARM_RELEASE_GATE_FORCE", raising=False)
    return write_marker, HEAD


class TestPublishGatedOnMarker:
    def test_deny_when_marker_absent(self, marker_env):
        # no marker written → fail-closed DENY
        assert _is_deny(_run("gh release create v1.25.0 dist/app.dmg --title v1.25.0"))

    def test_allow_when_marker_matches_head(self, marker_env):
        write_marker, HEAD = marker_env
        write_marker(HEAD)  # CI green on the current HEAD
        assert not _is_deny(_run("gh release create v1.25.0 dist/app.dmg --title v1.25.0"))

    def test_deny_when_marker_is_stale(self, marker_env):
        write_marker, HEAD = marker_env
        write_marker("b" * 40)  # marker from a PREVIOUS release's HEAD
        assert _is_deny(_run("gh release create v1.25.0 dist/app.dmg")), \
            "a stale marker (different HEAD) must NOT authorize publish on this HEAD"

    def test_deny_reason_names_ci_and_head(self, marker_env):
        r = _run("gh release create v1.25.0 dist/app.dmg")
        reason = r.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "CI" in reason
        assert "release-gate" in reason or "HEAD" in reason


class TestEditFlipGated:
    """The real publish action is now `gh release edit --draft=false` (flip a
    CI-created draft to published), NOT `gh release create`. run_900bb839: the guard
    gated only `create`, so the edit-flip published on an unvalidated HEAD unchecked.
    The guard must gate the edit-flip on the SAME CI-green marker."""

    # gh's --draft is a boolean pflag (verified gh 2.88.1): value attaches ONLY via
    # `=`, and Go strconv.ParseBool's false-set is EXACTLY {false,f,0} (case-insensitive).
    # ALL of these publish, so ALL must be gated. NOT here: `--draft false` (space form,
    # a hard gh arg error) and `--draft=no`/`=n`/`=yes` (gh rejects as invalid ParseBool
    # — never publish); gating those would be dead weight against commands gh aborts.
    @pytest.mark.parametrize("cmd", [
        "gh release edit v1.25.0 --draft=false",                    # canonical
        "gh release edit v1.25.0 --draft=FALSE",                    # case-insensitive
        "gh release edit v1.25.0 --draft=0",                        # ParseBool false — was a BYPASS
        "gh release edit v1.25.0 --draft=f",                        # ParseBool false — was a BYPASS
        "gh release edit v1.25.0 --draft=false --latest",           # with --latest
        "gh release edit --draft=false --latest v1.25.0",           # flag order independent
        'gh release edit v1.25.0 --draft=false --notes "ship it"',  # flip + notes together
    ])
    def test_deny_edit_flip_without_marker(self, cmd, marker_env):
        # no marker written → fail-closed DENY (this is the bug being fixed: was ALLOW)
        assert _is_deny(_run(cmd)), f"draft->published flip must be GATED: {cmd!r}"

    def test_allow_edit_flip_when_marker_matches(self, marker_env):
        write_marker, HEAD = marker_env
        write_marker(HEAD)
        assert not _is_deny(_run("gh release edit v1.25.0 --draft=false --latest"))

    def test_force_bypasses_edit_flip(self, marker_env, monkeypatch):
        monkeypatch.setenv("SWARM_RELEASE_GATE_FORCE", "1")
        assert not _is_deny(_run("gh release edit v1.25.0 --draft=false"))


class TestNonPublishApproved:
    """Fail-safe: only publish actions (create + edit-flip) are gated; else passes."""

    @pytest.mark.parametrize("cmd", [
        "gh release view v1.24.0 --json tagName",     # view is not publish
        "gh release list",                             # list is not publish
        "gh release download v1.24.0",                 # download is not publish
        "gh release edit v1.25.0 --notes 'updated'",   # metadata-only edit, NOT a flip
        "gh release edit v1.25.0 --draft=true",        # re-drafting (reverse) is NOT publish
        "gh release edit v1.25.0 --draft=1",           # ParseBool TRUE — reverse, NOT publish
        "gh release delete v1.24.0",                   # delete owned by C041 gate, not here
        "git push origin main",
        "python scripts/artifact_cli.py release-gate --poll",
        "gh run list --branch main",
        'git commit -m "docs: describe gh release create flow"',  # quoted → not a real create
        'git commit -m "note: run gh release edit --draft=false to publish"',  # quoted → not real
    ])
    def test_non_publish_approved(self, cmd, marker_env):
        assert not _is_deny(_run(cmd)), f"non-publish command must be APPROVED: {cmd!r}"

    def test_non_bash_tool_approved(self, marker_env):
        assert not _is_deny(_run("gh release create v1.25.0 x.dmg", tool_name="Read"))

    def test_empty_command_approved(self, marker_env):
        assert not _is_deny(_run(""))


class TestForceOverride:
    def test_force_env_allows_without_marker(self, marker_env, monkeypatch):
        monkeypatch.setenv("SWARM_RELEASE_GATE_FORCE", "1")
        # no marker, but FORCE set → allowed (logged escape hatch)
        assert not _is_deny(_run("gh release create v1.25.0 dist/app.dmg"))


class TestRegisteredInHookChain:
    def test_guard_registered(self):
        import inspect
        from core import hook_builder
        src = inspect.getsource(hook_builder)
        assert "release_publish_guard" in src, (
            "release_publish_guard must be registered in hook_builder.build_hooks — "
            "an unregistered guard is dead code."
        )


class TestRealPredicateFailClosed:
    """Exercise the REAL _release_marker_authorizes_head (not the fixture stub) to
    prove it fail-closes when the marker genuinely does not exist for SwarmAI."""

    def test_real_predicate_denies_without_marker(self, monkeypatch, tmp_path):
        # Point workspace at an empty tmp → no marker → must be (False, ...)
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        ok, reason = security_hooks._release_marker_authorizes_head()
        assert ok is False
        assert "marker" in reason.lower()


class TestRealPredicateCwdIndependence:
    """Regression for the run_9fec1fb1 adversarial BLOCK: the hook runs in the daemon
    whose cwd is '/' (NOT a git repo). A bare `git rev-parse HEAD` there returns 128
    → would DENY every release. The marker records repo_root; the predicate must
    resolve HEAD via `git -C <repo_root>` so it works REGARDLESS of process cwd.
    These drive the REAL predicate against a REAL temp git repo, from a NON-repo cwd."""

    @staticmethod
    def _make_repo(tmp_path):
        import subprocess
        repo = tmp_path / "srcrepo"
        repo.mkdir()
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path)}
        import os as _os
        e = {**_os.environ, **env}
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=e)
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, env=e)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env=e)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        return repo, head

    def _write_marker(self, tmp_path, monkeypatch, head_sha, repo_root):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        marker = tmp_path / "Projects" / "SwarmAI" / ".artifacts" / ".release-ci-green.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"head_sha": head_sha, "repo_root": str(repo_root),
                                      "run_id": 1, "conclusion": "success", "ts": "t"}))

    def test_real_allow_path_from_non_repo_cwd(self, tmp_path, monkeypatch):
        """The bug the skeptic caught: marker+matching HEAD must AUTHORIZE even when
        the process cwd is a non-repo dir (mirrors the daemon's cwd='/')."""
        import os
        repo, head = self._make_repo(tmp_path)
        self._write_marker(tmp_path, monkeypatch, head, repo)
        # chdir to a NON-repo dir — the exact condition that broke the first version
        non_repo = tmp_path / "elsewhere"; non_repo.mkdir()
        cwd0 = os.getcwd()
        try:
            os.chdir(non_repo)
            ok, reason = security_hooks._release_marker_authorizes_head()
        finally:
            os.chdir(cwd0)
        assert ok is True, f"real predicate must AUTHORIZE from non-repo cwd via git -C: {reason}"

    def test_real_stale_marker_denies_from_non_repo_cwd(self, tmp_path, monkeypatch):
        import os
        repo, head = self._make_repo(tmp_path)
        self._write_marker(tmp_path, monkeypatch, "d" * 40, repo)  # marker HEAD != real HEAD
        non_repo = tmp_path / "elsewhere"; non_repo.mkdir()
        cwd0 = os.getcwd()
        try:
            os.chdir(non_repo)
            ok, reason = security_hooks._release_marker_authorizes_head()
        finally:
            os.chdir(cwd0)
        assert ok is False and "stale" in reason.lower()

    def test_real_missing_repo_root_denies(self, tmp_path, monkeypatch):
        """Old-format marker without repo_root → fail-closed (not a silent allow)."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        marker = tmp_path / "Projects" / "SwarmAI" / ".artifacts" / ".release-ci-green.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"head_sha": "a" * 40, "conclusion": "success"}))
        ok, reason = security_hooks._release_marker_authorizes_head()
        assert ok is False and "repo_root" in reason


class TestExtractReleaseTag:
    """_extract_release_tag pulls the positional tag from a publish command so the
    guard can verify the PUBLISHED tag against a tag-anchored marker."""

    @pytest.mark.parametrize("cmd,expected", [
        ("gh release edit v1.26.0 --draft=false --latest", "v1.26.0"),
        ("gh release create v1.26.0 --notes 'x'", "v1.26.0"),
        ("gh release edit --draft=false --latest v1.26.0", "v1.26.0"),  # flag-first order
        ("gh release edit v1.26.0 --draft=false --notes \"see v9.9.9 for details\"", "v1.26.0"),  # notes lookalike stripped
        ("gh release edit --draft=false", None),   # no positional tag → None → fail-closed
        ("git push origin main", None),            # not a release command
        # Gate-2 HIGH: a SPACE-valued flag before the tag must not be read as the tag
        ("gh release edit --title MyTitle v1.26.0 --draft=false", "v1.26.0"),
        ("gh release edit -R owner/repo v1.26.0 --draft=false", "v1.26.0"),
        ("gh release edit --target abc123 v1.26.0 --draft=false", "v1.26.0"),  # value swallowed, positional found
        # Gate-2 re-review LOW: boolean flags must NOT swallow the tag as a "value"
        ("gh release edit --prerelease v1.26.0 --draft=false", "v1.26.0"),
        ("gh release create -p v1.26.0 --notes 'x'", "v1.26.0"),
        ("gh release edit --latest v1.26.0 --draft=false", "v1.26.0"),
    ])
    def test_extract(self, cmd, expected):
        assert security_hooks._extract_release_tag(cmd) == expected


class TestDecouplingFlagsFailClosed:
    """Gate-2 CRITICAL (run_81ad1cfe): `gh release edit --draft=false --target <sha>`
    publishes at --target's commit, and `--tag <name>` renames the published tag —
    both decouple what gh ships from the positional tag the hook verifies locally.
    The guard MUST fail-CLOSED (DENY) on their presence, even with a valid marker,
    because it cannot attest the actually-published commit. The legit runbook flip
    never uses them."""

    @pytest.fixture
    def valid_tag_marker(self, tmp_path, monkeypatch):
        repo, tag_commit, tip = TestTagAnchoredMarker._make_repo_with_tag(tmp_path)
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        marker = tmp_path / "Projects" / "SwarmAI" / ".artifacts" / ".release-ci-green.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"head_sha": tag_commit, "repo_root": str(repo),
                                      "tag": "v1.26.0", "run_id": 1, "conclusion": "success", "ts": "t"}))
        monkeypatch.delenv("SWARM_RELEASE_GATE_FORCE", raising=False)
        return repo, tag_commit, tip

    @pytest.mark.parametrize("cmd", [
        "gh release edit v1.26.0 --draft=false --target {tip}",     # publish at unverified tip
        "gh release edit v1.26.0 --draft=false --tag v9.9.9",       # rename published tag
        "gh release edit v1.26.0 --draft=false --target=main",      # =-form also caught
    ])
    def test_deny_decoupling_flag_even_with_valid_marker(self, cmd, valid_tag_marker):
        repo, tag_commit, tip = valid_tag_marker
        assert _is_deny(_run(cmd.format(tip=tip))), \
            f"--target/--tag must fail-CLOSED (would publish a commit the gate can't verify): {cmd!r}"

    def test_force_still_overrides_decoupling(self, valid_tag_marker, monkeypatch):
        repo, tag_commit, tip = valid_tag_marker
        monkeypatch.setenv("SWARM_RELEASE_GATE_FORCE", "1")
        assert not _is_deny(_run(f"gh release edit v1.26.0 --draft=false --target {tip}"))

    def test_detector_predicate(self):
        assert security_hooks._release_command_is_decoupled("gh release edit v1 --draft=false --target x")
        assert security_hooks._release_command_is_decoupled("gh release edit v1 --draft=false --tag y")
        assert not security_hooks._release_command_is_decoupled("gh release edit v1 --draft=false --latest")


class TestTagAnchoredMarker:
    """release-gate --ref <tag> writes a tag-anchored marker (records the tag +
    the commit it derefs to). The guard must then verify the PUBLISHED tag derefs
    to the SAME commit — LOCALLY (git -C, no network) — so a tag-based release is
    authorized on the commit CI validated, NOT on the moving branch tip. Fail-CLOSED
    on: no published tag, unresolvable tag, or tag→different-commit. Drives the REAL
    predicate against a REAL temp git repo with a REAL annotated tag."""

    @staticmethod
    def _make_repo_with_tag(tmp_path):
        import subprocess, os as _os
        repo = tmp_path / "srcrepo"
        repo.mkdir()
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path)}
        e = {**_os.environ, **env}
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=e)
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, env=e)
        subprocess.run(["git", "commit", "-qm", "released"], cwd=repo, check=True, env=e)
        tag_commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        # annotated tag on the released commit
        subprocess.run(["git", "tag", "-a", "v1.26.0", "-m", "Release v1.26.0"],
                       cwd=repo, check=True, env=e)
        # advance the branch tip PAST the tag (the re-pointed-tag scenario)
        (repo / "g.txt").write_text("y")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, env=e)
        subprocess.run(["git", "commit", "-qm", "later parallel commit"], cwd=repo, check=True, env=e)
        tip = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert tip != tag_commit, "tip must be ahead of the tag for this scenario"
        return repo, tag_commit, tip

    def _write_tag_marker(self, tmp_path, monkeypatch, head_sha, repo_root, tag):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        marker = tmp_path / "Projects" / "SwarmAI" / ".artifacts" / ".release-ci-green.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"head_sha": head_sha, "repo_root": str(repo_root),
                                      "tag": tag, "run_id": 1, "conclusion": "success", "ts": "t"}))

    def test_allow_when_published_tag_matches_marker_commit(self, tmp_path, monkeypatch):
        """The core fix: tag→CI-verified commit, even though branch tip has moved on."""
        repo, tag_commit, tip = self._make_repo_with_tag(tmp_path)
        self._write_tag_marker(tmp_path, monkeypatch, tag_commit, repo, "v1.26.0")
        ok, reason = security_hooks._release_marker_authorizes_head(published_tag="v1.26.0")
        assert ok is True, reason
        assert tag_commit[:8] in reason

    def test_deny_when_published_tag_derefs_to_different_commit(self, tmp_path, monkeypatch):
        """Marker attests the tag commit; a DIFFERENT (stale) commit in head_sha must DENY."""
        repo, tag_commit, tip = self._make_repo_with_tag(tmp_path)
        self._write_tag_marker(tmp_path, monkeypatch, tip, repo, "v1.26.0")  # marker head=tip, tag=commit
        ok, reason = security_hooks._release_marker_authorizes_head(published_tag="v1.26.0")
        assert ok is False and "!=" in reason

    def test_deny_when_no_published_tag_given(self, tmp_path, monkeypatch):
        """Tag-anchored marker + publish command names no tag → fail-CLOSED (not HEAD-fallback)."""
        repo, tag_commit, tip = self._make_repo_with_tag(tmp_path)
        self._write_tag_marker(tmp_path, monkeypatch, tag_commit, repo, "v1.26.0")
        ok, reason = security_hooks._release_marker_authorizes_head(published_tag=None)
        assert ok is False and "no tag" in reason.lower()

    def test_deny_when_published_tag_unresolvable(self, tmp_path, monkeypatch):
        """Published tag doesn't exist locally → fail-CLOSED (never a silent allow)."""
        repo, tag_commit, tip = self._make_repo_with_tag(tmp_path)
        self._write_tag_marker(tmp_path, monkeypatch, tag_commit, repo, "v1.26.0")
        ok, reason = security_hooks._release_marker_authorizes_head(published_tag="v99.99.99")
        assert ok is False and "resolve" in reason.lower()

    def test_full_guard_allows_edit_flip_on_matching_tag(self, tmp_path, monkeypatch):
        """End-to-end through release_publish_guard: the 7c flip command on a matching
        tag-anchored marker is APPROVED (drives real _extract_release_tag + predicate)."""
        repo, tag_commit, tip = self._make_repo_with_tag(tmp_path)
        self._write_tag_marker(tmp_path, monkeypatch, tag_commit, repo, "v1.26.0")
        monkeypatch.delenv("SWARM_RELEASE_GATE_FORCE", raising=False)
        r = _run("gh release edit v1.26.0 --draft=false --latest")
        assert not _is_deny(r), r

    def test_full_guard_denies_edit_flip_on_wrong_tag(self, tmp_path, monkeypatch):
        """A publish naming a tag whose commit != marker commit is DENIED end-to-end."""
        repo, tag_commit, tip = self._make_repo_with_tag(tmp_path)
        self._write_tag_marker(tmp_path, monkeypatch, tip, repo, "v1.26.0")  # stale head vs tag
        monkeypatch.delenv("SWARM_RELEASE_GATE_FORCE", raising=False)
        assert _is_deny(_run("gh release edit v1.26.0 --draft=false --latest"))
