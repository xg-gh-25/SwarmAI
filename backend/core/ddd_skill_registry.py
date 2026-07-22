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
  A new user's is EMPTY unless a default DDD ships. Content = each DDD's
  ``aim.json`` ``plugins.domain_skills`` resolved to real skill dirs, with provenance.

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


def _resolve_skill_dir(skill_name: str, project_dir: Path, builtin_dir: Path) -> Path | None:
    """Resolve a domain skill's ACTUAL directory.

    Order (strangler-aware): the DDD package's own ``skills/`` first (Run-3 target),
    then the built-in dir (where domain skills still physically live pre-Run-3).
    Returns None if found in neither (a declared-but-absent skill → skipped, logged).
    """
    in_package = ddd_path(project_dir, "capabilities") / skill_name
    if in_package.is_dir() and (in_package / "SKILL.md").is_file():
        return in_package
    in_builtin = builtin_dir / skill_name
    if in_builtin.is_dir() and (in_builtin / "SKILL.md").is_file():
        return in_builtin
    return None


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


def build_manifest(workspace_root: Path, builtin_dir: Path) -> list[dict[str, Any]]:
    """Scan ``<workspace_root>/Projects/*/aim.json`` and return the domain-skill records.

    Each record: ``{skill, class:"domain", owner_ddd, path, fingerprint}``.
    Writes the manifest atomically to ``<workspace_root>/.context/ddd_skill_registry.json``.

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
        aim = project_dir / "aim.json"
        if not aim.is_file():
            continue
        owner = project_dir.name
        for skill_name in _read_domain_skills(aim):
            skill_dir = _resolve_skill_dir(skill_name, project_dir, builtin_dir)
            if skill_dir is None:
                logger.info(
                    "ddd_registry: %s declares domain skill %s but no dir found "
                    "(package or built-in) — skipped", owner, skill_name,
                )
                continue
            records.append({
                "skill": skill_name,
                "class": "domain",
                "owner_ddd": owner,
                "path": str(skill_dir),
            })

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
