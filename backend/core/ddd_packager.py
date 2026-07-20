"""DDD dual-target distribution packager — render any compliant six-section DDD
into a distributable package.

Two targets (design SSOT ``docs/2026-07-20-ddd-dual-target-distribution-design.md``):
- **``aim-capabilities``** — an internal AIM capabilities package: a ``Config`` with
  the ``AIMBuild`` build-tool + a ``type=ai-capabilities`` target (preserving any
  existing build-system), ``agents/<ddd>.agent-spec.json``, ``skills/``, ``context/``,
  ``agent-sops/``.
- **``open-plugin``** — an Open-Plugins Standard plugin: ``.plugin/plugin.json`` +
  ``skills/`` + ``agents/*.md`` + ``rules/`` + ``.mcp.json`` + ``hooks/``.

Design invariants enforced here:
- **DDD-agnostic** — knows nothing about any specific DDD; input is a directory path.
- **Declaration is the ceiling** — emits ONLY the targets the DDD declared, subset-only
  (via ``ddd_distribution_policy``). Never widens reach by inference (C041).
- **Class-A/B split DELEGATES to ``ddd_skill_registry``** (``_is_enablement`` /
  ``_read_domain_skills``) — never forks the enablement definition (Gate-1 C3).
- **Content-safety scan runs over the ENTIRE emitted tree** before any external
  publish — secrets, internal-org strings, home-machine path literals (Gate-1 H2).
  ``scan`` (pure, returns findings) is separate from ``rewrite`` (Gate-1 M2);
  v1 default is ABORT-only.
- **Deterministic** — sorted dir walks, ``sort_keys`` JSON, ``\\n`` endings, no mtimes
  in structural output → byte-identical across runs (Gate-1 H3).

Pure functions + small dataclasses. No network, no git, no ``aim``/``gh`` calls —
those are the human/orchestrator's job (the ``s_ddd-distribute`` skill).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import ddd_distribution_policy as policy
from core.ddd_distribution_policy import (
    TARGET_AIM,
    TARGET_OPEN_PLUGIN,
    DistributionPolicy,
)

logger = logging.getLogger(__name__)

# Delegate the class-A/B split to the single source of truth (Gate-1 C3).
try:  # pragma: no cover - import wiring
    from core.ddd_skill_registry import _is_enablement, _read_domain_skills
except Exception:  # pragma: no cover - fallback keeps the module importable off-host
    _ENABLEMENT_PREFIXES = ("s_ddd-",)
    _ENABLEMENT_EXACT = {"s_ai-ready-repo"}

    def _is_enablement(skill_name: str) -> bool:
        return skill_name in _ENABLEMENT_EXACT or any(
            skill_name.startswith(p) for p in _ENABLEMENT_PREFIXES
        )

    def _read_domain_skills(aim_path: Path) -> list[str]:
        try:
            data = json.loads(Path(aim_path).read_text(encoding="utf-8"))
        except Exception:
            return []
        plugins = data.get("plugins") if isinstance(data, dict) else None
        domain = plugins.get("domain_skills") if isinstance(plugins, dict) else None
        if not isinstance(domain, list):
            return []
        return [s for s in domain if isinstance(s, str) and s and not _is_enablement(s)]


# --- Content-safety patterns (Gate-1 H2/H3; reuse the spirit of security_hooks) ---
# Home-machine path literals: break on ANY foreign host, so rewritten/aborted on ALL
# targets (not just external). Portable replacement is the workspace env var.
_HOST_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.swarm-ai/SwarmWS"),            # ~/.swarm-ai/SwarmWS, /home/x/.swarm-ai/…, bare
    re.compile(r"/(?:Users|home)/[^/\s\"']+/\.swarm-ai"),
    re.compile(r"SwarmAI-Workspace"),             # any host: /Users/…, /home/…, or bare relative (Gate-2 H2)
)
_PORTABLE_WORKSPACE_VAR = "${SWARM_WORKSPACE}"

# Internal-org strings: forbidden in an EXTERNAL (public) package only.
_INTERNAL_STRING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcode\.amazon\.com\b"),
    re.compile(r"[a-z0-9.-]+\.a2z\.com\b"),
    re.compile(r"[a-z0-9.-]+\.aws\.dev\b"),
    re.compile(r"[a-z0-9.-]+\.amazon\.dev\b"),
    re.compile(r"\bmidway\b", re.IGNORECASE),
    re.compile(r"\bbrazil\b", re.IGNORECASE),
    re.compile(r"\b\d{12}\b"),  # 12-digit AWS account id
)

# Secrets: forbidden on ANY target.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)AKIA[0-9A-Z]{16}"),                   # AWS access key id (case-insensitive, Gate-2 H2)
    re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S+"),
    # Quoted OR unquoted assignment (Gate-2 H1 — .env/shell/YAML are usually unquoted).
    re.compile(r"(?i)\b(secret|password|passwd|token|api[_-]?key)\s*[=:]\s*['\"][^'\"]{6,}['\"]"),
    re.compile(r"(?i)\b(secret|password|passwd|token|api[_-]?key)\s*[=:]\s*[^\s'\"]{6,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),             # GitHub token
)

# Content-safety scan is fail-CLOSED: scan EVERYTHING except a small deny-list of
# known-binary suffixes (Gate-2 C1 — an allow-list of "content" suffixes fails OPEN,
# silently shipping secrets in .env/.pem/.key/.properties/etc.). A secret-bearing
# file must never slip through because its extension wasn't anticipated.
_BINARY_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar",
     ".tgz", ".bz2", ".xz", ".7z", ".rar", ".woff", ".woff2", ".ttf", ".otf", ".eot",
     ".mp3", ".mp4", ".mov", ".avi", ".wav", ".flac", ".webm", ".so", ".dylib", ".dll",
     ".pyc", ".pyo", ".class", ".o", ".a", ".bin", ".dat", ".db", ".sqlite", ".wasm"}
)


def _is_scannable(path: Path) -> bool:
    """Fail-closed: scan unless the suffix is a known-binary type (Gate-2 C1)."""
    return path.suffix.lower() not in _BINARY_SUFFIXES


@dataclass(frozen=True)
class ScanFinding:
    file: str          # path relative to the scanned root
    kind: str          # "secret" | "internal-string" | "host-path"
    detail: str        # the matched snippet (truncated)
    line: int


@dataclass
class PackageResult:
    target: str
    out_dir: Path
    files: list[str] = field(default_factory=list)          # sorted, relative
    skills_included: list[str] = field(default_factory=list)
    skills_excluded: list[str] = field(default_factory=list)  # loud (Gate-1 H5)
    warnings: list[str] = field(default_factory=list)


class PackagingError(Exception):
    """Raised when a package cannot be safely emitted (undeclared target, scan abort)."""


# ---------------------------------------------------------------------------
# Name normalization (Open-Plugins strict-lowercase) + collision (Gate-1 L1)
# ---------------------------------------------------------------------------
def normalize_name(ddd_name: str, prefix: str = "swarmai-") -> str:
    """Normalize a DDD name to an Open-Plugins-legal plugin name, namespaced.

    Lowercase, ``_``/space/illegal → ``-``, collapse runs, strip separators, prefix.
    """
    s = ddd_name.strip().lower()
    s = re.sub(r"[^a-z0-9.]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-.")
    if not s:
        s = "ddd"
    name = f"{prefix}{s}"
    # Open-Plugins: 1-64 chars.
    return name[:64].rstrip("-.")


# ---------------------------------------------------------------------------
# Skill classification — DELEGATES to ddd_skill_registry (Gate-1 C3)
# ---------------------------------------------------------------------------
def split_skills(ddd_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """Return (domain_included, enablement_excluded, unclassified_excluded).

    - domain_included: class-B (from aim.json plugins.domain_skills, via the registry).
    - enablement_excluded: class-A native_skills (declared enablement) — excluded.
    - unclassified_excluded: a skill dir on disk in NEITHER list → excluded + surfaced
      LOUDLY (Gate-1 H5), never default-included.
    All lists sorted (determinism).
    """
    aim_path = ddd_dir / "aim.json"
    domain = set(_read_domain_skills(aim_path))

    declared_native: set[str] = set()
    try:
        data = json.loads(aim_path.read_text(encoding="utf-8"))
        plugins = data.get("plugins") if isinstance(data, dict) else None
        if isinstance(plugins, dict) and isinstance(plugins.get("native_skills"), list):
            declared_native = {s for s in plugins["native_skills"] if isinstance(s, str)}
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    on_disk: set[str] = set()
    skills_root = ddd_dir / "skills"
    if skills_root.is_dir():
        for child in sorted(skills_root.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                on_disk.add(child.name)

    # Excluded set is AUTHORITATIVE over included (Gate-2 C2): a skill listed in BOTH
    # native_skills and domain_skills (author error or deliberate smuggle) MUST be
    # excluded — never copied into an external package. Subtract native + enablement.
    domain_included = sorted(
        s for s in domain
        if s in on_disk and s not in declared_native and not _is_enablement(s)
    )
    enablement_excluded = sorted(
        s for s in on_disk if s in declared_native or _is_enablement(s)
    )
    unclassified = sorted(
        s for s in on_disk
        if s not in domain and s not in declared_native and not _is_enablement(s)
    )
    return domain_included, enablement_excluded, unclassified


# ---------------------------------------------------------------------------
# Content-safety scan — over the EMITTED tree (Gate-1 H2). scan ≠ rewrite (M2).
# ---------------------------------------------------------------------------
def _scan_text(rel: str, text: str, *, external: bool) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for pat in _SECRET_PATTERNS:
            m = pat.search(line)
            if m:
                findings.append(ScanFinding(rel, "secret", m.group(0)[:40], i))
        for pat in _HOST_PATH_PATTERNS:
            m = pat.search(line)
            if m:
                findings.append(ScanFinding(rel, "host-path", m.group(0)[:60], i))
        if external:
            for pat in _INTERNAL_STRING_PATTERNS:
                m = pat.search(line)
                if m:
                    findings.append(ScanFinding(rel, "internal-string", m.group(0)[:60], i))
    return findings


def content_safety_scan(tree: Path, *, external: bool) -> list[ScanFinding]:
    """Walk the ENTIRE emitted tree, return findings. Pure — does not mutate (M2).

    ``external=True`` also flags internal-org strings (forbidden in a public package);
    secrets + host-paths are flagged on ANY target (a host-path breaks even a private
    install; a secret must never ship).
    """
    findings: list[ScanFinding] = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        if not _is_scannable(path):
            continue
        rel = str(path.relative_to(tree))
        try:
            # errors="replace" so a non-UTF-8 byte (Gate-2 C3) can't silently hide a
            # secret in the readable remainder — we still scan the decodable text.
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # Truly unreadable → fail LOUD: surface as a finding, never silent-skip.
            findings.append(ScanFinding(rel, "unreadable", "could not read file for scan", 0))
            continue
        findings.extend(_scan_text(rel, text, external=external))
    return sorted(findings, key=lambda f: (f.file, f.line, f.kind))


def rewrite_host_paths(tree: Path) -> int:
    """Explicit, opt-in remediation (M2): rewrite host-path literals to the portable
    workspace var across the emitted tree. Returns the count of files changed.
    Separate from ``content_safety_scan`` on purpose — mutation is never implicit.
    """
    changed = 0
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or not _is_scannable(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new = text
        for pat in _HOST_PATH_PATTERNS:
            new = pat.sub(_PORTABLE_WORKSPACE_VAR, new)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Deterministic JSON writer (Gate-1 H3)
# ---------------------------------------------------------------------------
def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _copy_skill_dirs(ddd_dir: Path, out_skills: Path, skills: list[str]) -> list[str]:
    """Copy each included skill dir into the package. Returns sorted relative files."""
    copied: list[str] = []
    for name in sorted(skills):
        src = ddd_dir / "skills" / name
        if not src.is_dir():
            continue
        dst = out_skills / name
        shutil.copytree(src, dst, dirs_exist_ok=True)
        for f in sorted(dst.rglob("*")):
            if f.is_file():
                copied.append(str(f.relative_to(out_skills.parent)))
    return sorted(copied)


def _read_aim(ddd_dir: Path) -> dict[str, Any]:
    try:
        d = json.loads((ddd_dir / "aim.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


# Six-section knowledge docs → context/ (deterministic set).
_KNOWLEDGE_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")


# ---------------------------------------------------------------------------
# Target A — AIM capabilities package
# ---------------------------------------------------------------------------
def emit_target_aim(ddd_dir: Path, out_dir: Path) -> PackageResult:
    ddd_name = ddd_dir.name
    aim = _read_aim(ddd_dir)
    domain, enablement_excl, unclassified = split_skills(ddd_dir)
    res = PackageResult(target=TARGET_AIM, out_dir=out_dir,
                        skills_included=domain,
                        skills_excluded=sorted(enablement_excl + unclassified))
    for s in unclassified:
        res.warnings.append(f"skill '{s}' on disk but in NEITHER native_skills nor domain_skills → excluded (unclassified)")

    out_dir.mkdir(parents=True, exist_ok=True)

    # Config: preserve any existing build-system, ADD AIMBuild + ai-capabilities target.
    existing_build_system = "aim-build"  # default for a fresh AI-cap package
    config_src = ddd_dir / "Config"
    if config_src.is_file():
        m = re.search(r"build-system\s*=\s*([\w-]+)", config_src.read_text(encoding="utf-8"))
        if m:
            existing_build_system = m.group(1)
    pkg_id = ddd_name.replace(" ", "")
    config_text = (
        f"package.{pkg_id} = {{\n"
        f"    interfaces = (1.0);\n\n"
        f"    build-system = {existing_build_system};\n"
        f"    build-tools = {{\n"
        f"        1.0 = {{\n"
        f"            AIMBuild = 1.0;\n"
        f"        }};\n"
        f"    }};\n\n"
        f"    targets = {{\n"
        f"        {pkg_id}-1.0 = {{\n"
        f"            type = ai-capabilities;\n"
        f"        }};\n"
        f"    }};\n"
        f"}};\n"
    )
    (out_dir / "Config").write_text(config_text, encoding="utf-8")

    # agents/<ddd>.agent-spec.json — systemPrompt via {{aim:include}}, glob skill dep.
    agent_spec = {
        "schemaVersion": "1",
        "name": normalize_name(ddd_name, prefix=""),
        "config": {
            "description": str(aim.get("description", f"{ddd_name} domain brain")),
            "systemPrompt": "{{aim:include:context/AGENTS.md}}",
        },
        "dependencies": {
            "skills": {"skillNames": sorted(domain) or ["*"]},
            "context": {"contextNames": ["*"]},
            "agentSops": {"agentSopNames": ["*"]},
        },
    }
    _write_json(out_dir / "agents" / f"{normalize_name(ddd_name, prefix='')}.agent-spec.json", agent_spec)

    # skills/, context/ (knowledge docs + AGENTS.md), agent-sops/ (gates + refresher)
    res.files += _copy_skill_dirs(ddd_dir, out_dir / "skills", domain)
    ctx = out_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    for doc in (*_KNOWLEDGE_DOCS, "AGENTS.md"):
        src = ddd_dir / doc
        if src.is_file():
            shutil.copy2(src, ctx / doc)
    sops = out_dir / "agent-sops"
    sops.mkdir(parents=True, exist_ok=True)
    for doc in ("REFRESHER.md",):
        src = ddd_dir / doc
        if src.is_file():
            (sops / f"{Path(doc).stem.lower()}.sop.md").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8")

    res.files = sorted({str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file()})
    return res


# ---------------------------------------------------------------------------
# Target B — Open-Plugins plugin
# ---------------------------------------------------------------------------
def emit_target_open_plugin(ddd_dir: Path, out_dir: Path) -> PackageResult:
    ddd_name = ddd_dir.name
    aim = _read_aim(ddd_dir)
    domain, enablement_excl, unclassified = split_skills(ddd_dir)
    res = PackageResult(target=TARGET_OPEN_PLUGIN, out_dir=out_dir,
                        skills_included=domain,
                        skills_excluded=sorted(enablement_excl + unclassified))
    for s in unclassified:
        res.warnings.append(f"skill '{s}' on disk but in NEITHER native_skills nor domain_skills → excluded (unclassified)")

    out_dir.mkdir(parents=True, exist_ok=True)
    plugin_name = normalize_name(ddd_name)

    # .plugin/plugin.json (Open-Plugins Standard — NOT the internal .claude-plugin/ format)
    plugin_json = {
        "name": plugin_name,
        "version": "1.0.0",
        "description": str(aim.get("description", f"{ddd_name} domain brain"))[:512],
        "skills": "skills",
        "agents": "agents",
        "rules": "rules",
        "hooks": "hooks",
    }
    _write_json(out_dir / ".plugin" / "plugin.json", plugin_json)

    # skills/
    res.files += _copy_skill_dirs(ddd_dir, out_dir / "skills", domain)

    # agents/<ddd>.md — system prompt as a sub-agent markdown
    agents_dir = out_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agents_src = ddd_dir / "AGENTS.md"
    body = agents_src.read_text(encoding="utf-8") if agents_src.is_file() else f"# {ddd_name}\n"
    (agents_dir / f"{plugin_name}.md").write_text(
        f"---\nname: {plugin_name}\ndescription: {ddd_name} domain brain\n---\n\n{body}",
        encoding="utf-8",
    )

    # rules/ — knowledge docs as always-apply rules
    rules_dir = out_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for doc in _KNOWLEDGE_DOCS:
        src = ddd_dir / doc
        if src.is_file():
            (rules_dir / f"{doc[:-3].lower()}.md").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8")

    # hooks/ dir (present, empty stub — jobs wiring is the install script's job)
    (out_dir / "hooks").mkdir(parents=True, exist_ok=True)

    # .mcp.json — delegate shaping to mcp_config_loader when available (Gate-1 H4)
    mcp_servers = _extract_mcp(ddd_dir)
    if mcp_servers:
        _write_json(out_dir / ".mcp.json", {"mcpServers": mcp_servers})

    res.files = sorted({str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file()})
    return res


def _extract_mcp(ddd_dir: Path) -> dict[str, Any]:
    """Pull declared MCP servers from bindings.yaml/aim.json into a plain dict.
    Kept deterministic + dependency-light; the design defers full auth wiring."""
    servers: dict[str, Any] = {}
    aim = _read_aim(ddd_dir)
    plugins = aim.get("plugins") if isinstance(aim, dict) else None
    mcp = plugins.get("mcp") if isinstance(plugins, dict) else None
    if isinstance(mcp, list):
        for entry in mcp:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                servers[entry["name"]] = {
                    k: v for k, v in sorted(entry.items()) if k != "name"
                }
    return servers


# ---------------------------------------------------------------------------
# install.sh emitter — defensive by construction (Gate-1 M3)
# ---------------------------------------------------------------------------
def emit_install_script(out_dir: Path, plugin_name: str, *, is_aim_target: bool) -> Path:
    """Emit a defensive install.sh. set -euo pipefail, quoted expansions,
    no-TTY-conservative, idempotent, guards fetched content. Passes bash -n."""
    lines = [
        "#!/usr/bin/env bash",
        "# Auto-generated by ddd_packager — install this DDD package.",
        "# Defensive by construction: fails safe, idempotent, never deletes silently.",
        "set -euo pipefail",
        "",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        f'PLUGIN_NAME="{plugin_name}"',
        "",
        'if command -v aim >/dev/null 2>&1; then',
        '  echo "[install] aim CLI found — installing capability"',
        '  aim plugins install --local "${SCRIPT_DIR}"',
        "else",
        '  echo "[install] no aim CLI — placing files for a non-AIM host"',
        '  DEST="${SWARM_WORKSPACE:-$HOME/.local/share/ddd-plugins}/${PLUGIN_NAME}"',
        '  mkdir -p "${DEST}"',
        '  cp -R "${SCRIPT_DIR}/." "${DEST}/"',
        '  echo "[install] placed at ${DEST}"',
        "fi",
        "",
        'echo "[install] done: ${PLUGIN_NAME}"',
    ]
    path = out_dir / "install.sh"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# Top-level orchestration — declaration is the ceiling (Gate-1 AC5)
# ---------------------------------------------------------------------------
def package_ddd(
    ddd_dir: str | Path,
    out_root: str | Path,
    *,
    requested_targets: list[str] | None = None,
    publish: bool = False,
) -> list[PackageResult]:
    """Emit the DDD into ``out_root/<target>/`` for each PERMITTED target.

    - Reach = the DDD's declared ceiling (``ddd_distribution_policy``); a caller may
      request a SUBSET (never widen). An undeclared request is refused (raises).
    - ``publish=True`` runs the external-publish content gate: on any secret /
      host-path (any target) or internal-string (external visibility) → PackagingError
      (fail-closed abort). ``publish=False`` (emit-only) still scans + warns, and still
      aborts on secrets/host-paths, but does NOT block on internal-strings (a private
      internal package legitimately contains internal strings).
    - Emit ≠ publish: an ``open-plugin`` target under ``visibility:internal`` will
      EMIT but ``publish=True`` is refused for it (design §0.2 invariant 1).
    """
    ddd_dir = Path(ddd_dir)
    out_root = Path(out_root)
    pol = policy.validate_distribution_file(ddd_dir / "aim.json")

    permitted, refused = policy.resolve_requested_targets(pol, requested_targets)
    if refused:
        raise PackagingError(
            f"declaration is the ceiling: targets {refused} not declared "
            f"(declared: {list(pol.targets)}). A caller may only subset, never widen."
        )
    if publish and not pol.permits_external_publish():
        raise PackagingError(
            f"publish refused: visibility='{pol.visibility}' forbids external publish "
            f"(emit ≠ publish). Change the declaration (human-gated) to publish externally."
        )

    results: list[PackageResult] = []
    for target in sorted(permitted):
        out_dir = out_root / target
        if out_dir.exists():
            shutil.rmtree(out_dir)  # idempotent, deterministic re-emit
        if target == TARGET_AIM:
            res = emit_target_aim(ddd_dir, out_dir)
        elif target == TARGET_OPEN_PLUGIN:
            res = emit_target_open_plugin(ddd_dir, out_dir)
            emit_install_script(out_dir, normalize_name(ddd_dir.name), is_aim_target=False)
        else:  # pragma: no cover - permitted is filtered to known targets
            continue

        # Content-safety scan over the EMITTED tree.
        external = pol.visibility == policy.VISIBILITY_EXTERNAL
        findings = content_safety_scan(out_dir, external=external)
        # Secrets + host-paths abort on ANY emit; internal-strings abort only on publish.
        blocking = [f for f in findings if f.kind in ("secret", "host-path")]
        if publish:
            blocking = findings  # everything blocks a publish
        if blocking:
            detail = "; ".join(f"{f.kind}@{f.file}:{f.line} ({f.detail})" for f in blocking[:8])
            raise PackagingError(
                f"content-safety scan BLOCKED {target} ({len(blocking)} finding(s)): {detail}"
            )
        for f in findings:
            res.warnings.append(f"scan: {f.kind}@{f.file}:{f.line} ({f.detail})")
        results.append(res)

    return results
