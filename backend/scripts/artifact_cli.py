#!/usr/bin/env python3
"""CLI for DDD maintenance and the release CI gate.

Called by the agent via bash. A standalone script with no FastAPI dependency.
Pipeline runs are not here: Pipeline v1 runs live in ``"$SWARM_BACKEND_BIN" pipeline-v1``.

Usage:
    python artifact_cli.py bind --project SwarmAI
    python artifact_cli.py ddd-health|ddd-format ...
    python artifact_cli.py release-gate --poll|--status|--verify|--clear [--ref v1.2.3]

Public symbols:
- ``main`` — CLI entry point with subcommand dispatch.
- ``cmd_*`` — one handler per subcommand.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path so we can import core modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _get_workspace() -> Path:
    """Resolve workspace root from environment or default."""
    from config import get_swarmws
    return get_swarmws().expanduser().resolve()


def _validate_project_name(project: str, projects_root: Path) -> None:
    """Raise ValueError if ``project`` could escape ``projects_root``.

    Rejects obvious traversal tokens, then proves containment on RESOLVED paths
    with ``Path.is_relative_to`` (never a string ``startswith``: the macOS default
    FS is case-insensitive, so a raw prefix compare on resolved output is unsound).
    An unresolvable path (a NUL byte, a resolve error) counts as escaping — fail
    closed.
    """
    if (
        not project
        or not isinstance(project, str)
        or project in (".", "..")
        or "/" in project
        or "\\" in project
        or "\x00" in project
        or project != project.strip()
        or Path(project).is_absolute()
    ):
        raise ValueError(f"unsafe project name (path traversal): {project!r}")
    try:
        root = projects_root.resolve()
        contained = (projects_root / project).resolve().is_relative_to(root)
    except (OSError, ValueError):
        contained = False
    if not contained:
        raise ValueError(f"project dir escapes Projects/: {project!r}")


def _ensure_project_dir(project: str) -> Path:
    """Return ``Projects/<project>/``, guaranteeing it exists AND is REGISTERED.

    THE single choke point for minting a project directory from the pipeline CLI.
    Every command that would ``mkdir(parents=True)`` a ``Projects/<name>/`` subtree
    (release-gate today) must obtain the project dir
    THROUGH here first — so a ``Projects/<name>/`` can NEVER be created without its
    ``.project.json`` (which is what makes it visible to Brain Hub). This closes the
    "pseudo-DDD" class at the birth point rather than in one command (see run_be30a0c6).

    Idempotent + non-destructive: ``ensure_project_metadata`` is only-if-absent, so an
    already-registered project keeps its metadata/history untouched. Returns the dir.
    """
    from core.swarm_workspace_manager import ensure_project_metadata, reject_builtin_brain_name
    # Containment guard (adversarial-review HIGH): `project` is a CLI arg that upstream
    # tooling may fill from requirement/LLM text — an unvalidated segment like
    # "../../.ssh" would let mkdir(parents=True) escape the workspace.
    projects_root = _get_workspace() / "Projects"
    _validate_project_name(project, projects_root)
    reject_builtin_brain_name(project)   # before the mkdir: no empty Projects/General is left
    pdir = projects_root / project
    pdir.mkdir(parents=True, exist_ok=True)
    ensure_project_metadata(pdir, project)
    return pdir




# ── release-gate: the code-enforced CI-green gate for s_swarm-release 7b/7c ──────
#
# Design A (run_9fec1fb1, 2026-07-04; hook→flow-step run_d613bb27): `--poll` is the
# ONLY writer of the CI-green marker; `--verify` is the fail-closed reader that
# authorizes the publish (the "check CI" half + the "enforce" half, both in this CLI).
# `--verify` REPLACES the old `release_publish_guard` PreToolUse hook — a per-command
# product-wide hook was the wrong layer for SwarmAI's OWN release discipline, so the
# enforce step moved into the release flow (s_swarm-release 7c calls `--verify` before
# `gh release edit --draft=false`). Both `--poll` (network `gh run list`) and `--verify`
# (local marker read + `git rev-parse`) run as explicit one-time CLI calls, so neither
# reintroduces the foreground-timeout hang trap that lived in the 7b runbook poll.
#
# Marker: Projects/<project>/.artifacts/.release-ci-green.json
#   {"head_sha": "<40-char>", "repo_root": "<abs>", "run_id": <int>, "tag": "<opt>",
#    "conclusion": "success", "ts": "<iso>"}
# --verify authorizes publish IFF the marker attests the commit being released (tag-anchored
# via the marker's `tag`, else HEAD-anchored). A stale marker authorizes nothing — fail-closed.

def _release_marker_path(project: str) -> Path:
    return (_get_workspace() / "Projects" / project / ".artifacts"
            / ".release-ci-green.json")


def _release_marker_authorizes_head(
    marker: Path, repo_root: str | None, published_tag: str | None = None,
) -> tuple[bool, str]:
    """(authorized, reason). True IFF the CI-green marker attests the commit BEING
    PUBLISHED. Moved from security_hooks.release_publish_guard (run_d613bb27) — the
    pre-publish check is now a one-time gate in the release flow, not a per-command
    hook. Fail-CLOSED on any failure.

    Two modes, chosen by whether the marker records a `tag`:
    - **Tag-anchored** (marker has `tag`): resolve the PUBLISHED tag to its commit
      LOCALLY and require it == marker.head_sha (CI green on the exact shipped commit).
    - **HEAD-anchored** (legacy, no `tag`): require marker.head_sha == current HEAD.
    """
    if not marker.exists():
        return False, "no CI-green marker (run `artifact_cli.py release-gate --poll` until PASS)"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"marker unreadable: {type(e).__name__}"
    marker_head = data.get("head_sha") or ""
    # fail-CLOSED: the marker MUST record its own repo_root (--poll always writes it).
    # Do NOT fall back to the caller's cwd — a marker missing repo_root is a stale/
    # malformed marker and must BLOCK, exactly as the original release_publish_guard did.
    marker_repo = data.get("repo_root") or ""
    marker_tag = data.get("tag") or ""
    if not marker_head or not marker_repo:
        return False, "marker missing head_sha/repo_root (stale format — re-poll release-gate)"

    def _rev_parse(spec: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", marker_repo, "rev-parse", spec],
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            return None

    if marker_tag:
        if not published_tag:
            return (False,
                    f"marker is tag-anchored (tag {marker_tag}) but the publish names no "
                    "tag to verify — fail-closed (pass --ref <tag>, or re-poll release-gate)")
        published_commit = _rev_parse(f"{published_tag}^{{commit}}")
        if not published_commit:
            return (False,
                    f"cannot resolve published tag '{published_tag}' in {marker_repo} "
                    "(local tag missing?) — fail-closed")
        if published_commit != marker_head:
            return (False,
                    f"published tag {published_tag} → {published_commit[:8]} != CI-verified "
                    f"commit {marker_head[:8]} (marker tag {marker_tag}) — CI not green on the "
                    "commit being published")
        return (True,
                f"CI green on {marker_head[:8]} — published tag {published_tag} matches "
                f"marker tag {marker_tag} (run {data.get('run_id')})")

    head = _rev_parse("HEAD")
    if not head:
        return False, f"git HEAD unresolved in marker repo_root {marker_repo}"
    if marker_head != head:
        return False, f"marker HEAD {marker_head[:8]} != current HEAD {head[:8]} (stale — CI not green on THIS HEAD)"
    return True, f"CI green on HEAD {head[:8]} (run {data.get('run_id')})"


def cmd_release_gate(args, reg) -> None:
    """CI-green gate for the release skill's Stage 7b (design A, run_9fec1fb1).

    --poll: do ONE `gh run list` check for the CI run on the CURRENT git HEAD.
      - CI conclusion == success  → atomically write the marker, print PASS, exit 0
      - CI completed but red       → print BLOCK + failing conclusion, exit 1 (no marker)
      - CI not done / not registered → print WAIT, exit 3 (agent should sleep + re-poll)
    --clear: delete the marker (call after a successful publish, or to reset).
    --status: print current marker vs HEAD without touching CI.

    NEVER loops internally — the AGENT drives polling (one fast call per invocation),
    so no long-lived process can be foreground-timeout-killed. Bounded git/gh calls
    each carry a timeout.
    """

    marker = _release_marker_path(args.project)

    ref = getattr(args, "ref", None)  # optional tag/sha of the commit BEING RELEASED

    def _head_and_root() -> tuple[str | None, str | None]:
        """(commit sha, repo root) of the CWD's git repo. The CLI is run by the agent
        via `cd $SWARMAI_ROOT && ...`, so this resolves the SOURCE repo. Both are
        recorded in the marker so the (differently-cwd'd) hook can re-resolve against
        the SAME repo via `git -C <repo_root>` — never its own daemon cwd.

        When `--ref <tag-or-sha>` is given, resolve THAT ref to its commit
        (`<ref>^{commit}` — derefs an annotated tag to the commit it points at, and
        is idempotent on a plain sha). This is the fix for tag-based releases: the
        gate must verify the commit BEING RELEASED (the tag target), not the moving
        branch tip. No `--ref` → resolve HEAD (unchanged legacy behavior)."""
        target = f"{ref}^{{commit}}" if ref else "HEAD"
        try:
            r = subprocess.run(["git", "rev-parse", target, "--show-toplevel"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return None, None
            lines = r.stdout.strip().splitlines()
            head = lines[0].strip() if lines else None
            root = lines[1].strip() if len(lines) > 1 else None
            return head, root
        except (subprocess.TimeoutExpired, OSError):
            return None, None

    def _head() -> str | None:
        return _head_and_root()[0]

    if getattr(args, "clear", False):
        existed = marker.exists()
        marker.unlink(missing_ok=True)
        print(json.dumps({"cleared": existed, "marker": str(marker)}))
        return

    head, repo_root = _head_and_root()
    if not head:
        print(json.dumps({"error": "cannot resolve git HEAD (not a repo / git unavailable)"}),
              file=sys.stderr)
        sys.exit(2)

    if getattr(args, "status", False):
        cur = None
        if marker.exists():
            try:
                cur = json.loads(marker.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cur = None
        matches = bool(cur and cur.get("head_sha") == head)
        print(json.dumps({"head": head, "marker": cur, "authorizes_current_head": matches}))
        return

    if getattr(args, "verify", False):
        # --verify: the PRE-PUBLISH gate. Formerly the `release_publish_guard`
        # PreToolUse hook; moved into the release flow (s_swarm-release Stage 7b)
        # as a one-time check instead of a per-command runtime hook (a self-dev
        # concern does not belong in the product-wide hook layer). Fail-CLOSED:
        # authorizes the publish IFF the CI-green marker attests the commit being
        # released. Prints the verdict; exit 0 = authorized, exit 1 = blocked.
        # All-local (marker read + `git -C <repo_root> rev-parse`), never network.
        ok, reason = _release_marker_authorizes_head(
            marker, repo_root, published_tag=ref,
        )
        print(json.dumps({"state": "PASS" if ok else "BLOCK", "reason": reason,
                          "head": head, "published_ref": ref}),
              file=sys.stdout if ok else sys.stderr)
        sys.exit(0 if ok else 1)

    # --poll (default): one gh check for the CI run on the target commit.
    # With --ref, query by the resolved COMMIT (`gh run list --commit <sha>`), NOT
    # a limited `--branch main` window: a re-pointed tag's commit can sit many
    # commits behind the moving tip and fall outside `--limit N`, making the gate
    # falsely WAIT forever (the exact class of bug this fix exists to kill). The
    # commit-filtered query is exact and window-independent. No --ref → legacy
    # branch-tip scan (unchanged).
    gh_cmd = (["gh", "run", "list", "--commit", head, "--limit", "20",
               "--json", "databaseId,name,headSha,status,conclusion"]
              if ref else
              ["gh", "run", "list", "--branch", "main", "--limit", "8",
               "--json", "databaseId,name,headSha,status,conclusion"])
    try:
        r = subprocess.run(gh_cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        print(json.dumps({"state": "WAIT", "reason": "gh run list timed out (30s) — retry"}),
              file=sys.stderr)
        sys.exit(3)
    except OSError as e:
        print(json.dumps({"error": f"gh unavailable: {e}"}), file=sys.stderr)
        sys.exit(2)

    if r.returncode != 0:
        print(json.dumps({"state": "WAIT", "reason": f"gh error: {r.stderr.strip()[:160]}"}),
              file=sys.stderr)
        sys.exit(3)

    try:
        runs = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"state": "WAIT", "reason": "gh returned non-JSON — retry"}),
              file=sys.stderr)
        sys.exit(3)

    ci = next((x for x in runs
               if x.get("name") == "CI" and (x.get("headSha") or "").startswith(head[:8])),
              None)
    if ci is None:
        print(json.dumps({"state": "WAIT", "reason": "CI run not registered for HEAD yet",
                          "head": head}))
        sys.exit(3)

    status, concl = ci.get("status"), ci.get("conclusion")
    if status != "completed":
        print(json.dumps({"state": "WAIT", "status": status, "run_id": ci.get("databaseId"),
                          "head": head}))
        sys.exit(3)

    if concl == "success":
        # Atomic write (temp + replace) — mirror the file-write discipline used elsewhere.
        # Register the project first — this .artifacts/ mkdir would otherwise mint an
        # unregistered Projects/<name>/ (pseudo-DDD) if the project was never created.
        _ensure_project_dir(args.project)
        marker.parent.mkdir(parents=True, exist_ok=True)
        payload = {"head_sha": head, "repo_root": repo_root,
                   "run_id": ci.get("databaseId"),
                   "conclusion": "success",
                   "ts": datetime.now(timezone.utc).isoformat()}
        # Record the released ref (tag/sha) so the publish guard can verify the
        # PUBLISHED tag derefs to this same commit — the marker is authoritative,
        # the hook does one LOCAL deref and compares (no network). Absent when the
        # gate ran without --ref (legacy branch-tip release → hook falls back to HEAD).
        if ref:
            payload["tag"] = ref
        tmp = marker.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(marker)
        print(json.dumps({"state": "PASS", **payload, "marker": str(marker)}))
        return

    # completed + red
    print(json.dumps({"state": "BLOCK", "conclusion": concl,
                      "run_id": ci.get("databaseId"), "head": head,
                      "hint": "gh run view <run_id> --json jobs → fix → push → re-poll"}),
          file=sys.stderr)
    sys.exit(1)


def cmd_bind(args, reg) -> None:
    """CREATE→BIND→PULL: clone + index every binding of a project's bindings.yaml.

    The invokable entrypoint for the DDD binding layer — replaces the old python -c
    prose recipe. Calls core.ddd_bindings.bind_project (which loops ALL bindings with
    per-binding isolation) and prints one JSON outcome per binding. A pure-DDD project
    (no bindings.yaml) prints an empty list. Exit 1 only if EVERY binding failed
    (a mixed bound/deferred/failed set still exits 0 — deferred + isolated-failure are
    expected, not a run-level error).
    """
    from core.ddd_bindings import bind_project

    if sum(bool(getattr(args, k, None)) for k in ("local", "url", "relocate")) > 1:
        print(json.dumps({"error": "give one of --local, --url, --relocate"}), file=sys.stderr)
        sys.exit(2)
    if getattr(args, "local", None):
        _bind_local(args.project, args.local, source=getattr(args, "source", "agent"))
        return
    if getattr(args, "url", None) or getattr(args, "relocate", None):
        _bind_into_workspace(args)
        return
    try:
        outcomes = bind_project(args.project)
    except (FileNotFoundError, ValueError) as e:
        # A malformed bindings.yaml (ValueError names the bad field) is a run-level error.
        print(json.dumps({"error": f"bind failed for '{args.project}': {e}"}), file=sys.stderr)
        sys.exit(1)

    payload = [
        {"repo": o.repo, "kind": o.kind, "status": o.status, "error": o.error,
         "worktree": o.worktree, "code_intel_db": o.code_intel_db, "node_count": o.node_count}
        for o in outcomes
    ]
    # A bound repo is learned right away: its documents indexed in place and the
    # project surveyed (design 2026-10-27 §5.2–§5.3). Deterministic; a failure here
    # is reported, never undoes the bind.
    learned = None
    if any(o.status == "bound" for o in outcomes):
        from core.brain_bootstrap.doc_index import sync_project
        try:
            learned = _summarize_learned(sync_project(_get_workspace() / "Projects" / args.project))
        except Exception as e:  # noqa: BLE001
            learned = {"error": f"{type(e).__name__}: {e}"}
    # ... and its Project Agent draft gains the repo's code-change mode and checks
    # (design 2026-10-29-project-agent-standard-template §6.4), merged with what is there
    agent = None
    if any(o.status == "bound" for o in outcomes):
        from core.project_agent_draft import refresh_draft
        r = refresh_draft(_get_workspace() / "Projects" / args.project)
        agent = None if r is None else {"written": r.get("written"), "problems": r.get("problems")}
    print(json.dumps({"project": args.project, "bindings": payload, "learned": learned,
                      "project_agent": agent,
                      "counts": {
                          "bound": sum(1 for o in outcomes if o.status == "bound"),
                          "deferred": sum(1 for o in outcomes if o.status == "deferred"),
                          "failed": sum(1 for o in outcomes if o.status == "failed"),
                      }}, indent=2, default=str))
    # Run-level failure ONLY when there were bindings and every one failed.
    if outcomes and all(o.status == "failed" for o in outcomes):
        sys.exit(1)


def _bind_into_workspace(args) -> None:
    """``bind --url``: clone the repository into the repository workspace
    (``core.repo_placement``), then read it in place like ``--local``. ``bind
    --relocate <repo>``: move a binding's bind-managed mirror into the workspace, then
    the same. Either way the Brain ends with one checkout the person can see and work in,
    where a Hive's returned commits land (fixing-spec 2026-11-02 G1).

    Everything that can refuse runs before anything is created or moved (the project,
    the URL, the branch, the workspace folder, the target); a binding that already has
    this remote is reused (a mirror is relocated, a checkout is read again); and when
    the in-place read is refused after all, the clone is removed or the move undone."""
    from core import repo_placement as rp
    projects = _get_workspace() / "Projects"
    project = _project_name(projects, args.project)
    pdir = projects / project
    source = getattr(args, "source", "agent")
    fresh: Path | None = None
    moved = None
    try:
        if args.url:
            url = rp.normalize_url(args.url)
            branch = rp.check_branch(getattr(args, "branch", None))
            rp.check_repos_dir()
            existing = rp.binding_for_remote(pdir, url)
            kind = rp.placement(pdir).get(existing, {}).get("checkout") if existing else None
            if existing and kind == "mirror":
                moved = rp.relocate(pdir, existing)
                root, cloned = moved.target, False
            elif existing:
                from core.project_agent import repo_roots
                root = repo_roots(pdir).get(existing)
                if root is None:
                    raise rp.PlacementError(f"{existing} binds this remote but its checkout is "
                                            "not on this machine; bind it with --local <path>")
                cloned = False
            else:
                root, cloned = rp.clone_into_workspace(url, branch)
                fresh = root if cloned else None
        else:
            moved = rp.relocate(pdir, args.relocate)
            root, cloned = moved.target, False
    except rp.PlacementError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"checkout": str(root), "cloned": cloned, "relocated": moved is not None}),
          file=sys.stderr)
    try:
        _bind_local(project, str(root), source=source)
    except SystemExit as e:
        if e.code:                       # refused after all: leave nothing half-done
            if fresh is not None:
                rp._remove_fresh(fresh)
            if moved is not None:
                moved.undo()
            print(json.dumps({"error": "nothing was kept: "
                              + ("the clone was removed" if fresh else "the mirror was put back"
                                 if moved else "no change")}), file=sys.stderr)
        raise


def _project_name(projects: Path, project: str) -> str:
    """The project's directory name (exact, else the one case-insensitive match); exits 1
    when there is none. The local-roots registry is keyed by it, the name Brain Hub reads."""
    names = [d.name for d in projects.iterdir() if d.is_dir()] if projects.is_dir() else []
    match = [n for n in names if n == project] or [n for n in names if n.lower() == project.lower()]
    if len(match) != 1:
        print(json.dumps({"error": f"no project named {project!r}"}), file=sys.stderr)
        sys.exit(1)
    return match[0]


def _bind_local(project: str, path: str, *, source: str = "agent") -> None:
    """Read the user's own checkout *path* into *project* in place: it is owned now
    (``local_roots.declare``; validated, refused if another Brain reads it) and its
    graph is built by a detached child, so this returns at once (design
    2026-10-29-code-intel-auto D1). The Brain Hub card lists it with a Remove."""
    from core.code_intel.local_roots import LocalRootError, declare
    projects = _get_workspace() / "Projects"
    project = _project_name(projects, project)
    try:
        entry, added = declare(project, path, source=source)
    except LocalRootError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
    from core.code_intel import owned_graphs
    from core.code_intel.build import spawn_build
    repo = next((ref.repo for ref in owned_graphs(projects / project)
                 if ref.repo_root is not None and str(ref.repo_root) == entry["path"]), None)
    building = False
    if repo is not None:
        try:
            spawn_build(project, repo=repo, only_if_stale=True)
            building = True
        except OSError as e:
            print(json.dumps({"warning": f"the graph build could not start: {e}; the "
                              "hourly code-intel job builds it"}), file=sys.stderr)
    # the rest of the Brain finds its repositories in bindings.yaml (the Project Agent's
    # code-change mode and checks, a Hive task's repositories, P6): record it there,
    # then learn it and redraft the Project Agent like a cloned repo (cmd_bind)
    from core import local_binding
    learned = agent = None
    try:
        local_binding.record(projects / project, Path(entry["path"]))
    except (OSError, ValueError) as e:
        print(json.dumps({"warning": f"not recorded in bindings.yaml: {e}"}), file=sys.stderr)
    else:
        from core.brain_bootstrap.doc_index import sync_project
        try:
            learned = _summarize_learned(sync_project(projects / project))
        except Exception as e:  # noqa: BLE001 — reported, never undoes the bind
            learned = {"error": f"{type(e).__name__}: {e}"}
        from core.project_agent_draft import refresh_draft
        r = refresh_draft(projects / project)
        agent = None if r is None else {"written": r.get("written"), "problems": r.get("problems")}
    print(json.dumps({"project": project, "local_root": entry, "added": added,
                      "graph": "building in a child process" if building else
                               "built by the hourly code-intel job",
                      "learned": learned, "project_agent": agent}, indent=2, default=str))


def _summarize_learned(sync: dict) -> dict:
    """The CLI's view of ``doc_index.sync_project``: per repo, what was indexed and
    what the survey found — counts and paths, never the survey's text."""
    from core.brain_bootstrap.survey import read_survey
    out: dict = {"index": sync.get("repos"), "pruned": sync.get("pruned"),
                 "survey": sync.get("survey")}
    if sync.get("survey_error"):
        out["survey_error"] = sync["survey_error"]
    data = read_survey(Path(sync["survey"]).parents[2]) if sync.get("survey") else None
    if data:
        out["repos"] = [{
            "repo": r.get("repo"), "head": (r.get("identity") or {}).get("head"),
            "docs_by_tier": {str(t): sum(1 for d in r.get("docs", []) if d.get("tier") == t)
                             for t in (1, 2, 3, 4)},
            "inlined": sum(1 for d in r.get("docs", []) if d.get("included")),
            "flagged": [d["path"] for d in r.get("docs", []) if d.get("flags")],
            "api_contracts": len(r.get("api_contracts", [])),
            "commands": len(r.get("commands", [])),
            "hotspots": len((r.get("history") or {}).get("hotspots", [])),
            "code_shape": "unavailable" if "unavailable" in (r.get("code_shape") or {}) else "ok",
            "errors": r.get("errors"),
        } for r in data.get("repos", [])]
    return out


def cmd_survey(args, reg) -> None:
    """Survey every repo a project owns and index its documents in place.

    Design 2026-10-27-new-brain-initial-ddd §5.2–§5.3. Writes
    ``Projects/<p>/.artifacts/bootstrap/survey.json`` and syncs the repo documents into
    recall under ``bind:<project>/<repo>/``. No LLM, no network, nothing in a repo runs.
    Prints a summary (counts and paths); the agent reads survey.json itself. A project
    with no repo gets an empty survey. Exit 1 when a repo part failed.
    """
    if "/" in args.project or "\\" in args.project or args.project in ("", ".", ".."):
        print(json.dumps({"error": f"invalid project name '{args.project}'"}), file=sys.stderr)
        sys.exit(2)
    project_dir = _get_workspace() / "Projects" / args.project
    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project '{args.project}' not found"}), file=sys.stderr)
        sys.exit(2)
    from core.brain_bootstrap.doc_index import sync_project
    summary = _summarize_learned(sync_project(project_dir))
    print(json.dumps({"project": args.project, **summary}, indent=2, default=str))
    failed = (summary.get("survey_error")
              or any(isinstance(v, dict) and v.get("error") for v in (summary.get("index") or {}).values())
              or any(r.get("errors") for r in summary.get("repos") or []))
    if failed:
        sys.exit(1)


def cmd_bootstrap_apply(args, reg) -> None:
    """Write a newborn brain's first entries from its bootstrap-entries.json.

    Design 2026-10-27-new-brain-initial-ddd §5.5: deterministic checks (section and
    type, required fields, sources a tracked repo file / file: / url: / user), written
    only while the brain is setting up and only into docs that hold nothing but the
    skeleton and earlier bootstrap entries. Prints the report (written, rejected with
    reasons, the PRODUCT sections still to ask about or record as gaps). Exit 1 when
    an entry was rejected or a doc refused; 2 when nothing could run.
    """
    if "/" in args.project or "\\" in args.project or args.project in ("", ".", ".."):
        print(json.dumps({"error": f"invalid project name '{args.project}'"}), file=sys.stderr)
        sys.exit(2)
    project_dir = _get_workspace() / "Projects" / args.project
    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project '{args.project}' not found"}), file=sys.stderr)
        sys.exit(2)
    from core.brain_bootstrap.apply import apply_entries
    report = apply_entries(project_dir, args.entries, dry_run=args.dry_run)
    print(json.dumps({"project": args.project, **report}, indent=2, ensure_ascii=False))
    if report.get("status") == "refused":
        sys.exit(2)
    if report.get("rejected") or report.get("refused_docs"):
        sys.exit(1)


def cmd_entry_contract(args, reg) -> None:
    """Print a brain's entry contract and write it to .artifacts/bootstrap/entry-contract.json.

    Design 2026-10-27-new-brain-initial-ddd r12: the format of bootstrap-entries.json,
    generated from the rules bootstrap-apply enforces (core.brain_bootstrap.apply
    .entry_contract), so the agent reads the rules themselves, not a copy. Works for
    every brain, with or without a repo. Exit 2 when the project does not exist.
    """
    if "/" in args.project or "\\" in args.project or args.project in ("", ".", ".."):
        print(json.dumps({"error": f"invalid project name '{args.project}'"}), file=sys.stderr)
        sys.exit(2)
    project_dir = _get_workspace() / "Projects" / args.project
    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project '{args.project}' not found"}), file=sys.stderr)
        sys.exit(2)
    from core.brain_bootstrap.apply import entry_contract, write_entry_contract
    try:
        path = write_entry_contract(project_dir)
    except OSError as exc:
        print(json.dumps({"error": f"cannot write the contract: {type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"project": args.project, "written": str(path),
                      "contract": entry_contract(project_dir)}, indent=2, ensure_ascii=False))


def cmd_ddd_format(args, reg) -> None:
    """Put a brain's four docs in the shape CDF writes (``core.ddd_format``): typed
    entries with their metadata line, in the skeleton sections their type belongs to;
    everything else moves, verbatim, to ``2-understanding/knowledge/``. Without
    ``--apply``: the report only. Exit 1 when a doc is still out of shape after it."""
    from core import ddd_format
    project_dir = _get_workspace() / "Projects" / args.project
    if "/" in args.project or args.project in (".", "..") or not project_dir.is_dir():
        print(json.dumps({"error": f"Brain '{args.project}' not found"}), file=sys.stderr)
        sys.exit(2)
    report = ddd_format.apply_project(project_dir, apply=args.apply)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if any(v.get("after") or v.get("error") for v in report["docs"].values()):
        sys.exit(1)


def cmd_project_agent_draft(args, reg) -> None:
    """Draft a Brain's Project Agent definition (design 2026-10-28-project-agent-flow §5).

    Without ``--apply``: print the facts (repos, skills, gates, declared commands with
    ids, docs) and the proposal format. With ``--apply <proposal.json>`` (``-`` = an
    empty proposal): build the ``schema: 2`` draft, drop each judged row whose source
    does not exist and each command its source does not declare, validate it and write
    ``project-agent.yaml`` (``--dry-run``: print, write nothing), merged with the
    definition there unless ``--fresh``. ``--general`` targets the General Brain (the
    Runner Agent). Exit 1 when the draft has problems; 2 when nothing could run.
    """
    from core.project_agent import GENERAL_BRAIN, general_brain_dir
    if args.general:
        project_dir, name = general_brain_dir(_get_workspace()), GENERAL_BRAIN
    else:
        if not args.project or "/" in args.project or "\\" in args.project or args.project in (".", ".."):
            print(json.dumps({"error": "give --project <name> or --general"}), file=sys.stderr)
            sys.exit(2)
        project_dir, name = _get_workspace() / "Projects" / args.project, args.project
    if not project_dir.is_dir():
        print(json.dumps({"error": f"Brain '{name}' not found"}), file=sys.stderr)
        sys.exit(2)
    from core import project_agent_draft as pad
    if not args.apply:
        print(json.dumps({"brain": name, "facts": pad.survey(project_dir)}, indent=2, ensure_ascii=False))
        return
    proposal: object = {}
    if args.apply != "-":
        try:
            proposal = json.loads(Path(args.apply).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": f"cannot read the proposal: {type(exc).__name__}: {exc}"}),
                  file=sys.stderr)
            sys.exit(2)
    report = pad.apply(project_dir, proposal, dry_run=args.dry_run, merge=not args.fresh)
    print(json.dumps({"brain": name, **report}, indent=2, ensure_ascii=False))
    if report["problems"]:
        sys.exit(1)


def cmd_classify_starter(args, reg) -> None:
    """Which role a New Brain starter path takes: GOVERN (a git work tree, or a folder
    of them: bind each with ``bind --local``), DISTILL (a single file: read it into
    entries) or SHELF (a folder of documents: mount it for recall). The same answer
    New Brain shows (``local_roots.classify_starter``); the person can still change
    the role. Design 2026-10-29-code-intel-auto D2."""
    from core.code_intel.local_roots import LocalRootError, classify_starter
    try:
        out = classify_starter(args.path)
    except LocalRootError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(2)
    print(json.dumps(out))


def cmd_ddd_skeleton_align(args, reg) -> None:
    """List (default) or add (--apply) the skeleton sections an existing brain lacks.

    Design 2026-10-27-new-brain-initial-ddd §5.10. A heading that only resembles a
    missing section (e.g. "Key Subsystems (architecture shape)") is reported under
    `rename` and never gets an empty twin; existing bytes are never changed. Exit 1
    when a doc is missing or a rename remains, 0 otherwise.
    """
    if "/" in args.project or "\\" in args.project or args.project in ("", ".", ".."):
        print(json.dumps({"error": f"invalid project name '{args.project}'"}), file=sys.stderr)
        sys.exit(2)
    project_dir = _get_workspace() / "Projects" / args.project
    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project '{args.project}' not found"}), file=sys.stderr)
        sys.exit(2)
    from core.ddd_skeleton import align_project
    report = align_project(project_dir, apply=args.apply)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["unresolved"]:
        # A missing doc or a heading to rename is left for the person: say so by exit code.
        sys.exit(1)


def cmd_ddd_health(args, reg) -> None:
    """5-dimensional DDD health scoring per section."""
    project_dir = _get_workspace() / "Projects" / args.project
    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project '{args.project}' not found"}))
        return

    from core.ddd_health import compute_section_health

    # persist=False: this CLI just PRINTS the scores (a read/inspect command) —
    # it must not write section_health.json as a side effect; the scheduled
    # ddd_refresh job owns the snapshot. #5 fix, run_e90535ea.
    result = compute_section_health(project_dir, persist=False)
    print(json.dumps(result, indent=2, default=str))


def _refuse_if_worktree_copy(script: str) -> None:
    """A copy of this tool inside a run worktree must never write the workspace that
    owns that worktree (it may be half-edited). Against another workspace (a test's
    temp SwarmWS) it is the code under test and may run."""
    from core import run_worktree as rw
    hit = rw.run_for_path(script)
    if hit is None:
        return
    rid, repo = hit
    ws = os.environ.get("SWARM_WORKSPACE")
    if ws and not rw.owned_by(rid, Path(ws).expanduser()):
        return
    print(json.dumps({"ok": False, "error": "this tool copy lives inside run worktree "
                      f"{rw.wt_path(rid, repo)}; run the main checkout's copy instead"}))
    sys.exit(2)


def main() -> None:
    import functools
    parser = argparse.ArgumentParser(
        description="SwarmAI DDD maintenance and release-gate CLI", allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", required=True,
                                parser_class=functools.partial(argparse.ArgumentParser,
                                                               allow_abbrev=False))

    # bind (CREATE→BIND→PULL: clone + index every binding of a project's bindings.yaml)
    p_bind = sub.add_parser("bind", help="PULL: clone + code-intel every binding in a project's bindings.yaml")
    p_bind.add_argument("--project", required=True, help="Project whose bindings.yaml to PULL")
    p_bind.add_argument("--local", default=None, metavar="PATH",
                        help="read the user's own checkout PATH in place: it becomes a local root "
                             "of the project now and its graph is built by a child process "
                             "(instead of PULLing bindings.yaml)")
    p_bind.add_argument("--url", default=None, metavar="URL",
                        help="clone the repository at URL into the repository workspace "
                             "(repos_dir, default ~/SwarmAI-Repos) and read it in place like "
                             "--local; the same repository already there is reused")
    p_bind.add_argument("--branch", default=None, help="with --url: the branch to check out")
    p_bind.add_argument("--relocate", default=None, metavar="REPO",
                        help="move REPO's bind-managed mirror (~/.swarm-ai/bindings/REPO) into "
                             "the repository workspace and read it there; refused when it holds "
                             "unpushed work")
    p_bind.add_argument("--source", default="agent", choices=("agent", "new-brain"),
                        help="with --local / --url: who declared it, shown on the Brain Hub card")

    # survey (design 2026-10-27 §5.2-§5.3: inventory the owned repos, index their docs in place)
    p_survey = sub.add_parser("survey", help="Survey a project's repos into survey.json and index their docs for recall")
    p_survey.add_argument("--project", required=True)

    # bootstrap-apply (design 2026-10-27 §5.5: a newborn brain's first entries, checked)
    p_bapply = sub.add_parser("bootstrap-apply", help="Write a newborn brain's checked first entries from bootstrap-entries.json")
    p_bapply.add_argument("--project", required=True)
    p_bapply.add_argument("--entries", default=None, help="Entries file (default: .artifacts/bootstrap/bootstrap-entries.json)")
    p_bapply.add_argument("--dry-run", action="store_true", help="Check and report; write nothing")

    # entry-contract (design 2026-10-27 r12: the entries file's format, from bootstrap-apply's rules)
    p_contract = sub.add_parser("entry-contract", help="Print and write a brain's bootstrap entry contract")
    p_contract.add_argument("--project", required=True)

    # project-agent-draft (design 2026-10-28-project-agent-flow §5: a Brain's Project Agent draft)
    p_pad = sub.add_parser("project-agent-draft", help="Print the facts for, or write, a Brain's Project Agent draft")
    p_pad_target = p_pad.add_mutually_exclusive_group(required=True)
    p_pad_target.add_argument("--project", default=None)
    p_pad_target.add_argument("--general", action="store_true", help="The General Brain (Runner Agent)")
    p_pad.add_argument("--apply", default=None, help="Proposal JSON file ('-' = no proposal: deterministic fields only)")
    p_pad.add_argument("--dry-run", action="store_true", help="Build and check; write nothing")
    p_pad.add_argument("--fresh", action="store_true",
                       help="Start over: keep nothing of the earlier draft (default: keep what the proposal does not decide)")

    # classify-starter (design 2026-10-27 §5.10: GOVERN / DISTILL / SHELF for a starter path)
    p_cls = sub.add_parser("classify-starter", help="GOVERN / DISTILL / SHELF for a New Brain starter path")
    p_cls.add_argument("path")

    # ddd-skeleton-align (design 2026-10-27 §5.10: give an existing brain the skeleton's sections)
    p_align = sub.add_parser("ddd-skeleton-align", help="List (or --apply: append) the skeleton sections a brain's 4 docs lack")
    p_align.add_argument("--project", required=True)
    p_align.add_argument("--apply", action="store_true", help="Append the missing sections (default: dry-run report)")

    # ddd-health
    p_fmt = sub.add_parser("ddd-format", help="Report (or --apply: move into) the shape CDF writes a brain's 4 docs in")
    p_fmt.add_argument("--project", required=True)
    p_fmt.add_argument("--apply", action="store_true",
                       help="write: reference material to 2-understanding/knowledge/, then each doc")
    p_ddd_health = sub.add_parser("ddd-health", help="5-dimensional DDD health scoring per section")
    p_ddd_health.add_argument("--project", required=True)

    # release-gate (run_9fec1fb1: code-enforced CI-green gate for s_swarm-release 7b)
    p_rel_gate = sub.add_parser("release-gate", help="CI-green gate: poll CI for HEAD, write marker on green (authorizes gh release create)")
    p_rel_gate.add_argument("--project", default="SwarmAI", help="Project owning the marker (default SwarmAI)")
    p_rel_gate.add_argument("--poll", action="store_true", help="One gh run list check for HEAD; green→write marker (exit 0), red→exit 1, not-done→exit 3")
    p_rel_gate.add_argument("--status", action="store_true", help="Print marker vs HEAD (does it authorize current HEAD?) without touching CI")
    p_rel_gate.add_argument("--verify", action="store_true", help="PRE-PUBLISH gate (fail-closed): authorize the publish IFF the CI-green marker attests the commit being released. exit 0=authorized, 1=blocked. All-local, no network. Call in s_swarm-release Stage 7b BEFORE `gh release create/edit`. (Replaces the release_publish_guard hook.)")
    p_rel_gate.add_argument("--clear", action="store_true", help="Delete the marker (call after publish, or to reset)")
    p_rel_gate.add_argument("--ref", default=None, help="Tag or sha of the commit BEING RELEASED (e.g. v1.26.0). Resolves <ref>^{commit}, polls CI for THAT commit, records it (+the tag) in the marker. Omit for a branch-tip release (legacy HEAD behavior).")


    _refuse_if_worktree_copy(__file__)
    args = parser.parse_args()
    handlers = {
        "bind": cmd_bind,
        "survey": cmd_survey,
        "bootstrap-apply": cmd_bootstrap_apply,
        "entry-contract": cmd_entry_contract,
        "project-agent-draft": cmd_project_agent_draft,
        "classify-starter": cmd_classify_starter,
        "ddd-skeleton-align": cmd_ddd_skeleton_align,
        "ddd-format": cmd_ddd_format,
        "ddd-health": cmd_ddd_health,
        "release-gate": cmd_release_gate,
    }
    try:
        handlers[args.command](args, None)  # 2nd arg: unused, kept for the handlers' signature
    except json.JSONDecodeError:
        raise                                # a data error keeps its traceback
    except ValueError as e:
        # an unsafe identifier is refused with the CLI's JSON error, not a traceback
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
