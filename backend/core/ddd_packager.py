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
from core.ddd_paths import ddd_path, KNOWLEDGE_CORPUS_DIR  # six-section layout resolver (SSOT)

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
        except Exception as exc:  # noqa: BLE001
            # Degrade-OBSERVABLE. [] reads as "this domain declares no skills", so a
            # malformed manifest silently ships a package with its skills stripped.
            logger.warning("cannot read domain skills from %s, packaging none: %s",
                           aim_path, exc)
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
    # Quoted assignment (Gate-2 H1) — the quotes are themselves a strong secret signal,
    # so ANY 6+ char quoted value counts (applies in EVERY file type, incl. prose).
    re.compile(r"(?i)\b(secret|password|passwd|token|api[_-]?key)\s*[=:]\s*['\"][^'\"]{6,}['\"]"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),             # GitHub token
)

# Unquoted assignment (.env/shell/YAML) — kept STRICT (`{6,}`, an all-letter value like
# `password=abcdefgh` IS caught) so the detector is never weakened in a config/script file.
# The prose false-positive it used to cause (`token = operating a bespoke idP` in a `.md`
# knowledge doc) is suppressed by FILE TYPE, NOT by weakening the pattern: see `_scan_text`,
# which drops an all-LETTER unquoted value ONLY in a prose file (.md/.rst/.txt). A secret in
# a real config/script extension is still matched by this exact pattern. The value is a named
# group so `_scan_text` can inspect it. (run_6e4bced6, REVIEW finding — do NOT re-tighten the
# pattern globally; that fail-opens all-letter secrets in .env — scope the FP fix to prose.)
_UNQUOTED_SECRET: re.Pattern[str] = re.compile(
    r"(?i)\b(?:secret|password|passwd|token|api[_-]?key)\s*[=:]\s*(?P<val>[^\s'\"]{6,})"
)
# Prose extensions where an all-letter unquoted `word = word` is English, not a secret.
_PROSE_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown", ".rst", ".txt"})

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


def _in_deliverables_zone(rel_file: str) -> bool:
    """True iff a scan-finding's ``file`` (a path relative to the emitted tree) lives
    under the ``deliverables/`` zone. Uses path PARTS, not a string prefix — OS-separator
    safe AND exact (a sibling ``deliverables-foo/`` must NOT match). Gate-1 finding C."""
    parts = Path(rel_file).parts
    return bool(parts) and parts[0] == _DELIVERABLES_DIR


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
    # Prose files (.md/.rst/.txt) suppress the all-letter unquoted-secret false positive,
    # but NARROWLY (Gate-2 run_6e4bced6): the real FP is a SENTENCE — "a shared token =
    # operating a bespoke idP" — where the matched value is one word FOLLOWED BY MORE PROSE
    # WORDS. An ISOLATED assignment ("password=hunterhunter", "TOKEN=mytokenname" whether the
    # whole RHS or the last token on the line) is a real credential and MUST flag even in a
    # .md — the earlier "any all-letter value in prose" rule fail-opened exactly that. So
    # suppress ONLY when: prose file AND all-letter value AND the value is followed on the
    # line by whitespace + another word (the sentence tail). A value with any non-letter
    # (digit/symbol) is flagged everywhere; config/script files flag all values (no weakening).
    is_prose = Path(rel).suffix.lower() in _PROSE_SUFFIXES
    for i, line in enumerate(text.splitlines(), start=1):
        for pat in _SECRET_PATTERNS:
            m = pat.search(line)
            if m:
                findings.append(ScanFinding(rel, "secret", m.group(0)[:40], i))
        um = _UNQUOTED_SECRET.search(line)
        if um:
            val = um.group("val")
            # Sentence tail = the value is followed by " <word>" on the same line (prose),
            # not the end of an isolated assignment.
            tail = line[um.end():]
            has_prose_tail = bool(re.match(r"\s+\S", tail))
            suppress = is_prose and val.isalpha() and has_prose_tail
            if not suppress:
                findings.append(ScanFinding(rel, "secret", um.group(0)[:40], i))
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
        _rewrite_skill_body_refs(dst / "SKILL.md", name_map)
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


# A sibling cross-reference line in a SKILL.md — the ONLY lines where an s_-prefixed name
# is a metadata pointer to another skill (whether in a `description: >` block or the body).
# Anchored to the SIBLINGS/NOT FOR prefix at line start so PROSE and CODE FENCES that
# legitimately mention an s_ name (a python example, a description of LOCAL SwarmAI
# behavior) are NEVER rewritten (Gate-1 C3). The `[^:\n]*` tolerates a parenthetical
# qualifier between the keyword and the colon — the real shape is
# `SIBLINGS (SecDLC ④capabilities): ...`, not a bare `SIBLINGS:`.
_SIBLING_META_LINE = re.compile(r"^(\s*(?:SIBLINGS?|NOT FOR)\b[^:\n]*:.*)$", re.MULTILINE | re.IGNORECASE)


