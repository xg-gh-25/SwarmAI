#!/usr/bin/env python3
"""CLI for the artifact registry, DDD maintenance and the release CI gate.

Called by the agent via bash. A standalone script with no FastAPI dependency.
Pipeline runs are not here: Pipeline v1 runs live in ``"$SWARM_BACKEND_BIN" pipeline-v1``.

Usage:
    python artifact_cli.py discover --project SwarmAI --types research,alternatives [--full]
    python artifact_cli.py publish --project SwarmAI --type evaluation \\
        --producer s_evaluate --summary "GO" --data '{"roi": 3.2}'
    python artifact_cli.py learn --project SwarmAI --evaluation-id art_xxx --outcome success
    python artifact_cli.py projects
    python artifact_cli.py bind --project SwarmAI
    python artifact_cli.py ddd-health|ddd-retire|ddd-noise ...
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

from core.artifact_registry import ArtifactRegistry



def _get_workspace() -> Path:
    """Resolve workspace root from environment or default."""
    from config import get_swarmws
    return get_swarmws().expanduser().resolve()


def cmd_discover(args, reg: ArtifactRegistry) -> None:
    """Discover active artifacts of given types."""
    types = [t.strip() for t in args.types.split(",") if t.strip()]
    artifacts = reg.discover(args.project, *types)

    if not artifacts:
        print(json.dumps({"artifacts": [], "count": 0}))
        return

    result = []
    for a in artifacts:
        entry = {
            "id": a.id,
            "type": a.type,
            "producer": a.producer,
            "summary": a.summary,
            "file": a.file,
        }
        # Optionally load full data
        if args.full:
            artifact_dir = (
                _get_workspace() / "Projects" / args.project / ".artifacts"
            )
            data_file = artifact_dir / a.file
            if data_file.exists():
                try:
                    entry["data"] = json.loads(
                        data_file.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    pass
        result.append(entry)

    print(json.dumps({"artifacts": result, "count": len(result)}, indent=2))



def _read_json_arg(raw: str, argname: str) -> str:
    """Resolve a CLI JSON argument's RAW-STRING source — @path / @- / inline.

    Used by ``publish --data``: it reads a
    file. ``@PATH`` reads the file, ``@-`` reads stdin, anything else is returned
    verbatim (a JSON value never starts with '@', so the prefix is unambiguous).
    It does not ``json.loads``: the caller parses; ``argname`` names the argument
    in the read-failure message.
    """
    if isinstance(raw, str) and raw.startswith("@"):
        try:
            if raw == "@-":
                return sys.stdin.read()
            return Path(raw[1:]).expanduser().read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            print(json.dumps({"error": f"Could not read {argname} source {raw!r}: {e}"}),
                  file=sys.stderr)
            sys.exit(1)
    return raw


def cmd_publish(args, reg: ArtifactRegistry) -> None:
    """Publish a bare artifact (no run, no stage): manifest-tracked, read by id.
    Standalone skills (s_evaluate on ESCALATE, s_qa standalone) emit an
    artifact this way; Pipeline v1 run documents go through ``pipeline-v1``."""
    _raw = _read_json_arg(args.data, "--data")
    try:
        data = json.loads(_raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON data: {e}"}), file=sys.stderr)
        sys.exit(1)
    try:
        # the trust boundary: an unsafe project name writes nothing
        reg._validate_path_identifiers(args.project)
    except ValueError as e:
        print(json.dumps({"error": f"unsafe path identifier (rejected before write): {e}"}),
              file=sys.stderr)
        sys.exit(3)
    try:
        artifact_id = reg.publish(
            project=args.project, artifact_type=args.type, data=data,
            producer=args.producer, summary=args.summary, topic=args.topic or "")
    except (ValueError, FileNotFoundError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
    if getattr(args, "quiet", False):
        print(json.dumps({"artifact_id": artifact_id}))
    else:
        print(json.dumps({"artifact_id": artifact_id, "project": args.project}))


def cmd_learn(args, reg: ArtifactRegistry) -> None:
    """Record outcome of a pipeline run for learning feedback."""
    lessons = [l.strip() for l in (args.lessons or "").split(";") if l.strip()]
    reg.record_outcome(
        project=args.project,
        evaluation_id=args.evaluation_id,
        outcome=args.outcome,
        actual_effort=args.actual_effort,
        lessons=lessons or None,
    )
    print(json.dumps({
        "project": args.project,
        "evaluation_id": args.evaluation_id,
        "outcome": args.outcome,
        "recorded": True,
    }))


def cmd_projects(args, reg: ArtifactRegistry) -> None:
    """List all projects with pipeline status."""
    statuses = reg.list_projects()
    result = [
        {
            "project": s.project,
            "pipeline_state": s.pipeline_state,
            "artifact_count": s.artifact_count,
            "active_artifact_count": s.active_artifact_count,
            "latest_artifact": s.latest_artifact,
        }
        for s in statuses
    ]
    print(json.dumps({"projects": result, "count": len(result)}, indent=2))


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
    from core.swarm_workspace_manager import ensure_project_metadata
    # Containment guard (adversarial-review HIGH): `project` is a CLI arg that upstream
    # tooling may fill from requirement/LLM text — an unvalidated segment like
    # "../../.ssh" would let mkdir(parents=True) escape the workspace.
    # SSOT (run_44a06138, Gate-1 amendment A): delegate the project-name
    # containment to ArtifactRegistry._validate_path_identifiers — the ONE place
    # that owns "is this caller-supplied identifier safe to build a path from?"
    # Do NOT re-derive the resolve()/relative_to idiom here (a 2nd copy drifts).
    from core.artifact_registry import ArtifactRegistry
    projects_root = _get_workspace() / "Projects"
    ArtifactRegistry(_get_workspace())._validate_path_identifiers(project)
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
    import subprocess
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


def cmd_release_gate(args, reg: "ArtifactRegistry") -> None:
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
    import subprocess
    from datetime import datetime, timezone

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
    print(json.dumps({"project": args.project, "bindings": payload, "learned": learned,
                      "counts": {
                          "bound": sum(1 for o in outcomes if o.status == "bound"),
                          "deferred": sum(1 for o in outcomes if o.status == "deferred"),
                          "failed": sum(1 for o in outcomes if o.status == "failed"),
                      }}, indent=2, default=str))
    # Run-level failure ONLY when there were bindings and every one failed.
    if outcomes and all(o.status == "failed" for o in outcomes):
        sys.exit(1)


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


def cmd_project_agent_draft(args, reg) -> None:
    """Draft a Brain's Project Agent definition (design 2026-10-28-project-agent-flow §5).

    Without ``--apply``: print the facts (repos, skills, gates, declared commands with
    ids, docs) and the proposal format. With ``--apply <proposal.json>`` (``-`` = an
    empty proposal): build the ``schema: 2`` draft, drop each judged row whose source
    does not exist and each command its source does not declare, validate it and write
    ``project-agent.yaml`` (``--dry-run``: print, write nothing). A reviewed definition
    is never replaced. ``--general`` targets the General Brain (the Runner Agent).
    Exit 1 when the draft has problems or a reviewed definition is there; 2 when
    nothing could run.
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
    report = pad.apply(project_dir, proposal, dry_run=args.dry_run)
    print(json.dumps({"brain": name, **report}, indent=2, ensure_ascii=False))
    if report["problems"]:
        sys.exit(1)


