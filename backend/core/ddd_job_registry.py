"""DDD Job Registry — the product-level engine that indexes the SCHEDULED JOBS
owned by each mounted DDD (a project under the workspace ``Projects/``).

Paradigm: a mature DDD is a portable capability package (design:
``Knowledge/Designs/2026-07-19-ddd-portable-capability-package-design.md`` +
``2026-07-19-ddd-jobs-tools-registry-design.md`` §2 / §5-J1). Beyond knowledge +
skills, a DDD OWNs the scheduled jobs that DRIVE its domain skills — a ``kind: job``
governed asset declared in the DDD's ``bindings.yaml``. This module builds a
**cached manifest** of every mounted DDD's jobs so the App can discover which DDD
owns which job + which domain skill each job depends on.

**Option A — SIDECAR OWNERSHIP INDEX, NOT a scheduler source (J1 scope).** The
scheduler (``jobs/scheduler.py`` ``load_jobs``) is first-source-wins: a DDD job
sharing a ``job_id`` with a ``user-jobs.yaml`` body would be *dropped* by dedup, so
it cannot enrich ``load_jobs``. Under option A the runnable body stays in
``user-jobs.yaml`` and this manifest is a *read-on-demand ownership index* consumed
by diagnostics / ``s_ddd-manager`` / distribution tooling — NOT by the scheduler.
Making the scheduler DDD-aware is J1a (merge-on-duplicate) / J2 (per-DDD jobs.yaml);
neither is in J1. (Gate-2 finding, run_1af19e2d.)

**Immediate value even before a metadata consumer exists:** ``depends_on_skill_resolved``
surfaces a *dangling job* — a job whose declared domain skill is not discoverable in
the skill registry — the exact "silent broken declaration" bug class (cf. CMHK
bms-risk's missing implementation, run_21cedd3b).

Two layers (this module is the engine; the ops face lives in ``s_ddd-manager``):
- **Engine (this file)**: product-level — every SwarmAI user has it in the codebase.
- **Manifest (data)**: per-workspace — ``<workspace>/.context/ddd_job_registry.json``.

Design invariants:
- **Fail-soft, always.** A missing OR malformed manifest/bindings.yaml is treated as
  "no jobs" — this function NEVER raises. It must tolerate HETEROGENEOUS bindings
  shapes: some DDDs declare ``governed_assets: [{kind: job, ...}]`` (GitHub_Community);
  others use a bare ``jobs: []`` or have no jobs at all (CMHK) — a shape it doesn't
  recognize simply yields no job records for that project, never a crash. It does
  NOT reuse ``ddd_bindings.load_bindings`` — that loader's ``BindingsDoc`` requires a
  top-level ``bindings:`` key and rejects a ``governed_assets``-only file with a
  ValueError (Gate-1 finding, run_5ec6b7ad). We ``yaml.safe_load`` raw.
- **PER-PROJECT empty-overwrite guard (STRONGER than the skill registry).** The
  skill registry's guard is all-or-nothing on ``Projects/.iterdir()``; it does NOT
  protect against ONE project's source file being transiently unreadable while
  others read fine (a latent gap noted in the design). Here the source is a
  per-project ``bindings.yaml``, so we track a PER-PROJECT ``read_failed`` signal:
  if project X's bindings.yaml raises OSError (vs. parses to genuinely 0 jobs), we
  PRESERVE X's prior manifest slice (filter the old manifest by ``owner_ddd``)
  instead of dropping X's jobs. A genuine parse-to-0 (file readable, declares no
  jobs) still writes empty for X — that is a real removal, not a transient failure.
- **Atomic write.** tmp + ``os.replace`` so a concurrent reader never sees a
  half-written file. (Mirrors ``ddd_skill_registry._write_manifest_atomic``.)
- **field mapping.** bindings.yaml declares a job asset's id under ``name``; the
  manifest exposes it as ``job_id`` (design schema §2.2).
"""
from __future__ import annotations