def _rewrite_skill_body_refs(skill_md: Path, name_map: dict[str, str]) -> None:
    """Rewrite ``s_<sibling>`` references to their AIM-compliant names, but ONLY on lines
    matching the ``SIBLINGS:``/``NOT FOR:`` metadata prefix (``_SIBLING_META_LINE``) —
    where the name is a machine-resolved pointer to another packaged skill (dir is
    ``<compliant>``, so a raw ``s_`` ref dangles). A normal prose sentence or code-fence
    line that merely mentions an ``s_`` name is left UNTOUCHED, because it does NOT start
    with that metadata prefix — it references LOCAL/source-side behavior and is correct as
    written (Gate-1 C3). (A line literally shaped ``NOT FOR: s_x`` inside a fence WOULD
    match the prefix, but the rewrite there is still to the correct compliant name — the
    scoping is by the metadata prefix, not by fence-awareness.) Only the KNOWN sibling
    names in ``name_map`` are replaced, whole-token, longest-first so a prefix name can't
    partially clobber a longer one. Fail-soft on a missing/unreadable SKILL.md."""
    if not skill_md.is_file():
        return
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    # Only raw names that actually change under normalization, longest-first (so
    # s_fx-report is tried before a hypothetical s_fx that is its prefix).
    renames = sorted(
        ((raw, comp) for raw, comp in name_map.items() if raw != comp),
        key=lambda kv: len(kv[0]), reverse=True,
    )
    if not renames:
        return

    def _fix_line(m: "re.Match[str]") -> str:
        line = m.group(1)
        for raw, comp in renames:
            # whole-token: the raw name not flanked by an identifier char (so a longer
            # sibling that merely CONTAINS this name as a substring is not mangled).
            line = re.sub(rf"(?<![\w-]){re.escape(raw)}(?![\w-])", comp, line)
        return line

    new_text = _SIBLING_META_LINE.sub(_fix_line, text)
    if new_text != text:
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


def _skill_description(ddd_dir: Path, raw_skill: str) -> str:
    """First-sentence description of a skill, via the SSOT frontmatter parser
    (``skill_manager.parse_frontmatter`` — never a hand-rolled `description:` grep,
    so a folded ``>`` or quoted value both resolve). Empty string if the skill has
    no SKILL.md / no description (caller decides how to render an empty one)."""
    from core.skill_manager import SkillParseError, parse_frontmatter

    skill_md = ddd_path(ddd_dir, "capabilities") / raw_skill / "SKILL.md"
    if not skill_md.is_file():
        return ""
    # Exfil-guard parity (Gate-2 run_d8d60202, LOW): a skill dir that is a SYMLINK
    # escaping the DDD would embed the target SKILL.md's bytes into the README. The
    # upstream scan_domain_skill_dirs already filters symlinked skill dirs, but keep
    # this self-contained (mirrors _copy_corpus/_copy_deliverables/_copy_gates) so
    # README safety does not depend on a cross-module invariant.
    skill_dir = skill_md.parent
    if skill_dir.is_symlink() and _escapes_ddd(skill_dir, ddd_dir):
        return ""
    try:
        meta, _ = parse_frontmatter(skill_md)
    except (SkillParseError, OSError, ValueError):
        return ""
    desc = str(meta.get("description", "")).strip()
    # First sentence only — keep the capability table one-line-per-skill. Split on the
    # first ". " (period+space) so an embedded "e.g." mid-word does not truncate early.
    head = desc.split(". ", 1)[0].strip()
    return head[:200]


_SYSTEM_PROMPT_FILE = "SYSTEM_PROMPT.md"


