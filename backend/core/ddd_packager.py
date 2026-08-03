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
)
from core.project_registry import DDD_CANONICAL_DOCS
from core.ddd_paths import ddd_path  # six-section layout resolver (SSOT)

logger = logging.getLogger(__name__)

# Delegate the class-A/B split to the single source of truth (Gate-1 C3).
# NOTE: split_skills now discovers via ddd_skill_registry.scan_domain_skill_dirs
# (folder-as-source) — it no longer needs _read_domain_skills. _is_enablement is
# still used (enablement_excluded). The fallback keeps the module importable off-host.
try:  # pragma: no cover - import wiring
    from core.ddd_skill_registry import _is_enablement
except Exception:  # pragma: no cover - fallback keeps the module importable off-host
    _ENABLEMENT_PREFIXES = ("s_ddd-",)
    _ENABLEMENT_EXACT = {"s_repo-to-ddd"}

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

    FOLDER-AS-SOURCE (unified with ddd_skill_registry — design
    ``2026-07-23-steal-from-agentrock-distribution-evaluate.md``): domain membership
    is decided by SCANNING ``4-capabilities/`` (via
    ``ddd_skill_registry.scan_domain_skill_dirs``), NOT by the aim.json declared
    list. This makes build_manifest (runtime discovery) and split_skills (packaging)
    share ONE notion of "which skills does this DDD own" — no split-brain where a
    skill is available at runtime but missing from a distributed package (Gate-1 T3).

    - domain_included: on-disk skill dirs that are NOT enablement (the folder scan).
    - enablement_excluded: on-disk dirs that ARE enablement (native_skills OR
      ``s_ddd-*``/``s_repo-to-ddd``) — never shipped as domain.
    - unclassified_excluded: EMPTY under folder-as-source (a dir with SKILL.md that
      is not enablement IS a domain skill by definition). Retained in the return
      tuple for caller compatibility (emit_target_* unpack 3 values); the loud
      undeclared-skill warning it used to carry is now the registry's declared-but-
      absent cross-check (the inverse direction: declared∖on-disk, not on-disk∖declared).
    All lists sorted (determinism).
    """
    from core.ddd_skill_registry import _read_native_skills, scan_domain_skill_dirs

    # domain = folder scan minus enablement minus declared-native (smuggle guard is
    # inside scan_domain_skill_dirs — Gate-2 C2 preserved).
    domain_included = sorted(d.name for d in scan_domain_skill_dirs(ddd_dir))

    # Enablement dirs on disk (declared native OR name-convention) — excluded from
    # domain, optionally shipped as the portable engine via with_enablement.
    declared_native = _read_native_skills(ddd_dir / "aim.json")
    enablement_excluded: list[str] = []
    skills_root = ddd_path(ddd_dir, "capabilities")
    if skills_root.is_dir():
        for child in sorted(skills_root.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                if child.name in declared_native or _is_enablement(child.name):
                    enablement_excluded.append(child.name)

    unclassified: list[str] = []  # folder-as-source: no undeclared-but-on-disk class
    return domain_included, sorted(enablement_excluded), unclassified


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


# AIM agentskills.io requires a skill `name` (and its dir name) to match
# ^[a-z0-9-]+$ — lowercase letters, digits, hyphens ONLY. SwarmAI's internal
# convention prefixes every skill dir with `s_` (e.g. `s_repo-to-ddd`), whose
# underscore is DOUBLY non-compliant: aim-build rejects both "name has invalid
# format" (the `_`) and "name must match dir". So the distributed dir name AND its
# SKILL.md `name` must be normalized to a compliant form on emit. (run_05e60d5b —
# reverses run_62055da6's approach A, which matched name to the raw `s_` dir and
# still failed the character-set rule.)
_COMPLIANT_NAME = re.compile(r"^[a-z0-9-]+$")


def _compliant_skill_name(raw: str) -> str:
    """Normalize a SwarmAI skill name to an AIM-compliant one: lowercase, strip a
    leading ``s_`` prefix, turn any remaining ``_`` into ``-``, drop any other illegal
    char, collapse repeat hyphens, strip leading/trailing hyphens. The result is
    GUARANTEED non-empty and to match ``^[a-z0-9-]+$``.
    (``s_repo-to-ddd`` → ``repo-to-ddd``; ``s_ddd-manager`` → ``ddd-manager``.)

    Fail-LOUD on a degenerate input that normalizes to empty (a dir named ``s_`` /
    ``s___`` / all-dots / pure-non-ascii): an un-nameable skill dir is an author
    error — emitting it under an empty name would collapse the skill INTO the skills
    root (``out_skills / "" == out_skills``) and corrupt siblings (Gate-2 HIGH,
    run_05e60d5b). Raising here surfaces the bad source dir instead of silently
    shipping a broken package."""
    s = raw.lower()
    if s.startswith("s_"):
        s = s[2:]
    s = re.sub(r"[^a-z0-9-]", "-", s)      # _ and any other illegal char → hyphen
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s or not _COMPLIANT_NAME.match(s):
        raise PackagingError(
            f"skill dir name {raw!r} normalizes to an empty/invalid AIM name — "
            f"rename it to something matching ^[a-z0-9-]+$ (after the s_ prefix)"
        )
    return s


def _compliant_skill_map(skills: list[str]) -> dict[str, str]:
    """Map each raw skill name → its AIM-compliant name. Fail-LOUD on a collision
    (two raw names normalizing to the same compliant name) — silently merging two
    skills into one dir would drop a capability from the package."""
    out: dict[str, str] = {}
    seen: dict[str, str] = {}
    for raw in sorted(skills):
        comp = _compliant_skill_name(raw)
        if comp in seen and seen[comp] != raw:
            raise PackagingError(
                f"skill name collision after AIM normalization: '{raw}' and "
                f"'{seen[comp]}' both map to '{comp}' — rename one at the source"
            )
        seen[comp] = raw
        out[raw] = comp
    return out


def _copy_skill_dirs(ddd_dir: Path, out_skills: Path, skills: list[str]) -> list[str]:
    """Copy each included skill dir into the package under its AIM-COMPLIANT name,
    and set the emitted SKILL.md `name` to that same compliant value (name==dirname
    AND character-set-legal). Emit-layer only — the source dir/SKILL.md are untouched.
    Returns sorted relative files."""
    copied: list[str] = []
    caps_root = ddd_path(ddd_dir, "capabilities")
    name_map = _compliant_skill_map(skills)  # raises on collision (fail-loud)
    for raw in sorted(skills):
        src = caps_root / raw
        if not src.is_dir():
            continue
        compliant = name_map[raw]
        dst = out_skills / compliant
        shutil.copytree(src, dst, dirs_exist_ok=True)
        _rewrite_skill_name(dst / "SKILL.md", compliant)
        for f in sorted(dst.rglob("*")):
            if f.is_file():
                copied.append(str(f.relative_to(out_skills.parent)))
    return sorted(copied)


# Matches a top-level YAML frontmatter `name:` line (`re.MULTILINE` anchors to line
# start, so a tab/space-INDENTED `name:` nested under another key is correctly NOT
# matched). Tolerates whitespace BEFORE the colon (`name :`) — a valid YAML form
# that aim-build still validates against, so we must normalize it too.
_SKILL_NAME_LINE = re.compile(r"^name[ \t]*:[ \t]*.*$", re.MULTILINE)


def _rewrite_skill_name(skill_md: Path, dir_name: str) -> None:
    """Rewrite the emitted SKILL.md `name:` value to equal ``dir_name`` — which the
    caller has already normalized to an AIM-compliant form (name==dirname AND
    ^[a-z0-9-]+$). No-op (idempotent) if already matching. Fail-soft: a missing
    SKILL.md, or one without a `name:` line, is left untouched (a skill dir need
    not carry a SKILL.md — e.g. a script-only sub-package)."""
    if not skill_md.is_file():
        return
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    # Function replacement (not a template string): a template would interpret
    # backslash / `\g<n>` sequences in dir_name as regex backreferences and corrupt
    # output. A callable's return is used literally. (dir_name is a
    # `_compliant_skill_name` result, but keep the replacement literal defensively.)
    new_text, n = _SKILL_NAME_LINE.subn(lambda _m: f"name: {dir_name}", text, count=1)
    if n and new_text != text:
        skill_md.write_text(new_text, encoding="utf-8")


def _shared_source_label(ddd_dir: Path, source: Path) -> str:
    """A readable relative label for a shared-source dir, for warning messages."""
    try:
        return str(source.resolve().relative_to(ddd_dir.resolve()))
    except ValueError:
        return source.name


def _collect_shared_sources(ddd_dir: Path) -> list[Path]:
    """Discover the DDD's shared-code source dirs to materialize into distributed skills.

    Two GENERIC (never project-hardcoded) sources, in precedence order:
    1. ``<capabilities>/_shared/`` — the conventional flat shared layer.
    2. The parent dir of each ``aim.json`` ``plugins.domain_tools`` entry — a data-agent
       DDD keeps its SDK as the ``data-source`` GOVERNED ASSET (and ③ moat) under
       ``assets/…``, NOT in ``_shared``; domain_tools declares those tool files, so their
       parent dir is the SDK source (CMHK → ``assets/data-source/scripts``).

    Returns existing dirs only, deduped, in the order above (``_shared`` first, then
    domain_tools parent-dirs sorted) — this order defines materialization precedence
    (first-writer-wins per dest filename). No sources → ``[]`` (caller no-ops).

    🔒 SANDBOXED (Gate-2 CRITICAL, run_b3c3d1e4): ``domain_tools`` entries come from an
    author-editable ``aim.json`` field, so a ``../``-traversal, an absolute path, or a
    bare filename (``parent == "."`` → the whole DDD root) could otherwise turn an
    ARBITRARY host dir into a materialization source and silently leak host files into the
    distributed package (the content-safety scan only catches pattern-matched secrets, not
    arbitrary proprietary code). So a domain_tools source is admitted ONLY if it resolves
    strictly INSIDE the DDD dir AND is not the DDD root itself AND is not under a skill's
    own capabilities dir (skill-owned code is not shared — else it cross-materializes into
    sibling skills). Rejected entries are dropped with a WARN via the logger.
    """
    sources: list[Path] = []
    seen: set[Path] = set()
    ddd_root = ddd_dir.resolve()
    caps_root = ddd_path(ddd_dir, "capabilities").resolve()

    def _add(d: Path, *, trusted: bool) -> None:
        rd = d.resolve()
        if not d.is_dir() or rd in seen:
            return
        if not trusted:
            # sandbox: must be strictly inside the DDD, not the root, not skill-owned
            try:
                rel = rd.relative_to(ddd_root)
            except ValueError:
                logger.warning("domain_tools source %s escapes the DDD dir — skipped", d)
                return
            if rel == Path("."):
                logger.warning("domain_tools source resolves to the DDD root — skipped (too broad)")
                return
            # under 4-capabilities/<skill>/… → skill-owned code, not a shared source
            # (would cross-materialize a skill's private files into sibling skills).
            try:
                caps_rel = rd.relative_to(caps_root)
            except ValueError:
                caps_rel = None  # not under capabilities — fine (e.g. assets/data-source/scripts)
            if caps_rel is not None and caps_rel != Path("."):
                logger.warning(
                    "domain_tools source %s is under a skill dir (skill-owned, not shared) — skipped", d
                )
                return
        seen.add(rd)
        sources.append(d)

    _add(ddd_path(ddd_dir, "capabilities") / "_shared", trusted=True)
    aim = _read_aim(ddd_dir)
    plugins = aim.get("plugins") if isinstance(aim, dict) else None
    tools = plugins.get("domain_tools") if isinstance(plugins, dict) else None
    if isinstance(tools, list):
        for parent in sorted({str(Path(t).parent) for t in tools if isinstance(t, str)}):
            _add(ddd_dir / parent, trusted=False)
    return sources


def _materialize_shared(ddd_dir: Path, out_skills: Path, skills: list[str]) -> list[str]:
    """Materialize the DDD's shared code layer(s) INTO each emitted skill's scripts/.

    A DDD may keep single-source shared code that its skills import at runtime via a
    ``parents[2]/_shared`` (or asset-relative) sys.path injection. That injection resolves
    LOCALLY, but a distributed package copies ONLY the skill dir (``_copy_skill_dirs``) —
    so the shared code lands OUTSIDE the package and the import fails at the foreign host.
    This copies each shared ``*.py`` module into every emitted skill's ``scripts/`` so the
    distributed skill is self-contained: the SAME ``import client`` line resolves from the
    shared dir locally and from the materialized sibling copy in the package.

    Sources are discovered by :func:`_collect_shared_sources` (``<capabilities>/_shared/``
    + ``aim.json`` domain_tools parent-dirs — so a data-source-asset SDK need not be moved
    into ``_shared`` to be distributable). Rules, applied per source:
    - **FLAT top-level .py modules ONLY** (skips ``__pycache__`` and ``__init__.py``).
    - Dest = each emitted skill's ``scripts/`` dir (created if absent).
    - Collision with a skill-OWNED same-name file → SKIP + WARN (never overwrite skill code).
    - Cross-source dedup: a dest filename written by an earlier source is not overwritten
      by a later one (first-writer-wins; ``_shared`` precedes domain_tools dirs).
    - No sources → no-op (returns empty), emitted tree byte-identical to before.

    ⚠️ FLAT-ONLY is ENFORCED, not assumed. The runtime contract is a sys.path injection
    that imports each module flat (``import client``). A **sub-package** (a sub-dir) in a
    source would build a package that PASSES the build but fails at the foreign host with
    ``ModuleNotFoundError`` on the sub-import (Gate-2 MED, run_493d964a) — so a sub-dir
    emits a LOUD warning and is NOT materialized.

    Returns the ``warnings`` list (empty when clean). Materialized files are picked up by
    each emit target's final ``rglob`` over out_dir, so this does not return a file list.
    """
    sources = _collect_shared_sources(ddd_dir)
    if not sources:
        return []
    warnings: list[str] = []

    # Collect flat modules per source (sub-dirs → LOUD WARN, not materialized).
    src_files: list[Path] = []
    seen_names: set[str] = set()  # cross-source dedup: first source wins a given filename
    for source in sources:
        rel = _shared_source_label(ddd_dir, source)
        for sub in sorted(d.name for d in source.iterdir() if d.is_dir() and d.name != "__pycache__"):
            warnings.append(
                f"{rel}/{sub}/ is a sub-package — flat materialization supports top-level "
                f"modules only; it was NOT materialized (a distributed skill importing it "
                f"would fail at the host). Flatten it or vend it inside the skill."
            )
        for p in sorted(source.glob("*.py")):
            if not p.is_file() or p.name == "__init__.py":
                continue
            if p.name in seen_names:
                continue  # first-writer-wins across sources (deterministic precedence)
            seen_names.add(p.name)
            src_files.append(p)
    if not src_files:
        return warnings

    # Skills are emitted under their AIM-COMPLIANT dir name (_copy_skill_dirs), so
    # materialize into that same compliant dir — matching on the raw name would miss
    # every emitted dir and silently ship a non-self-contained package.
    for raw in sorted(skills):
        name = _compliant_skill_name(raw)
        if not (out_skills / name).is_dir():
            continue  # skill wasn't emitted (excluded) — nothing to make self-contained
        skill_scripts = out_skills / name / "scripts"
        skill_scripts.mkdir(parents=True, exist_ok=True)
        for src in src_files:
            dst = skill_scripts / src.name
            if dst.exists():
                warnings.append(
                    f"skill '{name}' scripts/{src.name} is skill-owned — NOT overwritten "
                    f"with shared {src.name} (materialization skipped for this file)"
                )
                continue
            shutil.copy2(src, dst)
    return warnings


def _read_aim(ddd_dir: Path) -> dict[str, Any]:
    try:
        d = json.loads((ddd_dir / "aim.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


# Six-section knowledge docs → context/ (deterministic set).
# Single-source per Run 0 (project_registry.DDD_CANONICAL_DOCS) — no stray literal.
_KNOWLEDGE_DOCS = DDD_CANONICAL_DOCS


# ---------------------------------------------------------------------------
# Target A — AIM capabilities package
# ---------------------------------------------------------------------------
def emit_target_aim(ddd_dir: Path, out_dir: Path, *, with_enablement: bool = False) -> PackageResult:
    ddd_name = ddd_dir.name
    aim = _read_aim(ddd_dir)
    domain, enablement_excl, unclassified = split_skills(ddd_dir)
    # with_enablement (opt-in, bare-host variant): ship the class-A enablement engine
    # (e.g. s_repo-to-ddd) as a portable copy so a host lacking SwarmAI/AIM built-ins
    # can still USE the capability. Unclassified is NEVER shipped (author-error guard).
    # Default (False) is byte-identical to the lean knowledge-only package.
    engine_skills = sorted(enablement_excl) if with_enablement else []
    skills_to_copy = sorted(set(domain) | set(engine_skills))
    res = PackageResult(target=TARGET_AIM, out_dir=out_dir,
                        skills_included=skills_to_copy,
                        skills_excluded=sorted(
                            ([] if with_enablement else enablement_excl) + unclassified))
    for s in unclassified:
        res.warnings.append(f"skill '{s}' on disk but in NEITHER native_skills nor domain_skills → excluded (unclassified)")
    if with_enablement and engine_skills:
        res.warnings.append(f"with-enablement: shipping portable copy of enablement skill(s) {engine_skills} for bare foreign hosts")

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
            # skillNames must reference the emitted (AIM-compliant) dir names, not the
            # raw s_-prefixed source names — else the agent-spec points at dirs that
            # don't exist in the package. Same normalization _copy_skill_dirs applies.
            "skills": {"skillNames": [_compliant_skill_name(s) for s in skills_to_copy] or ["*"]},
            "context": {"contextNames": ["*"]},
            "agentSops": {"agentSopNames": ["*"]},
        },
    }
    _write_json(out_dir / "agents" / f"{normalize_name(ddd_name, prefix='')}.agent-spec.json", agent_spec)

    # skills/, context/ (knowledge docs + AGENTS.md), agent-sops/ (gates + refresher)
    res.files += _copy_skill_dirs(ddd_dir, out_dir / "skills", skills_to_copy)
    res.warnings += _materialize_shared(ddd_dir, out_dir / "skills", skills_to_copy)
    ctx = out_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    for doc in (*_KNOWLEDGE_DOCS, "AGENTS.md"):
        src = ddd_path(ddd_dir, doc)  # migrated docs live under 2-understanding/
        if src.is_file():
            shutil.copy2(src, ctx / doc)
    sops = out_dir / "agent-sops"
    sops.mkdir(parents=True, exist_ok=True)
    for doc in ("REFRESHER.md",):
        src = ddd_path(ddd_dir, doc)  # REFRESHER stays at root; resolver handles it
        if src.is_file():
            (sops / f"{Path(doc).stem.lower()}.sop.md").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8")

    res.files = sorted({str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file()})
    return res


# ---------------------------------------------------------------------------
# Target B — Open-Plugins plugin
# ---------------------------------------------------------------------------
def emit_target_open_plugin(ddd_dir: Path, out_dir: Path, *, with_enablement: bool = False) -> PackageResult:
    ddd_name = ddd_dir.name
    aim = _read_aim(ddd_dir)
    domain, enablement_excl, unclassified = split_skills(ddd_dir)
    # with_enablement (opt-in, bare-host variant): ship the class-A enablement engine
    # as a portable copy. Default (False) = lean knowledge-only, byte-identical to before.
    engine_skills = sorted(enablement_excl) if with_enablement else []
    skills_to_copy = sorted(set(domain) | set(engine_skills))
    res = PackageResult(target=TARGET_OPEN_PLUGIN, out_dir=out_dir,
                        skills_included=skills_to_copy,
                        skills_excluded=sorted(
                            ([] if with_enablement else enablement_excl) + unclassified))
    for s in unclassified:
        res.warnings.append(f"skill '{s}' on disk but in NEITHER native_skills nor domain_skills → excluded (unclassified)")
    if with_enablement and engine_skills:
        res.warnings.append(f"with-enablement: shipping portable copy of enablement skill(s) {engine_skills} for bare foreign hosts")

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
    res.files += _copy_skill_dirs(ddd_dir, out_dir / "skills", skills_to_copy)
    res.warnings += _materialize_shared(ddd_dir, out_dir / "skills", skills_to_copy)

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
        src = ddd_path(ddd_dir, doc)  # migrated docs live under 2-understanding/
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
    with_enablement: bool = False,
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
            res = emit_target_aim(ddd_dir, out_dir, with_enablement=with_enablement)
        elif target == TARGET_OPEN_PLUGIN:
            res = emit_target_open_plugin(ddd_dir, out_dir, with_enablement=with_enablement)
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
