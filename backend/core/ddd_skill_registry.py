"""DDD Skill Registry — the product-level engine that lets SwarmAI discover the
DOMAIN skills owned by each mounted DDD (a project under the workspace ``Projects/``).

Paradigm: a mature DDD is a portable capability package (design:
``Knowledge/Designs/2026-07-19-ddd-portable-capability-package-design.md``). Beyond
knowledge it OWNs domain skills (④ Capabilities). This module builds a **cached
manifest** of every mounted DDD's *domain* skills so the App can discover + apply
them — WITHOUT re-scanning the filesystem on every session.

Two layers (this module is the engine; the ops face lives in ``s_ddd-manager``):
- **Engine (this file)**: product-level — every SwarmAI user has it in the codebase.
- **Manifest (data)**: per-workspace — ``<workspace>/.context/ddd_skill_registry.json``.
  A new user's is EMPTY unless a default DDD ships. Content = each DDD's DOMAIN
  skill dirs discovered by SCANNING its ``4-capabilities/`` folder (folder-as-source
  — NOT the ``aim.json`` declared list), with provenance. The declared list is read
  only as a fail-loud cross-check for declared-but-absent (mid-migration) skills.

Design invariants (from Gate-1 review of run_597f4ed1):
- **DOMAIN only.** ``native_skills`` (enablement: ``s_ddd-*``, ``s_repo-to-ddd``,
  SwarmAI-provided) are EXCLUDED — they are not DDD-owned; the official built-in
  version serves them (tier ``built-in > ddd``).
- **Fail-soft, always.** A missing OR malformed manifest/aim.json is treated as
  "no domain skills" — this function NEVER raises into ``SkillManager.scan_all``
  (which is the single choke point for ALL skill discovery, 29 callers). A torn
  read or bad JSON must not take down skill discovery.
- **Atomic write.** ``build_manifest`` writes tmp + ``os.replace`` so a concurrent
  reader never sees a half-written file.
- **Content-hash provenance, no mtime.** The manifest is reproducible across hosts
  (Run-4 distribution goal) — no per-clone mtime baked in.
- **Authorization is NOT here.** Which agent/host may USE a domain skill is a
  distribution-time (Run 4) concern handled by the existing ``allowed_skills``
  allow-list. This engine only DISCOVERS ownership; ``SkillManager`` tags these as
  ``source_tier="ddd"`` and projection gates them exactly like user/plugin skills.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from core.ddd_paths import ddd_path  # six-section layout resolver (SSOT)

logger = logging.getLogger(__name__)

MANIFEST_RELPATH = Path(".context") / "ddd_skill_registry.json"

# Enablement skills are SwarmAI-provided platform capabilities lent to a DDD; they
# are never DDD-owned domain skills. Declared under aim.json plugins.native_skills.
# (Belt-and-suspenders: we read domain_skills directly, so native_skills are simply
#  not included — but this set documents the intent + guards a future aim.json that
#  mistakenly lists an enablement skill under domain_skills.)
_ENABLEMENT_PREFIXES = ("s_ddd-",)
_ENABLEMENT_EXACT = {"s_repo-to-ddd"}


def _is_enablement(skill_name: str) -> bool:
    """True if *skill_name* is an enablement (SwarmAI-provided) skill, not domain."""
    if skill_name in _ENABLEMENT_EXACT:
        return True
    return any(skill_name.startswith(p) for p in _ENABLEMENT_PREFIXES)


def scan_domain_skill_dirs(project_dir: Path) -> list[Path]:
    """Folder-as-source: return a DDD's DOMAIN skill directories by SCANNING its
    ``4-capabilities/`` folder — NOT by reading the ``aim.json`` declared list.

    The folder is the single source of truth (design:
    ``2026-07-23-steal-from-agentrock-distribution-evaluate.md`` — the S2 steal;
    unifies discovery so ``build_manifest`` and ``ddd_packager.split_skills`` share
    ONE notion of domain-membership, eliminating the hand-maintained declared list
    that had to stay in sync with the folder).

    A directory qualifies as a domain skill iff:
    - it is a direct child of ``ddd_path(project_dir, "capabilities")``, AND
    - it is NOT a symlink and it resolves to a path WITHIN the capabilities folder
      (path-escape guard — a symlink could point at host files outside the DDD and,
      because this primitive now feeds the PACKAGER too, leak them into a distributed
      package; mirrors the existing symlink/out-of-root guards in
      ``skill_manager._scan`` and ``ddd_packager._collect_shared_sources``), AND
    - it contains a top-level ``SKILL.md``, AND
    - it is NOT an enablement skill (``_is_enablement`` — ``s_ddd-*`` / ``s_repo-to-ddd``), AND
    - it is NOT declared under ``aim.json plugins.native_skills`` (the smuggle guard,
      Gate-2 C2: a skill an author declares native must NEVER be classified as domain,
      even if its name doesn't match the enablement convention — else it would ship as
      domain in a package. Excluded-set is authoritative over the folder).

    Returns a sorted list of skill dir Paths. Fail-soft: an unreadable capabilities
    dir raises OSError to the caller (which handles it per-DDD so one bad project
    cannot wipe the whole manifest — build_manifest wraps this call). A capabilities
    dir that simply does not exist yields ``[]`` (a 0-skill DDD is valid).
    """
    skills_root = ddd_path(project_dir, "capabilities")
    if not skills_root.is_dir():
        return []
    caps_root = skills_root.resolve()
    declared_native = _read_native_skills(project_dir / "aim.json")
    # NOTE: iterdir() may raise OSError (permission/stale mount) — deliberately NOT
    # caught here; build_manifest catches it PER-DDD so a transient per-project blip
    # skips that DDD without wiping the others (AC4 / Gate-1 T5).
    out: list[Path] = []
    for child in sorted(skills_root.iterdir()):
        # Path-escape guard (Gate-2 security): reject symlinks + anything that
        # resolves outside the capabilities folder. Pre-existing in the old scan's
        # absence, hardened here because this primitive now also gates packaging.
        if child.is_symlink():
            logger.warning(
                "ddd_registry: %s/%s is a symlink — skipped (path-escape guard)",
                project_dir.name, child.name,
            )
            continue
        try:
            if not child.resolve().is_relative_to(caps_root):
                logger.warning(
                    "ddd_registry: %s/%s resolves outside the capabilities folder "
                    "— skipped (path-escape guard)", project_dir.name, child.name,
                )
                continue
        except OSError:
            continue  # unresolvable (broken link / cycle) → not a valid skill dir
        if (
            child.is_dir()
            and (child / "SKILL.md").is_file()
            and not _is_enablement(child.name)
            and child.name not in declared_native
        ):
            out.append(child)
    return out


def _read_domain_skills(aim_path: Path) -> list[str]:
    """Read plugins.domain_skills from an aim.json. Fail-soft → [] on any error."""
    try:
        data = json.loads(aim_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("ddd_registry: unreadable aim.json %s: %s", aim_path, exc)
        return []
    if not isinstance(data, dict):
        return []
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return []
    domain = plugins.get("domain_skills")
    if not isinstance(domain, list):
        return []
    # Only strings; exclude any enablement skill that slipped into domain_skills.
    return [
        s for s in domain
        if isinstance(s, str) and s and not _is_enablement(s)
    ]


def _read_native_skills(aim_path: Path) -> set[str]:
    """Read plugins.native_skills (declared enablement) from an aim.json.

    Fail-soft → empty set on any error. Used by scan_domain_skill_dirs as the
    smuggle guard (Gate-2 C2): a skill an author declares native must never be
    classified as domain, even if its name doesn't match the enablement convention.
    """
    try:
        data = json.loads(aim_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return set()
    native = plugins.get("native_skills")
    if not isinstance(native, list):
        return set()
    return {s for s in native if isinstance(s, str) and s}


def build_manifest(workspace_root: Path, builtin_dir: Path) -> list[dict[str, Any]]:
    """Scan ``<workspace_root>/Projects/*/4-capabilities/`` and return domain-skill records.

    FOLDER-AS-SOURCE: each DDD's domain skills are discovered by scanning its
    capabilities folder (``scan_domain_skill_dirs``), NOT by reading the aim.json
    declared list. The declared list is consulted only as a fail-loud cross-check
    (declared-but-absent → warning). Each record: ``{skill, class:"domain",
    owner_ddd, path}``. Writes atomically to ``.context/ddd_skill_registry.json``.

    ``builtin_dir`` is retained for signature compatibility (29 callers) but is no
    longer a discovery source — domain skills live in their DDD package, not built-in.

    Fail-soft: any per-project error is logged and skipped; the function returns
    whatever it could resolve (possibly empty) and NEVER raises.
    """
    records: list[dict[str, Any]] = []
    scan_failed = False  # True ONLY if we could not even enumerate Projects/ —
    # the transient-read signal the empty-overwrite guard gates on. Distinct from
    # "enumerated fine, genuinely resolved 0 skills" (a legit 0 → must write empty).
    projects_dir = workspace_root / "Projects"
    try:
        project_dirs = sorted(p for p in projects_dir.iterdir() if p.is_dir())
    except OSError:
        project_dirs = []
        scan_failed = True

    for project_dir in project_dirs:
        owner = project_dir.name
        # FOLDER-AS-SOURCE: the 4-capabilities/ folder is authoritative, NOT the
        # aim.json declared list. Scan the folder; the declared list is read only
        # as a fail-loud cross-check below (AC3 — mid-migration safety).
        try:
            skill_dirs = scan_domain_skill_dirs(project_dir)
        except OSError as exc:
            # Per-DDD guard (AC4 / Gate-1 T5): a transient read failure on ONE
            # project's capabilities dir must NOT wipe the other DDDs' skills. Skip
            # this project (logged); it will resolve on the next successful rebuild.
            logger.warning(
                "ddd_registry: could not scan %s capabilities dir (%s) — skipping "
                "this DDD (other DDDs unaffected)", owner, exc,
            )
            continue

        for skill_dir in skill_dirs:
            records.append({
                "skill": skill_dir.name,
                "class": "domain",
                "owner_ddd": owner,
                "path": str(skill_dir),
            })

        # Fail-loud cross-check (AC3): a name DECLARED in aim.json domain_skills but
        # ABSENT from the folder is a half-migrated / stale declaration. It is NOT
        # added (the folder is authoritative), but its absence is surfaced LOUDLY so
        # a mid-migration DDD is visible, not silently dropped (the safety the old
        # backend/skills fallback used to provide — Gate-1 skeptic finding).
        aim = project_dir / "aim.json"
        if aim.is_file():
            found = {d.name for d in skill_dirs}
            for declared in _read_domain_skills(aim):
                if declared not in found:
                    logger.warning(
                        "ddd_registry: %s declares domain skill %s in aim.json but "
                        "it is ABSENT from the capabilities folder — not registered "
                        "(declared-but-absent; migrate the dir or drop the declaration)",
                        owner, declared,
                    )

    records.sort(key=lambda r: (r["skill"], r["owner_ddd"]))

    # Empty-overwrite guard — gated on SCAN FAILURE, not on `records == 0`.
    # If we could not even enumerate Projects/ (iterdir OSError → scan_failed),
    # a resolved-0 result is a transient read failure (mid-checkout / permission
    # blip / unreadable mount), NOT a real state. Overwriting the manifest with []
    # would wipe every domain skill from discovery until the next successful
    # rebuild — worse than staleness. So on scan_failed + an existing non-empty
    # manifest, keep the good cache.
    #
    # CRITICAL — this must NOT fire on a LEGITIMATE removal. If Projects/
    # enumerated fine (scan_failed=False) but genuinely resolved 0 skills — e.g.
    # a DDD's aim.json was edited to drop all domain_skills, or the last domain
    # DDD was decommissioned — that is a REAL 0 and MUST be written, else the
    # manifest keeps a stale skill forever (every later scan also sees 0 and the
    # guard would re-preserve it — self-perpetuating staleness, the exact inverse
    # of the bug this whole change fixes). Gating on `records == 0` alone conflated
    # these two; gating on scan_failed separates them. (Gate-2 adversarial finding,
    # run_669e29f6 — the first guard traded a transient wipe for a permanent stale.)
    if scan_failed and not records:
        existing = read_manifest(workspace_root)
        if existing:
            logger.warning(
                "ddd_registry: could not enumerate Projects/ (transient read "
                "failure) and a non-empty manifest exists — keeping existing "
                "cache (NOT overwriting with empty)."
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
            {"version": 1, "skills": records}, indent=2, ensure_ascii=False, sort_keys=True,
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
        logger.warning("ddd_registry: could not write manifest: %s", exc)


def read_manifest(workspace_root: Path) -> list[dict[str, Any]]:
    """Read the cached manifest records. Fail-soft → [] on missing OR malformed.

    This is called from ``SkillManager.scan_all`` (the choke point for ALL skill
    discovery) — it MUST NEVER raise. Missing == malformed == empty == no-op tier.
    """
    manifest_path = workspace_root / MANIFEST_RELPATH
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("ddd_registry: malformed manifest treated as empty: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    skills = data.get("skills")
    if not isinstance(skills, list):
        return []
    # Defensive: only well-formed records with the required fields.
    out: list[dict[str, Any]] = []
    for r in skills:
        if (
            isinstance(r, dict)
            and isinstance(r.get("skill"), str)
            and isinstance(r.get("path"), str)
            and isinstance(r.get("owner_ddd"), str)
        ):
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Ops face (list / refresh / inspect) — referenced by s_ddd-manager.
# Read-only except `refresh`, which reuses build_manifest (no forked rebuild).
# ---------------------------------------------------------------------------

def _cli() -> None:  # pragma: no cover - thin CLI wrapper over tested functions
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="ddd_skill_registry",
                                description="DDD skill registry ops (list/refresh/inspect)")
    p.add_argument("cmd", choices=["list", "refresh", "inspect"])
    p.add_argument("--workspace", required=True, help="SwarmWS workspace root")
    p.add_argument("--builtin", help="built-in skills dir (required for refresh)")
    p.add_argument("--skill", help="skill name (for inspect)")
    a = p.parse_args()
    ws = Path(a.workspace)

    if a.cmd == "refresh":
        if not a.builtin:
            p.error("--builtin is required for refresh")
        recs = build_manifest(ws, Path(a.builtin))
        print(json.dumps({"refreshed": len(recs), "skills": [r["skill"] for r in recs]},
                         indent=2, ensure_ascii=False))
    elif a.cmd == "list":
        recs = read_manifest(ws)
        print(json.dumps(
            [{"skill": r["skill"], "owner_ddd": r["owner_ddd"]} for r in recs],
            indent=2, ensure_ascii=False))
    elif a.cmd == "inspect":
        recs = read_manifest(ws)
        match = [r for r in recs if not a.skill or r["skill"] == a.skill]
        if not match:
            print(f"no domain skill found (skill={a.skill!r})", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(match, indent=2, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    _cli()