def _read_system_prompt(ddd_dir: Path) -> str:
    """The DDD's authored agent runtime persona (root ``SYSTEM_PROMPT.md``), returned
    VERBATIM. This is the source the AIM ``config.systemPrompt`` includes and the
    open-plugin ``agents/<plugin>.md`` body carries.

    FAIL-LOUD if absent (run_0395c955): an installed agent with no persona is a broken
    package — we NEVER silently fall back to ``AGENTS.md`` (that is the DDD *dev*
    door-plate / AIM *consumer* entry doc, per the AIM standard §7, NOT a runtime
    persona) and NEVER synthesize a hollow one from metadata. The author writes this
    file once, at the DDD root, like README.md — so it is visible + maintainable IN
    the DDD source, not conjured at export time.

    Exfil-guard parity with the other root-file readers (Gate-2 run_6e4bced6): a
    ``SYSTEM_PROMPT.md`` that is a symlink escaping the DDD is treated as ABSENT
    (→ fail-loud), never dereferenced into the package."""
    src = ddd_dir / _SYSTEM_PROMPT_FILE
    if not src.is_file() or (src.is_symlink() and _escapes_ddd(src, ddd_dir)):
        raise PackagingError(
            f"{_SYSTEM_PROMPT_FILE} is missing at the DDD root ({ddd_dir}). It is the "
            f"agent's runtime persona (the AIM systemPrompt source) and is REQUIRED — "
            f"author it like README.md. The packager never falls back to AGENTS.md "
            f"(that is the consumer entry doc, not a persona) nor generates one."
        )
    return src.read_text(encoding="utf-8")


def _render_readme(ddd_dir: Path, skills_included: list[str]) -> str:
    """The package's top-level README (B2, run_d8d60202): if the DDD authored a root
    ``README.md`` → return it VERBATIM (author controls the wording, like a deck/corpus);
    otherwise GENERATE a minimal one so EVERY package is guaranteed to carry one — the
    install-team's first-open doc, present like Config/agent-spec.

    Generated body draws ONLY from the aim.json ``description`` field (NEVER the
    ``distribution`` block — a code.amazon.com/brazil target would both leak and
    self-block an external publish) + each included domain skill's first-sentence
    description via the SSOT frontmatter parser. Deterministic: skills sorted, no
    timestamp/run-id — byte-stable across re-emits."""
    authored = ddd_dir / "README.md"
    # Exfil-guard parity with _copy_corpus/_copy_deliverables/_copy_gates (Gate-2
    # run_6e4bced6): an authored README that is a SYMLINK escaping the DDD dir
    # (README.md → ~/.aws/credentials) would embed the target's bytes into the
    # package. is_file() follows symlinks, so guard it explicitly — an escaping
    # link is treated as ABSENT (fall through to the generated fallback).
    if authored.is_file() and not (authored.is_symlink() and _escapes_ddd(authored, ddd_dir)):
        return authored.read_text(encoding="utf-8")

    aim = _read_aim(ddd_dir)
    name = str(aim.get("name") or ddd_dir.name)
    description = str(aim.get("description", "")).strip()

    lines = [f"# {name}", ""]
    if description:
        lines += [description, ""]
    lines += ["## Capabilities", ""]
    if skills_included:
        lines += ["| Skill | What it does |", "| --- | --- |"]
        for raw in sorted(skills_included):
            # Sanitize the free-form description for a markdown table CELL (Gate-2
            # run_d8d60202, MEDIUM): an unescaped `|` would inject extra columns, and
            # a newline would break the single-row-per-skill layout. Escape `|` and
            # collapse any newline to a space.
            desc = (_skill_description(ddd_dir, raw) or "—").replace("|", "\\|")
            desc = " ".join(desc.split())
            lines.append(f"| `{_compliant_skill_name(raw)}` | {desc} |")
    else:
        lines.append("_(No domain skills — a pure-knowledge package.)_")
    # Directory map — the third element of the AIM §7 consumer-entry contract (overview +
    # MAP + usage). This is the FLAT package layout (not the DDD six-section source tree —
    # that source map lives in the DDD's own AGENTS.md, which does NOT ship, P1). Static:
    # these are the dirs the packager emits; deterministic, no filesystem walk.
    lines += [
        "",
        "## Package layout",
        "",
        "| Path | What's here |",
        "| --- | --- |",
        "| `skills/` | the domain skills this package installs |",
        "| `context/` | reference docs; `context/knowledge/` = the retrievable corpus |",
        "| `context/SYSTEM_PROMPT.md` | the installed agent's system prompt (persona) |",
        "| `agents/` | the agent spec(s) |",
        "| `agent-sops/` | always-apply SOPs incl. `agent-sops/gates/` standards |",
    ]
    lines += [
        "",
        "## Install",
        "",
        "```bash",
        "aim plugins install " + normalize_name(name, prefix=""),
        "```",
        "",
    ]
    return "\n".join(lines)


# Six-section knowledge docs → context/ (deterministic set).
# Single-source per Run 0 (project_registry.DDD_CANONICAL_DOCS) — no stray literal.
_KNOWLEDGE_DOCS = DDD_CANONICAL_DOCS