import logging
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

MANIFEST_RELPATH = Path(".context") / "ddd_job_registry.json"


def _read_job_assets(bindings_path: Path) -> list[dict[str, Any]]:
    """Read ``governed_assets[kind==job]`` from a DDD's bindings.yaml.

    Returns the raw job-asset dicts (still carrying bindings.yaml's ``name`` key).
    Fail-soft: raises OSError ONLY on a genuine read failure (so the caller can
    distinguish transient-unreadable from parses-to-0); any PARSE problem
    (malformed YAML, unexpected shape) is swallowed → ``[]`` (a readable-but-no-jobs
    result, which is a legitimate 0, not a transient failure).
    """
    # OSError propagates (the transient-read signal the per-project guard preserves
    # on). But a readable-yet-undecodable file (invalid UTF-8) raises
    # UnicodeDecodeError — a subclass of ValueError, NOT OSError — which is a
    # readable-but-unparseable source, NOT a transient failure: peel it off to the
    # parse-to-0 path (write empty), never let it escape as a transient-preserve or
    # crash the "never raises" contract. (Gate-2 finding run_5ec6b7ad — mirrors the
    # template ddd_skill_registry._read_domain_skills, which catches ValueError too.)
    try:
        text = bindings_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        logger.warning("ddd_job_registry: undecodable bindings.yaml %s: %s", bindings_path, exc)
        return []
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning("ddd_job_registry: malformed bindings.yaml %s: %s", bindings_path, exc)
        return []
    if not isinstance(doc, dict):
        return []
    assets = doc.get("governed_assets")
    if not isinstance(assets, list):
        # Heterogeneous shape (e.g. a bare `jobs: []`, or no governed_assets) — no
        # job records for this project. NOT an error.
        return []
    return [a for a in assets if isinstance(a, dict) and a.get("kind") == "job"]


def _resolved_skill_names(workspace_root: Path) -> set[str]:
    """The set of domain-skill names the skill registry currently resolves.

    Used to compute ``depends_on_skill_resolved``. Fail-soft: if the skill manifest
    is missing (fresh workspace, skill refresh hasn't run yet), returns an empty set
    → every ``depends_on_skill`` resolves to False (dangling, surfaced not crashed).
    One-directional import (ddd_skill_registry imports only stdlib → no cycle).
    """
    try:
        from core import ddd_skill_registry
        return {r["skill"] for r in ddd_skill_registry.read_manifest(workspace_root)}
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort, never fatal
        logger.debug("ddd_job_registry: skill manifest unavailable for resolve: %s", exc)
        return set()