def cmd_classify_starter(args, reg) -> None:
    """Which role a New Brain starter path takes: GOVERN (a repo: an asset to bind),
    DISTILL (a single file: read it into entries) or SHELF (a folder of documents: mount
    it for recall). Reuses library_mounts.judge_mount_kind (repo markers); the person
    can still change the role. Design 2026-10-27 §5.10."""
    from core.library_mounts import judge_mount_kind
    path = Path(args.path).expanduser()
    if path.is_file():
        role, why = "DISTILL", "a single file"
    elif not path.is_dir():
        print(json.dumps({"error": f"no such file or folder: {args.path}"}), file=sys.stderr)
        sys.exit(2)
    elif judge_mount_kind(str(path)) == "code":
        role, why = "GOVERN", "carries a repo marker (.git, package.json, pyproject.toml, …)"
    else:
        role, why = "SHELF", "a folder without a repo marker"
    print(json.dumps({"path": str(path), "role": role, "why": why}))


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


def cmd_ddd_retire(args, reg) -> None:
    """Report the one named retirement candidate without mutating the corpus.

    CDF stage B retires the legacy apply path. User edits go through
    governed_edit and automated maintenance is read-only.
    """
    from core.ddd_entry_lifecycle import retire_entry, RetireError, MEMORY_EVERGREEN_SECTIONS

    ws = _get_workspace()
    p = Path(args.file)
    if not p.is_absolute():
        p = ws / args.file
    if not p.exists():
        print(json.dumps({"error": f"file not found: {p}"}), file=sys.stderr)
        sys.exit(1)

    content = p.read_text(encoding="utf-8")
    archive_name = args.archive_name or (
        "MEMORY-archive.md" if p.name == "MEMORY.md" else "IMPROVEMENT-archive.md"
    )
    try:
        report = retire_entry(
            content, title=args.title, section=args.section,
            project_dir=p.parent, archive_name=archive_name,
            source_path=None,
            dry_run=True, force=bool(args.force),
            # MEMORY.md's evergreen sections (Open Threads, Standing Preferences,
            # etc.) need --force parity with the autonomous reclaim path.
            evergreen_sections=MEMORY_EVERGREEN_SECTIONS if p.name == "MEMORY.md" else None,
        )
    except RetireError as e:
        print(json.dumps({"error": str(e), "retired": False}), file=sys.stderr)
        sys.exit(1)

    print(json.dumps({
        "retired": False,
        "dry_run": True,
        "candidate": report.candidates[0] if report.candidates else None,
        "archived": [],
        "archive_file": archive_name,
        "hint": "report only — edit through governed workspace APIs",
    }))