# The un-numbered non-section dir carrying human-facing deliverables (decks, diagrams,
# reports). Shipped whole (incl. binaries) as human artifacts, NOT recall corpus.
_DELIVERABLES_DIR = "deliverables"


def _escapes_ddd(link: Path, ddd_root: Path) -> bool:
    """True if a symlink resolves to a target OUTSIDE the DDD dir (an exfil vector:
    ``deliverables/creds → ~/.aws/credentials`` would otherwise dereference the real
    file INTO the package). Mirrors the ``_collect_shared_sources`` sandbox. A broken
    link (resolve fails) is treated as escaping (skip it). Gate-2 finding, run_6e4bced6."""
    try:
        return not link.resolve().is_relative_to(ddd_root.resolve())
    except (OSError, RuntimeError, ValueError):
        return True


def _copy_corpus(ddd_dir: Path, dest: Path) -> list[str]:
    """Copy the DDD's sedimented recall corpus (``2-understanding/knowledge/*.md``)
    into ``dest``. The corpus IS the DDD's value (design §6) — a distributed package
    without it ships skills but not the knowledge that informs them.

    - FLAT ``*.md`` glob (the corpus is a flat recall dir by convention; a stray
      ``.gitkeep`` / non-md is excluded by the suffix filter).
    - A ``.md`` that is a SYMLINK escaping the DDD dir is SKIPPED + WARNed (exfil guard,
      Gate-2 run_6e4bced6) — ``copy2`` would otherwise dereference it into the package.
    - No corpus dir → no-op (a 0-corpus DDD is valid), returns [].
    - Deterministic (sorted). Returns the relative paths written under ``dest``.
    """
    src = ddd_dir / KNOWLEDGE_CORPUS_DIR
    if not src.is_dir():
        return []
    written: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for md in sorted(src.glob("*.md")):
        if md.is_symlink() and _escapes_ddd(md, ddd_dir):
            logger.warning("corpus file %s is a symlink escaping the DDD — skipped (exfil guard)", md.name)
            continue
        shutil.copy2(md, dest / md.name)
        written.append(md.name)
    return written


def _copy_deliverables(ddd_dir: Path, out_dir: Path) -> list[str]:
    """Copy the DDD's ``deliverables/`` tree WHOLE (recursively, incl. binaries) into
    ``out_dir/deliverables/`` as human-facing artifacts. Absent → no-op, returns [].

    Payload boundary (run_eb45c28d): copy ONLY ``deliverables/`` — never sweep in
    ``.artifacts/`` / ``code-intel.json`` / decay-archives (live-tree noise). This
    function is scoped to the one dir on purpose; it does not walk the DDD root.

    Exfil guard (Gate-2 run_6e4bced6): ``copytree`` defaults to dereferencing symlinks
    (``symlinks=False`` copies the TARGET's bytes) — a ``deliverables/creds →
    ~/.aws/credentials`` would package the real credential (a binary target even bypasses
    the content scan). We copy with ``symlinks=True`` (preserve the link, never its bytes)
    AND ``ignore`` any symlink whose target escapes the DDD dir (dropped entirely, so no
    dangling escaping link ships either). Mirrors the ``_collect_shared_sources`` sandbox.
    """
    src = ddd_dir / _DELIVERABLES_DIR
    if not src.is_dir():
        return []
    dst = out_dir / _DELIVERABLES_DIR

    def _ignore_escaping_links(dir_path: str, names: list[str]) -> set[str]:
        drop: set[str] = set()
        for n in names:
            p = Path(dir_path) / n
            if p.is_symlink() and _escapes_ddd(p, ddd_dir):
                logger.warning("deliverable %s is a symlink escaping the DDD — dropped (exfil guard)",
                               p.relative_to(ddd_dir))
                drop.add(n)
        return drop

    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True, ignore=_ignore_escaping_links)
    return sorted(str(p.relative_to(out_dir)) for p in dst.rglob("*") if p.is_file())


# Build-noise names that must NEVER ship inside a copied section (a live DDD's
# 3-gates/ accumulates a __pycache__/ next to its .py gate scripts). Excluded by
# DIR NAME (so copytree never recurses into it) AND by suffix (a stray .pyc).
_GATES_EXCLUDE_DIRS = frozenset({"__pycache__"})
_GATES_EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo"})