def build_manifest_jobs(workspace_root: Path) -> list[dict[str, Any]]:
    """Scan ``<workspace_root>/Projects/*/bindings.yaml`` for ``kind: job`` assets
    and return the job records, writing the manifest atomically.

    Each record: ``{job_id, owner_ddd, schedule, type, enabled, depends_on_skill,
    depends_on_skill_resolved, defined_in}``.

    Fail-soft + PER-PROJECT empty-overwrite guard (see module docstring): a project
    whose bindings.yaml is transiently unreadable KEEPS its prior manifest slice
    rather than being wiped. NEVER raises.
    """
    resolved_skills = _resolved_skill_names(workspace_root)
    # Prior manifest, indexed by owner_ddd — the source for per-project preservation.
    prior_by_owner: dict[str, list[dict[str, Any]]] = {}
    for rec in read_manifest(workspace_root):
        prior_by_owner.setdefault(rec.get("owner_ddd", ""), []).append(rec)

    records: list[dict[str, Any]] = []
    scan_failed = False  # could not even enumerate Projects/ (whole-tree transient)
    projects_dir = workspace_root / "Projects"
    try:
        project_dirs = sorted(p for p in projects_dir.iterdir() if p.is_dir())
    except OSError:
        project_dirs = []
        scan_failed = True

    for project_dir in project_dirs:
        owner = project_dir.name
        bindings = project_dir / "bindings.yaml"
        if not bindings.is_file():
            continue  # no bindings.yaml → no jobs (legitimate 0 for this project)
        try:
            job_assets = _read_job_assets(bindings)
        except OSError as exc:
            # PER-PROJECT transient read failure — do NOT wipe this DDD's jobs.
            # Preserve its prior manifest slice (if any). A brand-new project with
            # no prior slice correctly preserves nothing (it had no jobs to lose).
            preserved = prior_by_owner.get(owner, [])
            if preserved:
                logger.warning(
                    "ddd_job_registry: %s bindings.yaml unreadable (%s) — preserving "
                    "%d prior job record(s), NOT wiping.", owner, exc, len(preserved),
                )
                records.extend(preserved)
            else:
                logger.warning(
                    "ddd_job_registry: %s bindings.yaml unreadable (%s), no prior "
                    "slice to preserve — skipping.", owner, exc,
                )
            continue

        for asset in job_assets:
            job_id = asset.get("name")  # bindings.yaml uses `name`; manifest → job_id
            if not isinstance(job_id, str) or not job_id:
                logger.info("ddd_job_registry: %s has a job asset with no name — skipped", owner)
                continue
            dep = asset.get("depends_on_skill")
            # depends_on_skill_resolved is TRI-STATE — consumers MUST NOT use plain
            # truthiness (`if not resolved`) to detect a dangling job:
            #   True  → declared dependency resolves in the skill registry
            #   False → declared but NOT resolvable == DANGLING (the signal to act on)
            #   None  → no dependency declared (NOT dangling — a false alarm if
            #           conflated with False). Test for `resolved is False` for dangling.
            records.append({
                "job_id": job_id,
                "owner_ddd": owner,
                "schedule": asset.get("schedule"),
                "type": asset.get("type"),
                "enabled": bool(asset.get("enabled", True)),
                "depends_on_skill": dep,
                "depends_on_skill_resolved": (dep in resolved_skills) if dep else None,
                "defined_in": asset.get("defined_in"),
            })

    records.sort(key=lambda r: (r["job_id"], r["owner_ddd"]))

    # Whole-tree empty-overwrite guard (mirrors ddd_skill_registry): could not
    # enumerate Projects/ at all + a non-empty manifest exists → transient, keep it.
    # The PER-PROJECT guard above already handles single-project failures.
    if scan_failed and not records:
        existing = read_manifest(workspace_root)
        if existing:
            logger.warning(
                "ddd_job_registry: could not enumerate Projects/ (transient) and a "
                "non-empty manifest exists — keeping existing cache."
            )
            return existing

    _write_manifest_atomic(workspace_root, records)
    return records


def _write_manifest_atomic(workspace_root: Path, records: list[dict[str, Any]]) -> None:
    """Write the manifest via tmp + os.replace. Fail-soft (log, don't raise)."""
    manifest_path = workspace_root / MANIFEST_RELPATH
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "jobs": records}, indent=2, ensure_ascii=False, sort_keys=True,
        )
        fd, tmp = tempfile.mkstemp(dir=str(manifest_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, manifest_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError as exc:
        logger.warning("ddd_job_registry: could not write manifest: %s", exc)


def read_manifest(workspace_root: Path) -> list[dict[str, Any]]:
    """Read the cached job manifest records. Fail-soft → [] on missing OR malformed.

    MUST NEVER raise — consumers (diagnostics, s_ddd-manager) treat missing ==
    malformed == empty == "no DDD jobs".
    """
    manifest_path = workspace_root / MANIFEST_RELPATH
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("ddd_job_registry: malformed manifest treated as empty: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return []
    out: list[dict[str, Any]] = []
    for r in jobs:
        if (
            isinstance(r, dict)
            and isinstance(r.get("job_id"), str)
            and isinstance(r.get("owner_ddd"), str)
        ):
            out.append(r)
    return out