def cmd_ddd_noise(args, reg) -> None:
    """Per-doc DDD noise gate (M0 ②): reclaimable noise_rate + PASS/FAIL verdict.

    Measures the GATE metric (raw noise MINUS keep-class) so a doc heavy in
    permanent-but-dormant knowledge (COE/principles) doesn't FAIL spuriously.
    Verdict string (PASS/FAIL) is in stdout for the golden_set canary to assert.
    Always exits 0 — the canary keys off the string, not the exit code.
    """
    from datetime import date
    from core.ddd_entry_lifecycle import (
        parse_entries, compute_reclaimable_noise, NOISE_FAIL_THRESHOLD,
        MEMORY_EVERGREEN_SECTIONS,
    )

    ws = _get_workspace()
    today = date.today()

    # Build the doc list: --doc (one file) or default scan (MEMORY + all IMPROVEMENT).
    docs: list[tuple[str, "Path", "frozenset[str] | None"]] = []
    if getattr(args, "doc", None):
        p = Path(args.doc)
        if not p.is_absolute():
            p = ws / args.doc
        ever = MEMORY_EVERGREEN_SECTIONS if p.name == "MEMORY.md" else None
        docs.append((args.doc, p, ever))
    else:
        mem = ws / ".context" / "MEMORY.md"
        if mem.exists():
            docs.append(("MEMORY.md", mem, MEMORY_EVERGREEN_SECTIONS))
        proj_dir = ws / "Projects"
        if proj_dir.is_dir():
            for imp in sorted(proj_dir.glob("*/IMPROVEMENT.md")):
                docs.append((str(imp.relative_to(ws)), imp, None))

    results = []
    worst_fail = False
    for name, path, ever in docs:
        if not path.exists():
            results.append({"doc": name, "error": "not found"})
            continue
        report = compute_reclaimable_noise(
            parse_entries(path.read_text(encoding="utf-8")), today,
            evergreen_sections=ever,
        )
        verdict = "FAIL" if report.noise_rate > NOISE_FAIL_THRESHOLD else "PASS"
        if verdict == "FAIL":
            worst_fail = True
        results.append({
            "doc": name, "total": report.total, "noisy": report.noisy,
            "noise_rate": round(report.noise_rate, 4), "verdict": verdict,
        })

    print(json.dumps({
        "threshold": NOISE_FAIL_THRESHOLD,
        "overall": "FAIL" if worst_fail else "PASS",
        "docs": results,
    }, indent=2))




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
        description="Artifact registry CLI for SwarmAI pipeline", allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", required=True,
                                parser_class=functools.partial(argparse.ArgumentParser,
                                                               allow_abbrev=False))

    # discover
    p_discover = sub.add_parser("discover", help="Discover artifacts by type")
    p_discover.add_argument("--project", required=True)
    p_discover.add_argument("--types", required=True, help="Comma-separated types")
    p_discover.add_argument("--full", action="store_true", help="Include full artifact data")

    # publish
    p_publish = sub.add_parser("publish", help="Publish a new artifact")
    p_publish.add_argument("--project", required=True)
    p_publish.add_argument("--type", required=True)
    p_publish.add_argument("--producer", required=True)
    p_publish.add_argument("--summary", required=True)
    p_publish.add_argument("--data", required=True, help="JSON data string")
    p_publish.add_argument("--topic", default="")
    p_publish.add_argument("--quiet", action="store_true",
                           help='Emit ONLY a single-line {"artifact_id": ...} on success')


    # learn
    p_learn = sub.add_parser("learn", help="Record pipeline outcome for learning")
    p_learn.add_argument("--project", required=True)
    p_learn.add_argument("--evaluation-id", required=True, help="ID of evaluation artifact")
    p_learn.add_argument("--outcome", required=True, choices=["success", "partial", "failure", "cancelled"])
    p_learn.add_argument("--actual-effort", default=None, help="Actual effort (T-shirt or sessions)")
    p_learn.add_argument("--lessons", default=None, help="Semicolon-separated lessons")

    # projects
    sub.add_parser("projects", help="List all projects")




    # bind (CREATE→BIND→PULL: clone + index every binding of a project's bindings.yaml)
    p_bind = sub.add_parser("bind", help="PULL: clone + code-intel every binding in a project's bindings.yaml")
    p_bind.add_argument("--project", required=True, help="Project whose bindings.yaml to PULL")

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

    # classify-starter (design 2026-10-27 §5.10: GOVERN / DISTILL / SHELF for a starter path)
    p_cls = sub.add_parser("classify-starter", help="GOVERN / DISTILL / SHELF for a New Brain starter path")
    p_cls.add_argument("path")

    # ddd-skeleton-align (design 2026-10-27 §5.10: give an existing brain the skeleton's sections)
    p_align = sub.add_parser("ddd-skeleton-align", help="List (or --apply: append) the skeleton sections a brain's 4 docs lack")
    p_align.add_argument("--project", required=True)
    p_align.add_argument("--apply", action="store_true", help="Append the missing sections (default: dry-run report)")

    # ddd-health
    p_ddd_health = sub.add_parser("ddd-health", help="5-dimensional DDD health scoring per section")
    p_ddd_health.add_argument("--project", required=True)

    # ddd-retire (run_186a5f15: agent-directed named-entry RETIRE — knowledge 'out' side)
    p_ddd_retire = sub.add_parser("ddd-retire", help="Report ONE named (title,section) retirement candidate; never mutates")
    p_ddd_retire.add_argument("--file", required=True, help="Doc path (relative to workspace or absolute), e.g. .context/MEMORY.md or Projects/X/IMPROVEMENT.md")
    p_ddd_retire.add_argument("--title", required=True, help="Exact entry title (the bold text after the [type] tag)")
    p_ddd_retire.add_argument("--section", required=True, help="Exact '## Section' the entry lives under")
    p_ddd_retire.add_argument("--archive-name", dest="archive_name", default=None, help="Archive filename (default MEMORY-archive.md for MEMORY.md, else IMPROVEMENT-archive.md)")
    p_ddd_retire.add_argument("--force", action="store_true", help="Retire even a keep-class entry (decision/model/principle/correction/COE)")

    # ddd-noise (M0 ②: per-doc reclaimable-noise gate)
    p_ddd_noise = sub.add_parser("ddd-noise", help="Per-doc DDD noise gate (reclaimable noise_rate + PASS/FAIL)")
    p_ddd_noise.add_argument("--project", default=None, help="(unused; scans MEMORY + all IMPROVEMENT by default)")
    p_ddd_noise.add_argument("--doc", default=None, help="Single doc path (relative to workspace or absolute)")

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
    reg = ArtifactRegistry(_get_workspace())

    handlers = {
        "discover": cmd_discover,
        "publish": cmd_publish,
        "learn": cmd_learn,
        "projects": cmd_projects,
        "bind": cmd_bind,
        "survey": cmd_survey,
        "bootstrap-apply": cmd_bootstrap_apply,
        "entry-contract": cmd_entry_contract,
        "project-agent-draft": cmd_project_agent_draft,
        "classify-starter": cmd_classify_starter,
        "ddd-skeleton-align": cmd_ddd_skeleton_align,
        "ddd-health": cmd_ddd_health,
        "ddd-retire": cmd_ddd_retire,
        "ddd-noise": cmd_ddd_noise,
        "release-gate": cmd_release_gate,
    }
    try:
        handlers[args.command](args, reg)
    except json.JSONDecodeError:
        raise                                # a data error keeps its traceback
    except ValueError as e:
        # an unsafe identifier is refused with the CLI's JSON error, not a traceback
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