def _copy_gates(ddd_dir: Path, dst: Path) -> list[str]:
    """Copy the DDD's ③ ``3-gates/`` section WHOLE (recursively) into ``dst``.

    The gate section is MIXED — it holds ``.md`` standards (e.g. a security-coding
    baseline a consuming repo drops into ``.kiro/steering/``), executable ``.py``/``.sh``
    gate scripts, AND a ``context/includes/`` subdir of denylist data. A flat
    ``glob("*.md")`` (the corpus pattern) would silently drop the executables + the
    subdir — so this mirrors ``_copy_deliverables`` (recursive ``copytree``), NOT
    ``_copy_corpus``. Differences from deliverables:
    - EXCLUDES ``__pycache__``/``*.pyc`` build noise (a live 3-gates has it next to
      its gate scripts; the deliverables zone does not) — run_f4d1489b payload boundary.
    - No-op when the section is absent OR contains only a ``.gitkeep`` (a freshly
      scaffolded 3-gates ships nothing) — returns ``[]``.

    Exfil guard (parity with ``_copy_corpus``/``_copy_deliverables``, Gate-2
    run_6e4bced6): ``symlinks=True`` preserves a link (never dereferences the target's
    bytes) AND any symlink escaping the DDD dir is dropped entirely.
    """
    src = ddd_path(ddd_dir, "gates")
    if not src.is_dir():
        return []
    # No-op if the section carries no real SHIPPABLE payload (only .gitkeep / empty /
    # excluded build-noise / an escaping symlink the copy stage would drop). The
    # escaping-symlink clause keeps this pre-check in lockstep with the _ignore closure
    # below — otherwise is_file() (which follows symlinks) would count an escaping link
    # as payload and ship a spurious empty gates/ dir (Gate-2 LOW, run_f4d1489b).
    real = [
        p for p in src.rglob("*")
        if p.is_file()
        and p.name != ".gitkeep"
        and p.suffix not in _GATES_EXCLUDE_SUFFIXES
        and not any(part in _GATES_EXCLUDE_DIRS for part in p.relative_to(src).parts)
        and not (p.is_symlink() and _escapes_ddd(p, ddd_dir))
    ]
    if not real:
        return []

    def _ignore(dir_path: str, names: list[str]) -> set[str]:
        drop: set[str] = set()
        for n in names:
            p = Path(dir_path) / n
            if n in _GATES_EXCLUDE_DIRS or Path(n).suffix in _GATES_EXCLUDE_SUFFIXES:
                drop.add(n)
            elif n == ".gitkeep":
                drop.add(n)  # scaffold marker, not shippable payload
            elif p.is_symlink() and _escapes_ddd(p, ddd_dir):
                logger.warning("gate file %s is a symlink escaping the DDD — dropped (exfil guard)",
                               p.relative_to(ddd_dir))
                drop.add(n)
        return drop

    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True, ignore=_ignore)
    return sorted(str(p.relative_to(dst.parent)) for p in dst.rglob("*") if p.is_file())


# CONTRACT-DRIVEN tool defaults — the FALLBACK when a DDD's aim.json declares no
# plugins.tools / plugins.allowed_tools. Base tools ONLY (read/write/shell); NO @<mcp>
# is baked in here. Every @<mcp> tool is derived from the SINGLE source _extract_mcp
# (mirrors the official SampleAICapabilities shape where allowedTools = read + the MCP
# bundles). Baking @builder-mcp into a default would privilege one MCP + create a second
# source of truth for which MCPs appear in tools (Gate-1 run_91a812c6). A DDD that needs
# builder-mcp declares it in plugins.mcp — it then derives into both lists automatically.
_DEFAULT_KIRO_TOOLS = ["read", "write", "shell"]
_DEFAULT_KIRO_ALLOWED_BASE = ["read"]  # non-MCP allowed base; @<mcp> appended from _extract_mcp


