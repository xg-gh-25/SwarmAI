#!/usr/bin/env python3
"""Code-security boundary gate — SSOT scanner (Phase 1: Python SAST + secrets).

WHAT THIS IS
    A decoupled, detect-only security gate that runs OUTSIDE the agent/model. It
    is invoked by two callers that share this ONE code path (SSOT):
      (A) CI  — `.github/workflows/ci.yml` runs it server-side on push/PR (hard gate).
      (B) local/pipeline wrapper — `scripts/security_scan.sh` for shift-left.

    It NEVER modifies code (no autofix) and NEVER mutates git configuration
    (`core.hooksPath` is owned by Amazon git-defender — repointing it would DISABLE
    git-defender, a security regression). It is not a git hook.

WHY FINDING-LEVEL DIFF (not `bandit -b`)
    `bandit -b baseline.json` suppresses per-FILE + per-TEST-TYPE, not per-issue:
    a SECOND `md5()` added to a file that already has one baselined md5 is SILENTLY
    SUPPRESSED (empirically verified: exit 0). That is a false-green security gate —
    worse than none. So we parse the FULL bandit JSON ourselves and diff at the
    individual-finding level, fingerprinting on (test_id, relpath, normalized-code)
    — deliberately EXCLUDING line_number so a refactor that only moves a baselined
    finding does not re-trigger the gate.

SEVERITY POLICY (data-driven — see run_4b007e00 EVALUATE)
    Only bandit HIGH-severity × HIGH-confidence findings gate. MEDIUM/LOW are a
    false-positive wall on this repo (325 MEDIUM dominated by B608 internal-SQL
    interpolation) — blocking on them would get the gate disabled (F004). Secrets:
    any NEW detect-secrets finding gates.

EXIT CODES
    0  no NEW findings (all current findings are in the baselines) — PASS
    1  at least one NEW high-severity finding or NEW secret — BLOCK
    2  usage / infrastructure error (tool missing, version drift, bad args)

USAGE
    security_scan.py                      # scan repo (root inferred from this file)
    security_scan.py --root <dir>         # scan a specific tree (tests use this)
    security_scan.py --update-baseline    # regenerate both baselines from current state

Baselines (git-tracked, at repo root) — BOTH use our OWN {"fingerprints": [...]}
schema (NOT detect-secrets' native baseline format), written/read only by this
script. Regenerate via `--update-baseline`; never hand-edit or overwrite with the
native `detect-secrets scan` output.
    bandit-baseline.json   — {"fingerprints": [...], "findings": {...}, "policy": {...}}
    .secrets.baseline      — {"fingerprints": [...], "findings": {...}} (our schema)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# Repo root = parent of this script's dir (scripts/ lives at repo root). Anchored to
# __file__, NEVER cwd — the CI job runs from repo root but bandit's own -b path would
# otherwise resolve relative to cwd (Gate-1 BLOCKER-3).
REPO_ROOT = Path(__file__).resolve().parents[1]

# The scan scope. detect-secrets over the FULL repo hangs >2min on Projects/
# (workspace transcripts/artifacts); bandit over backend/ is ~seconds. Scope both
# to the Python source tree. `backend` is relative to the scan root.
SCAN_SUBDIR = "backend"
# Exclusions passed to bandit (-x) — comma-separated, relative to root.
# Private/internal skills (.gitignored: s_cmhk-*, _shared) MUST be excluded here:
# they exist ONLY on the maintainer's machine, so scanning them bakes their private
# paths into the git-tracked baseline's findings[].file field → a private-skill-name
# LEAK on the public repo (C041 family, run_f1fe156b). Excluding at scan-time (not
# hand-editing the JSON) means the path is NEVER baselined and the fix survives
# `--update-baseline`. Keep in sync with .gitignore's private-skill patterns.
BANDIT_EXCLUDES = "tests,.venv,node_modules,s_cmhk-*,_shared"
# detect-secrets `--exclude-files` regex counterpart (same private-skill scope).
SECRETS_EXCLUDE_RE = r"\.venv/|node_modules/|/s_cmhk-[^/]*/|/_shared/"

# The single severity/confidence policy — used identically for baseline generation
# AND compare, so they can never drift (Gate-1 MUST-FIX-5).
BANDIT_SEVERITY = "high"
BANDIT_CONFIDENCE = "high"

BANDIT_BASELINE = "bandit-baseline.json"
SECRETS_BASELINE = ".secrets.baseline"

# Pin: detect-secrets embeds plugins+version in its baseline; a version mismatch
# fails OPEN (silent under-detection). Assert the runtime matches the pin.
EXPECTED_DETECT_SECRETS_VERSION = "1.5.0"


# ────────────────────────────── helpers ──────────────────────────────
def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"security_scan: ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _flagged_line_content(code: str, flagged_line: int | None) -> str:
    """Extract the flagged line's SOURCE CONTENT from bandit's `code` block.

    bandit's `code` field prefixes each source line with its 1-based line number:
        "1 import hashlib\n2 x = hashlib.md5(b'a').hexdigest()\n"
    We split ONLY on the documented "<line_number> " prefix, using the KNOWN
    flagged_line number (not a digit heuristic — a source line that legitimately
    starts with digits must not be mistaken for a prefix; HIGH-4). We return the
    content of the flagged line specifically (move-robust: excludes surrounding
    context + the numeric prefix)."""
    if flagged_line is not None:
        want = f"{flagged_line} "
        for raw in code.splitlines():
            if raw.startswith(want):
                return raw[len(want):].strip()
    # Fallback (flagged_line unknown/absent): strip a leading "<int> " from each line
    # and join by ascending line number — deterministic, still prefix-free. Used only
    # when bandit omits line_number, which is rare.
    stripped: dict[int, str] = {}
    for raw in code.splitlines():
        head, _, rest = raw.partition(" ")
        if head.isdigit():
            stripped[int(head)] = rest.strip()
    return "\n".join(stripped[k] for k in sorted(stripped)) if stripped else code.strip()


def _fingerprint(test_id: str, relpath: str, content: str, occurrence: int) -> str:
    """Stable per-finding fingerprint.

    Keyed on (test_id, relpath, flagged-line-content, OCCURRENCE-ORDINAL). It
    deliberately EXCLUDES the absolute line_number so a finding that merely MOVES
    (unrelated code inserted above it) keeps its fingerprint (INV4 / refactor-robust).

    The `occurrence` ordinal is the fix for the fingerprint-COLLISION false-green
    (CRITICAL-1, run_4b007e00): two DISTINCT findings with identical flagged-line
    text (e.g. the same `hashlib.md5(data)` statement in two functions) would
    otherwise collide to one fingerprint — baselining one would silently absorb the
    other, and a genuinely NEW HIGH finding would pass the gate green. By numbering
    same-(test_id,relpath,content) findings 0,1,2,… the Nth duplicate gets a distinct
    fingerprint, so adding a 2nd identical-text finding IS caught. Ordinal is assigned
    in ascending line order (see _bandit_fingerprints), which is stable under whole-
    block moves (all occurrences shift together, order preserved)."""
    key = f"{test_id}\x00{relpath}\x00{content}\x00#{occurrence}"
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()


def _run_bandit(root: Path) -> list[dict]:
    """Run bandit over root/backend, return the raw results list (HIGH×HIGH only)."""
    target = root / SCAN_SUBDIR
    if not target.exists():
        return []
    # -x takes GLOB patterns, NOT plain directory paths — absolute dir paths are
    # silently inert (MEDIUM-6, verified: backend/tests still scanned). Use */name/*.
    excludes = ",".join(f"*/{e}/*" for e in BANDIT_EXCLUDES.split(","))
    cmd = [
        sys.executable, "-m", "bandit", "-r", str(target),
        "-x", excludes,
        "--severity-level", BANDIT_SEVERITY,
        "--confidence-level", BANDIT_CONFIDENCE,
        "-f", "json", "-q",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # bandit exit: 0 = no issues, 1 = issues found. Both are SUCCESS for us (we parse
    # JSON). rc>=2 is a config/internal ERROR — bandit can still emit valid JSON with
    # empty results on rc=2, which would silently read as "clean" (RP50 fail-open, the
    # exact class this gate exists to catch). Check the returncode, not just empty
    # stdout — a crash with valid-but-empty JSON must fail CLOSED (_die).
    if proc.returncode not in (0, 1):
        _die(f"bandit crashed (rc={proc.returncode}, expected 0|1) — infra/config error, "
             f"fail closed: {proc.stderr[:300]}")
    if not proc.stdout.strip():
        _die(f"bandit produced no JSON (rc={proc.returncode}): {proc.stderr[:300]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        _die(f"bandit JSON parse failed: {e}; stderr={proc.stderr[:200]}")
    if data.get("errors"):
        # scan errors (syntax etc.) — surface but don't crash; they aren't findings
        print(f"security_scan: bandit reported {len(data['errors'])} scan error(s)",
              file=sys.stderr)
    # Fail CLOSED if the scan walked zero files (a path/scope bug must not read as
    # "clean" — HIGH-3). bandit's metrics dict has one entry per scanned file plus a
    # "_totals" summary; loc>0 proves real files were read.
    metrics = data.get("metrics", {})
    per_file = {k: v for k, v in metrics.items() if k != "_totals"}
    total_loc = metrics.get("_totals", {}).get("loc", 0)
    if not per_file or total_loc == 0:
        _die(f"bandit scanned 0 files under {target} — scope/path bug (fail closed). "
             f"files={len(per_file)} loc={total_loc}")
    return data.get("results", [])


def _bandit_fingerprints(root: Path) -> dict[str, dict]:
    """Map fingerprint → finding for every current HIGH×HIGH bandit result.

    Assigns an OCCURRENCE ORDINAL per (test_id, relpath, flagged-content) group so
    two distinct findings with identical text get distinct fingerprints (CRITICAL-1
    collision fix). Ordinal is assigned in ascending (line_number) order, which is
    stable under whole-block moves."""
    # Sort by (file, line) so the ordinal is deterministic + move-stable.
    results = sorted(_run_bandit(root),
                     key=lambda r: (r.get("filename", ""), r.get("line_number") or 0))
    seen: dict[tuple[str, str, str], int] = {}
    out: dict[str, dict] = {}
    for r in results:
        # normalize to root-relative regardless of how bandit printed the path
        try:
            relpath = str(Path(r["filename"]).resolve().relative_to(root.resolve()))
        except (ValueError, OSError):
            relpath = r["filename"]
        content = _flagged_line_content(r.get("code", ""), r.get("line_number"))
        group = (r["test_id"], relpath, content)
        occ = seen.get(group, 0)
        seen[group] = occ + 1
        fp = _fingerprint(r["test_id"], relpath, content, occ)
        out[fp] = {
            "test_id": r["test_id"],
            "file": relpath,
            "line": r.get("line_number"),
            "text": r.get("issue_text", "")[:100],
        }
    return out


def _check_detect_secrets_version() -> None:
    """Fail CLOSED unless the runtime detect-secrets version exactly matches the pin.

    CRITICAL-2 (run_4b007e00): `detect_secrets.VERSION` / `.__version__` are BOTH None
    on a real 1.5.0 install — the old getattr check was dead code that never fired,
    leaving the very version-drift fail-open it claimed to prevent. The real version
    lives in package metadata. An UNKNOWN version fails closed (never treated as OK)."""
    try:
        import importlib.metadata as _md
        ver = _md.version("detect-secrets")
    except _md.PackageNotFoundError:
        _die("detect-secrets not installed (pip install detect-secrets==1.5.0)")
    except Exception as e:  # pragma: no cover - metadata infra guard
        _die(f"cannot determine detect-secrets version (fail closed): {e}")
    if ver != EXPECTED_DETECT_SECRETS_VERSION:
        _die(f"detect-secrets version drift: runtime={ver}, "
             f"expected={EXPECTED_DETECT_SECRETS_VERSION} (plugin set must match the "
             f"baseline generator — under-detects silently otherwise). Pin it.")


def _secret_fingerprints(root: Path) -> dict[str, dict]:
    """Map fingerprint → secret for every current detect-secrets finding under backend/.
    Fingerprint = (type, relpath, hashed_secret) — excludes line_number (move-robust)."""
    target = root / SCAN_SUBDIR
    if not target.exists():
        return {}
    # Two detect-secrets footguns, both empirically verified (run_4b007e00):
    #   1. `scan <dir>` walks only git-TRACKED files → 0 results on an untracked tree.
    #      `--all-files` makes it scan the source tree regardless of git state.
    #   2. an ABSOLUTE path arg silently yields 0 findings; detect-secrets resolves +
    #      filters paths relative to CWD. So we run WITH cwd=root and pass the
    #      RELATIVE subdir. (This is the bug that made a real AWS key scan clean.)
    cmd = [
        sys.executable, "-m", "detect_secrets", "scan", "--all-files", SCAN_SUBDIR,
        "--exclude-files", SECRETS_EXCLUDE_RE,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root))
    # detect-secrets rc: 0 = success (found secrets are reported IN the JSON, not via rc);
    # rc!=0 is a crash/config error that can still emit valid-but-empty JSON → would read
    # as "clean" (RP50 fail-open). Check the returncode, not just empty stdout.
    if proc.returncode != 0:
        _die(f"detect-secrets crashed (rc={proc.returncode}, expected 0) — infra/config "
             f"error, fail closed: {proc.stderr[:300]}")
    if not proc.stdout.strip():
        _die(f"detect-secrets produced no JSON (rc={proc.returncode}): {proc.stderr[:300]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        _die(f"detect-secrets JSON parse failed: {e}")
    # Fail CLOSED on scan-coverage / plugin drift (HIGH-3): detect-secrets ALWAYS
    # emits valid JSON with {"results": {}} even when it walked 0 files (the exact
    # silent-empty class as the abs-path bug). Assert the embedded version matches
    # the pin (this is where the REAL version lives, per CRITICAL-2) and that the
    # plugin set is non-empty — otherwise a scope/config bug reads as "clean".
    emitted_ver = data.get("version")
    if emitted_ver != EXPECTED_DETECT_SECRETS_VERSION:
        _die(f"detect-secrets baseline/runtime version mismatch: scan emitted "
             f"{emitted_ver!r}, expected {EXPECTED_DETECT_SECRETS_VERSION} (fail closed)")
    if not data.get("plugins_used"):
        _die("detect-secrets ran with 0 plugins — misconfigured (fail closed)")
    out: dict[str, dict] = {}
    for fn, secrets in data.get("results", {}).items():
        try:
            relpath = str(Path(fn).resolve().relative_to(root.resolve()))
        except (ValueError, OSError):
            relpath = fn
        for s in secrets:
            key = f"{s.get('type')}\x00{relpath}\x00{s.get('hashed_secret')}"
            fp = hashlib.sha256(key.encode()).hexdigest()
            out[fp] = {"type": s.get("type"), "file": relpath, "line": s.get("line_number")}
    return out


def _load_bandit_baseline(root: Path) -> set[str]:
    p = root / BANDIT_BASELINE
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()).get("fingerprints", []))
    except (json.JSONDecodeError, OSError) as e:
        _die(f"cannot read {BANDIT_BASELINE}: {e}")


def _load_secret_baseline(root: Path) -> set[str]:
    p = root / SECRETS_BASELINE
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()).get("fingerprints", []))
    except (json.JSONDecodeError, OSError) as e:
        _die(f"cannot read {SECRETS_BASELINE}: {e}")


# ────────────────────────────── commands ──────────────────────────────
def update_baseline(root: Path) -> int:
    """Regenerate both baselines from the CURRENT state of the tree."""
    _check_detect_secrets_version()
    bandit_fps = _bandit_fingerprints(root)
    secret_fps = _secret_fingerprints(root)
    (root / BANDIT_BASELINE).write_text(json.dumps({
        "_comment": "Auto-generated by scripts/security_scan.py --update-baseline. "
                    "Finding-level fingerprints (test_id+relpath+normalized-code, "
                    "line-number EXCLUDED). Gate blocks only NEW HIGHxHIGH bandit "
                    "findings not listed here. Do not hand-edit.",
        "policy": {"severity": BANDIT_SEVERITY, "confidence": BANDIT_CONFIDENCE},
        "count": len(bandit_fps),
        "fingerprints": sorted(bandit_fps),
        "findings": {fp: bandit_fps[fp] for fp in sorted(bandit_fps)},
    }, indent=2) + "\n")
    (root / SECRETS_BASELINE).write_text(json.dumps({
        "_comment": "Auto-generated by scripts/security_scan.py --update-baseline. "
                    "Secret fingerprints (type+relpath+hashed_secret). Gate blocks "
                    "only NEW secrets not listed here. Do not hand-edit.",
        "detect_secrets_version": EXPECTED_DETECT_SECRETS_VERSION,
        "count": len(secret_fps),
        "fingerprints": sorted(secret_fps),
        "findings": {fp: secret_fps[fp] for fp in sorted(secret_fps)},
    }, indent=2) + "\n")
    print(f"security_scan: baselines updated — {len(bandit_fps)} bandit HIGH, "
          f"{len(secret_fps)} secret(s) absorbed.")
    return 0


def scan(root: Path) -> int:
    """Scan; return 1 if any NEW finding vs baseline, else 0."""
    _check_detect_secrets_version()
    bandit_fps = _bandit_fingerprints(root)
    secret_fps = _secret_fingerprints(root)
    bandit_base = _load_bandit_baseline(root)
    secret_base = _load_secret_baseline(root)

    new_bandit = {fp: v for fp, v in bandit_fps.items() if fp not in bandit_base}
    new_secrets = {fp: v for fp, v in secret_fps.items() if fp not in secret_base}

    if not new_bandit and not new_secrets:
        print("security_scan: PASS — no new HIGH findings or secrets.")
        return 0

    print("security_scan: BLOCK — new security findings (not in baseline):\n")
    for v in new_bandit.values():
        print(f"  [bandit {v['test_id']}] {v['file']}:{v['line']} — {v['text']}")
    for v in new_secrets.values():
        print(f"  [secret {v['type']}] {v['file']}:{v['line']}")
    print("\nFix the finding, or if it is a verified false-positive, regenerate the "
          "baseline: scripts/security_scan.py --update-baseline (and justify in the commit).")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Code-security boundary gate (bandit + detect-secrets).")
    ap.add_argument("--root", default=str(REPO_ROOT),
                    help="Tree to scan (default: repo root inferred from this file).")
    ap.add_argument("--update-baseline", action="store_true",
                    help="Regenerate both baselines from the current tree state.")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.exists():
        _die(f"--root does not exist: {root}")
    # Fail CLOSED if the scan target is missing (LOW-1, Gate-2): _run_bandit /
    # _secret_fingerprints early-return [] when SCAN_SUBDIR is absent, which would
    # bypass the coverage guards and read as "clean" (exit 0) — silently disabling
    # the entire gate if backend/ is ever renamed or SCAN_SUBDIR drifts.
    if not (root / SCAN_SUBDIR).is_dir():
        _die(f"scan target '{SCAN_SUBDIR}' not found under {root} — the gate cannot "
             f"verify anything (fail closed). If the source tree moved, update SCAN_SUBDIR.")
    if args.update_baseline:
        return update_baseline(root)
    return scan(root)


if __name__ == "__main__":
    sys.exit(main())