def _build_kiro_client_config(ddd_dir: Path, has_corpus: bool) -> dict[str, Any]:
    """Build the clientConfig.kiroCli block for an emitted agent-spec, CONTRACT-DRIVEN
    from the DDD's aim.json declaration (mirrors the official SampleAICapabilities shape:
    per-agent tools + allowedTools + a knowledgeBase resource over context/knowledge).

    tools        = declared plugins.tools  OR _DEFAULT_KIRO_TOOLS, then UNION every
                   declared MCP as @<name> (single source = _extract_mcp), deduped.
    allowedTools = declared plugins.allowed_tools OR (_DEFAULT_KIRO_ALLOWED_BASE + the
                   same @<mcp> set) — then CLAMPED to a subset of tools (an author who
                   over-claims an allowed tool not granted in tools can't leak it).
    Both @<mcp> segments derive from ONE _extract_mcp call, so allowedTools ⊆ tools
    holds by construction (Gate-1). The corpus is a RETRIEVABLE knowledgeBase (indexType
    fast) rather than resident context:["*"] (standard §5)."""
    mcp_tags = [f"@{name}" for name in sorted(_extract_mcp(ddd_dir))]
    declared_tools, declared_allowed = _extract_tools(ddd_dir)

    tools = list(declared_tools) if declared_tools is not None else list(_DEFAULT_KIRO_TOOLS)
    for tag in mcp_tags:  # union the declared MCPs (dedup — builder-mcp only appears once)
        if tag not in tools:
            tools.append(tag)

    if declared_allowed is not None:
        allowed = list(declared_allowed)
    else:
        allowed = list(_DEFAULT_KIRO_ALLOWED_BASE) + [t for t in mcp_tags if t not in _DEFAULT_KIRO_ALLOWED_BASE]
    # Clamp: allowedTools is a GATE over tools — never allow what isn't granted (preserve
    # order, drop over-claims). Guarantees allowedTools ⊆ tools even on a declared override.
    tools_set = set(tools)
    allowed = [t for t in allowed if t in tools_set]

    cfg: dict[str, Any] = {"tools": tools, "allowedTools": allowed}
    if has_corpus:
        cfg["resources"] = [{
            "type": "knowledgeBase",
            "source": "file://{{aim:filepath:context/knowledge}}",
            "name": "DomainKnowledge",
            "description": "The DDD's sedimented knowledge corpus, retrievable on demand.",
            "indexType": "fast",
            "autoUpdate": True,
        }]
    return cfg


def _emit_gate_sops(ddd_dir: Path, sops_dir: Path) -> list[str]:
    """Emit each TOP-LEVEL ``.md`` gate STANDARD as ``agent-sops/<stem>.sop.md`` so the
    AIM runtime DISCOVERS it as a SOP (it recognizes ``agent-sops/*.sop.md`` only — a
    nested ``agent-sops/gates/foo.md`` ships but is undiscoverable; verified against the
    official SampleAICapabilities package). Only top-level ``.md`` files convert — a
    ``.py``/``.sh`` gate is a CI script (not an agent-read SOP) and a ``context/includes/``
    data file is not a standard, so both stay under ``gates/`` untouched.

    FAIL-LOUD on a stem collision (a gate named ``refresher.md`` would clobber the
    REFRESHER SOP, or two gates share a stem) — mirrors ``_compliant_skill_map``'s
    fail-loud-on-collision rather than a silent last-writer-wins overwrite."""
    gates_src = ddd_path(ddd_dir, "gates")
    if not gates_src.is_dir():
        return []
    emitted: list[str] = []
    for md in sorted(gates_src.glob("*.md")):  # TOP-LEVEL only (not context/includes/)
        if md.name == ".gitkeep":
            continue
        # Exfil guard parity with _copy_gates (line ~861): a gate .md that is a symlink
        # escaping the DDD must NOT be dereferenced into a .sop.md — else this new path
        # would leak the exact out-of-tree secret _copy_gates already drops (P8: the
        # sopify door must honor the same guard as the copy door).
        if md.is_symlink() and _escapes_ddd(md, ddd_dir):
            logger.warning("gate .md %s is a symlink escaping the DDD — not sopified (exfil guard)",
                           md.relative_to(ddd_dir))
            continue
        target = sops_dir / f"{md.stem.lower()}.sop.md"
        if target.exists():
            raise PackagingError(
                f"gate SOP stem collision: {md.name!r} → {target.name!r} already exists "
                f"(a gate named to match an existing SOP, e.g. refresher.md, would "
                f"clobber it) — rename the gate at the source"
            )
        target.write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
        emitted.append(target.name)
    return emitted


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

    # Fail-loud on a missing persona BEFORE writing anything (Gate-2 LOW, run_0395c955)
    # — so a broken DDD never leaves a partial half-tree on disk.
    system_prompt = _read_system_prompt(ddd_dir)

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

    # skills/, context/ (knowledge docs), context/knowledge/ (corpus),
    # agent-sops/ (refresher.sop.md + gates/ — the ③ gate section, always-apply standards).
    # NOTE (P1, run_ed775916): the source AGENTS.md is DELIBERATELY NOT copied — it is the
    # DDD *dev* door-plate describing the six-section SOURCE tree (2-understanding/,
    # 3-gates/, 4-capabilities/), which does NOT exist in the flat AIM package, so it is
    # noise to a package user. The consumer entry doc role (overview + directory map +
    # usage, AIM standard §7) is carried by the package README.md; the agent runtime
    # persona by context/SYSTEM_PROMPT.md. AGENTS.md is not an aim-build-scanned file (§2)
    # and has no machine consumer, so omitting it breaks nothing.
    res.files += _copy_skill_dirs(ddd_dir, out_dir / "skills", skills_to_copy)
    res.warnings += _materialize_shared(ddd_dir, out_dir / "skills", skills_to_copy)
    ctx = out_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    for doc in _KNOWLEDGE_DOCS:
        src = ddd_path(ddd_dir, doc)  # migrated docs live under 2-understanding/
        if src.is_file():
            shutil.copy2(src, ctx / doc)
    # Agent runtime persona → context/SYSTEM_PROMPT.md (the systemPrompt include source,
    # AIM standard §4). Read fail-loud at the top of this fn (before any write).
    (ctx / _SYSTEM_PROMPT_FILE).write_text(system_prompt, encoding="utf-8")
    sops = out_dir / "agent-sops"
    sops.mkdir(parents=True, exist_ok=True)
    for doc in ("REFRESHER.md",):
        src = ddd_path(ddd_dir, doc)  # REFRESHER stays at root; resolver handles it
        if src.is_file():
            (sops / f"{Path(doc).stem.lower()}.sop.md").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8")

    # Sedimented recall corpus → context/knowledge/ ; ③ gate section → agent-sops/gates/
    # (always-apply standards, co-located with the refresher SOP + covered by the
    # agentSops:["*"] dependency) ; human deliverables → deliverables/. ALL before the
    # res.files rebuild + the orchestrator's content-safety scan, so gates are scanned too.
    corpus_files = _copy_corpus(ddd_dir, ctx / "knowledge")
    _copy_gates(ddd_dir, sops / "gates")
    # ③ .md gate STANDARDs ALSO emit as agent-sops/<stem>.sop.md so the AIM runtime
    # DISCOVERS them as SOPs (it recognizes agent-sops/*.sop.md only, not nested .md —
    # verified against the official SampleAICapabilities package, see
    # Knowledge/Library/2026-08-25-aim-capabilities-package-standard.md §3). Fail-loud
    # on a stem collision (e.g. a gate literally named refresher.md would clobber the
    # REFRESHER SOP), mirroring _compliant_skill_map's fail-loud-on-collision.
    _emit_gate_sops(ddd_dir, sops)
    _copy_deliverables(ddd_dir, out_dir)

    # agents/<ddd>.agent-spec.json — built AFTER corpus copy so the knowledgeBase
    # resource is declared only when there is a corpus to index. clientConfig +
    # mcpRegistry mirror the official SampleAICapabilities kitchen-sink shape.
    agent_spec = {
        "schemaVersion": "1",
        "name": normalize_name(ddd_name, prefix=""),
        "config": {
            "description": str(aim.get("description", f"{ddd_name} domain brain")),
            # The agent's runtime persona is context/SYSTEM_PROMPT.md — NOT AGENTS.md
            # (which is the consumer entry doc, AIM standard §7). Matches the official
            # SampleAICapabilities shape ({{aim:include:context/included-prompt.md}}).
            "systemPrompt": f"{{{{aim:include:context/{_SYSTEM_PROMPT_FILE}}}}}",
        },
        "dependencies": {
            # skillNames must reference the emitted (AIM-compliant) dir names, not the
            # raw s_-prefixed source names — else the agent-spec points at dirs that
            # don't exist in the package. Same normalization _copy_skill_dirs applies.
            "skills": {"skillNames": [_compliant_skill_name(s) for s in skills_to_copy] or ["*"]},
            "context": {"contextNames": ["*"]},
            "agentSops": {"agentSopNames": ["*"]},
            # Every MCP a packaged skill uses must be DECLARED here or the runtime never
            # injects it. Source = aim.json plugins.mcp (author-declared ceiling, the
            # same SSOT emit_target_open_plugin's .mcp.json reads via _extract_mcp) —
            # NOT inferred from skill-body text (Gate-1: body-grep over/under-declares).
            "mcpRegistry": {name: {} for name in sorted(_extract_mcp(ddd_dir))},
        },
        "clientConfig": {"kiroCli": _build_kiro_client_config(ddd_dir, bool(corpus_files))},
    }
    _write_json(out_dir / "agents" / f"{normalize_name(ddd_name, prefix='')}.agent-spec.json", agent_spec)

    # Top-level README (authored-copy or generated fallback) — written BEFORE the
    # res.files rebuild so it lands in the manifest AND is content-safety-scanned.
    (out_dir / "README.md").write_text(_render_readme(ddd_dir, skills_to_copy), encoding="utf-8")

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

    # Fail-loud on a missing persona BEFORE writing anything (Gate-2 LOW, run_0395c955).
    system_prompt = _read_system_prompt(ddd_dir)

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

    # agents/<ddd>.md — system prompt as a sub-agent markdown. Body = the DDD's authored
    # runtime persona (SYSTEM_PROMPT.md), NOT AGENTS.md (the consumer entry doc). Fail-loud
    # if absent — same contract as the aim target (_read_system_prompt).
    agents_dir = out_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    body = system_prompt
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

    # Sedimented recall corpus → knowledge/ (a subtree distinct from the always-apply
    # rules/ — corpus is reference knowledge, not a rule) ; ③ gate section → rules/gates/
    # (gate standards ARE always-apply rules) ; human deliverables → deliverables/.
    # Kept consistent with emit_target_aim (both ship corpus + gates + deliverables).
    _copy_corpus(ddd_dir, out_dir / "knowledge")
    _copy_gates(ddd_dir, out_dir / "rules" / "gates")
    _copy_deliverables(ddd_dir, out_dir)

    # Top-level README (authored-copy or generated fallback) — same content as the AIM
    # target; written BEFORE the res.files rebuild so it's in the manifest + scanned.
    (out_dir / "README.md").write_text(_render_readme(ddd_dir, skills_to_copy), encoding="utf-8")

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


def _extract_tools(ddd_dir: Path) -> tuple[list[str] | None, list[str] | None]:
    """Pull the DDD's DECLARED agent tool grants from aim.json — the contract-driven
    source for clientConfig.kiroCli.{tools,allowedTools} (mirrors _extract_mcp's
    declaration-read). Reads ``plugins.tools`` and ``plugins.allowed_tools`` (NOT
    ``plugins.domain_tools`` — that is the data-agent SDK tool-FILE materializer, an
    unrelated concept). Returns (tools_or_None, allowed_or_None): None means "not
    declared → caller uses the default". FAIL-SOFT + isinstance-guarded like _extract_mcp:
    a missing/malformed value (not a list of str) yields None (fall back to default),
    never raises. Empty list `[]` is a valid explicit declaration (returns [], not None)."""
    aim = _read_aim(ddd_dir)
    plugins = aim.get("plugins") if isinstance(aim, dict) else None
    if not isinstance(plugins, dict):
        return None, None

    def _clean(key: str) -> list[str] | None:
        val = plugins.get(key)
        if not isinstance(val, list):
            return None  # absent or malformed (e.g. a bare string) → default
        # drop non-strings/empties, then order-preserving dedup (a declared
        # ["read","read"] should emit "read" once — the emitted tools list is a SET
        # of grants, dup entries are noise).
        return list(dict.fromkeys(t for t in val if isinstance(t, str) and t))

    return _clean("tools"), _clean("allowed_tools")


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
        # Zone policy (XG decision A): the deliverables/ zone holds human-facing artifacts
        # (decks, diagrams), NOT agent-consumed context. A host-path in a deliverable does
        # not "break the package" the way it breaks recall corpus — so it is DOWNGRADED to a
        # warning on emit. A SECRET is NEVER downgraded (blocks in every zone), and an
        # EXTERNAL publish blocks on everything regardless of zone.
        if publish:
            blocking = findings  # everything blocks a publish (deliverables downgrade is emit-only)
        else:
            blocking = [
                f for f in findings
                if f.kind == "secret"
                or (f.kind == "host-path" and not _in_deliverables_zone(f.file))
            ]
        if blocking:
            detail = "; ".join(f"{f.kind}@{f.file}:{f.line} ({f.detail})" for f in blocking[:8])
            raise PackagingError(
                f"content-safety scan BLOCKED {target} ({len(blocking)} finding(s)): {detail}"
            )
        for f in findings:
            res.warnings.append(f"scan: {f.kind}@{f.file}:{f.line} ({f.detail})")
        # G1: binary deliverables can't be content-scanned (_is_scannable skips them) —
        # surface a LOUD warning so nobody assumes they were secret-scanned. Fail-loud, not silent.
        for p in sorted((out_dir / _DELIVERABLES_DIR).rglob("*")) if (out_dir / _DELIVERABLES_DIR).is_dir() else []:
            if p.is_file() and not _is_scannable(p):
                res.warnings.append(
                    f"unscanned binary deliverable (content-safety scan cannot read it): "
                    f"{p.relative_to(out_dir)}"
                )
        results.append(res)

    return results
