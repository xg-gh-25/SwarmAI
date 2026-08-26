"""Tests for scripts/security_scan.py — the code-security boundary gate.

Methodology: these tests drive the REAL security_scan module against REAL bandit
and detect-secrets over throwaway tmp fixtures (no mocking of the tools — the whole
point of the gate is the tools' actual behavior). Each test constructs a tiny code
tree + a baseline, then asserts the scanner's exit decision.

Key invariants proven here (the ones Gate-1 flagged as make-or-break):
  * INV1  clean tree (all findings baselined) → exit 0  (not a no-op: INV2 flips it)
  * INV2  a NEW bandit HIGH finding → exit 1 + reports file  — INCLUDING the
          BLOCKER-1 case: a 2ND same-type finding (2nd md5) in an ALREADY-baselined
          file. `bandit -b` silently suppresses this (verified exit 0); our
          finding-level diff MUST catch it. This is the test that proves we did
          NOT just delegate to `bandit -b`.
  * INV3  a NEW secret → exit 1
  * INV4  a baselined finding whose LINE MOVED (unrelated edit above it) → exit 0
          (fingerprint excludes line_number → refactor-robust)
  * INV5  the scanner never issues a `git config` write (core.hooksPath untouched —
          git-defender preserved). Static + behavioral check.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_PY = REPO_ROOT / "scripts" / "security_scan.py"

pytestmark = pytest.mark.skipif(
    not SCAN_PY.exists(), reason="security_scan.py not built yet (RED phase)"
)


def _run_scan(tmp: Path, *extra: str) -> subprocess.CompletedProcess:
    """Run the scanner rooted at tmp. Returns CompletedProcess (rc + stdout+stderr)."""
    return subprocess.run(
        [sys.executable, str(SCAN_PY), "--root", str(tmp), *extra],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _make_baseline(tmp: Path) -> None:
    """Generate both baselines for the current state of tmp (the --update-baseline path)."""
    subprocess.run(
        [sys.executable, str(SCAN_PY), "--root", str(tmp), "--update-baseline"],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )


@pytest.fixture()
def scan_tree(tmp_path: Path) -> Path:
    """A minimal scannable tree with a `backend/` dir (scanner scopes to backend/)."""
    (tmp_path / "backend").mkdir()
    return tmp_path


# ── INV1: clean tree (everything baselined) → exit 0 ────────────────────────────
def test_inv1_clean_baselined_tree_exits_zero(scan_tree: Path):
    f = scan_tree / "backend" / "mod.py"
    f.write_text("import hashlib\nx = hashlib.md5(b'a').hexdigest()\n")
    _make_baseline(scan_tree)
    r = _run_scan(scan_tree)
    assert r.returncode == 0, f"clean baselined tree should pass. stdout={r.stdout}\nstderr={r.stderr}"


# ── INV2: NEW bandit HIGH → exit 1 (incl. the BLOCKER-1 2nd-same-type case) ─────
def test_inv2_new_high_finding_blocks(scan_tree: Path):
    f = scan_tree / "backend" / "mod.py"
    f.write_text("import hashlib\nx = hashlib.md5(b'a').hexdigest()\n")
    _make_baseline(scan_tree)
    # add a NEW, different-type HIGH: shell=True on a variable
    f.write_text(
        "import hashlib, subprocess\n"
        "x = hashlib.md5(b'a').hexdigest()\n"
        "subprocess.call('ls ' + x, shell=True)\n"
    )
    r = _run_scan(scan_tree)
    assert r.returncode == 1, f"a new HIGH finding must block. stdout={r.stdout}"
    assert "mod.py" in r.stdout


def test_inv2b_second_same_type_in_baselined_file_blocks(scan_tree: Path):
    """BLOCKER-1 regression: `bandit -b` suppresses a 2nd md5 in an already-baselined
    file (verified exit 0). Our finding-level diff MUST catch it. If this test passes
    only because we delegated to `bandit -b`, it would go GREEN wrongly → this is the
    canary that we do our own diff."""
    f = scan_tree / "backend" / "mod.py"
    f.write_text("import hashlib\nx = hashlib.md5(b'a').hexdigest()\n")
    _make_baseline(scan_tree)
    # add a SECOND md5 — SAME test-type (B324), SAME file. bandit -b misses this.
    f.write_text(
        "import hashlib\n"
        "x = hashlib.md5(b'a').hexdigest()\n"
        "y = hashlib.md5(b'THIS_IS_NEW').hexdigest()\n"
    )
    r = _run_scan(scan_tree)
    assert r.returncode == 1, (
        "a 2nd same-type HIGH in a baselined file MUST block (BLOCKER-1). "
        f"exit={r.returncode} — if 0, we regressed to bandit -b delegation. stdout={r.stdout}"
    )


def test_inv2c_identical_text_finding_collision_blocks(scan_tree: Path):
    """CRITICAL-1 (adversarial-found false-green): two DISTINCT findings with IDENTICAL
    flagged-line text (same md5 statement in two functions). A content-only fingerprint
    collides them → baselining one silently absorbs the other → a real NEW HIGH passes
    green. The occurrence-ordinal in _fingerprint must give the 2nd occurrence a distinct
    fingerprint so adding it is caught. (INV2b used different var names and missed this.)"""
    f = scan_tree / "backend" / "mod.py"
    f.write_text(
        "import hashlib\n"
        "def a(data):\n"
        "    return hashlib.md5(data).hexdigest()\n"
    )
    _make_baseline(scan_tree)
    # add a SECOND function with the byte-for-byte IDENTICAL flagged line
    f.write_text(
        "import hashlib\n"
        "def a(data):\n"
        "    return hashlib.md5(data).hexdigest()\n"
        "def evil(data):\n"
        "    return hashlib.md5(data).hexdigest()\n"
    )
    r = _run_scan(scan_tree)
    assert r.returncode == 1, (
        "a 2nd finding with IDENTICAL flagged-line text MUST block (CRITICAL-1 collision). "
        f"exit={r.returncode} — if 0, fingerprints collided and a real vuln slipped through. "
        f"stdout={r.stdout}"
    )


# ── INV3: NEW secret → exit 1 ───────────────────────────────────────────────────
def test_inv3_new_secret_blocks(scan_tree: Path):
    f = scan_tree / "backend" / "conf.py"
    f.write_text("SAFE = 'nothing here'\n")
    _make_baseline(scan_tree)
    f.write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    r = _run_scan(scan_tree)
    assert r.returncode == 1, f"a new hardcoded secret must block. stdout={r.stdout}"


# ── INV4: baselined finding whose line MOVED → exit 0 (move-robust) ─────────────
def test_inv4_moved_baselined_finding_still_passes(scan_tree: Path):
    f = scan_tree / "backend" / "mod.py"
    f.write_text("import hashlib\nx = hashlib.md5(b'a').hexdigest()\n")
    _make_baseline(scan_tree)
    # insert an UNRELATED line ABOVE the baselined md5 — its line number shifts.
    f.write_text(
        "import hashlib\n"
        "UNRELATED = 42  # new line above\n"
        "x = hashlib.md5(b'a').hexdigest()\n"
    )
    r = _run_scan(scan_tree)
    assert r.returncode == 0, (
        "a baselined finding that only MOVED must not re-block (fingerprint must "
        f"exclude line_number). exit={r.returncode} stdout={r.stdout}"
    )


# ── INV6: missing scan target fails CLOSED (exit 2), never silent-pass ──────────
def test_inv6_missing_scan_target_fails_closed(tmp_path: Path):
    """LOW-1 (Gate-2): if the scan subdir (backend/) is absent, the scanner must
    fail CLOSED (exit 2), NOT early-return a silent PASS — otherwise a rename of the
    source tree would silently disable the whole gate."""
    # tmp_path has NO backend/ subdir
    r = _run_scan(tmp_path)
    assert r.returncode == 2, (
        f"missing scan target must fail closed (exit 2), got {r.returncode}. "
        f"stdout={r.stdout} stderr={r.stderr}"
    )


# ── INV5: scanner never writes git config (core.hooksPath / git-defender safe) ──
def test_inv5_scanner_never_writes_git_config():
    """Static guard: the scanner must never INVOKE a git-config write. git-defender
    owns core.hooksPath; a stray write would disable Amazon's own secret scanner.

    We check for an actual invocation signature, not the phrase in prose (the module
    docstring legitimately explains the constraint). A git-config write shows up as
    the token 'config' inside a subprocess arg list that also references 'git', or a
    literal 'hooksPath' write. We assert neither invocation form is present."""
    import ast

    tree = ast.parse(SCAN_PY.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        # The real threat is an INVOCATION: a subprocess arg list that runs
        # `git config ...`. A docstring mentioning the constraint is fine; a command
        # list is not. (Checking the invocation, not the phrase, is the point — a
        # naive substring grep flags the module docstring that explains the rule.)
        if isinstance(node, (ast.List, ast.Tuple)):
            elts = [e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if "git" in elts and "config" in elts:
                offenders.append("subprocess arg list contains git + config")
        # also catch a single-string shell command "git config ..."
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "git config" in node.value and "\n" not in node.value:  # a command, not prose
                offenders.append(f"single-line git-config command: {node.value[:50]!r}")
    assert not offenders, f"scanner must not mutate git config (git-defender owns hooksPath): {offenders}"


# ── INV7: private/internal skills are excluded from scan → never baselined ──────
def test_inv7_private_skill_paths_never_enter_baseline(scan_tree: Path):
    """C041-family leak fix (run_f1fe156b): private skills (s_cmhk-*, _shared) are
    .gitignored (local-only). If the scanner walked them, their private paths would
    be baked into the git-tracked baseline's findings[].file → a private-skill-name
    LEAK on the public repo. This asserts a HIGH finding inside a private-skill dir
    does NOT appear in the generated baseline (excluded at scan-time, so the fix
    survives --update-baseline — not a hand-edit that regenerates back)."""
    priv = scan_tree / "backend" / "skills" / "s_cmhk-weekly-report"
    priv.mkdir(parents=True)
    # a real HIGH×HIGH bandit finding (md5) inside the private skill
    (priv / "leak.py").write_text(
        "import hashlib\nx = hashlib.md5(b'secret').hexdigest()\n"
    )
    # and a public finding so the baseline is non-empty (proves scan ran)
    (scan_tree / "backend" / "pub.py").write_text(
        "import hashlib\ny = hashlib.md5(b'a').hexdigest()\n"
    )
    _make_baseline(scan_tree)
    bandit_bl = (scan_tree / "bandit-baseline.json").read_text()
    secrets_bl = (scan_tree / ".secrets.baseline").read_text()
    assert "s_cmhk" not in bandit_bl, "private skill path leaked into bandit baseline"
    assert "s_cmhk" not in secrets_bl, "private skill path leaked into secrets baseline"
    # sanity: the public finding IS baselined (scan genuinely ran, not empty-pass)
    assert "pub.py" in bandit_bl, "public finding missing — scan didn't cover backend/"


def test_inv7b_shared_dir_excluded(scan_tree: Path):
    """The _shared helper dir (also .gitignored) is likewise excluded."""
    shared = scan_tree / "backend" / "skills" / "_shared"
    shared.mkdir(parents=True)
    (shared / "util.py").write_text(
        "import hashlib\nz = hashlib.md5(b'x').hexdigest()\n"
    )
    (scan_tree / "backend" / "pub.py").write_text(
        "import hashlib\ny = hashlib.md5(b'a').hexdigest()\n"
    )
    _make_baseline(scan_tree)
    assert "_shared" not in (scan_tree / "bandit-baseline.json").read_text(), \
        "_shared path leaked into bandit baseline"


def test_private_skill_scope_consistent_across_all_sources():
    """P8 (Gate-2 meta-review): all THREE finding sources must honor the SAME
    private-skill scope, or a source that scans a .gitignored dir leaks its private
    path into that source's git-tracked baseline (C041). The scope lives in two
    syntaxes — BANDIT_EXCLUDES (comma-list, also used by _cors_fingerprints._is_excluded)
    and SECRETS_EXCLUDE_RE (regex) — with nothing binding them, so a future private-skill
    glob added to one could silently miss the other. This test is that binding: every
    private-skill family must be covered by BOTH."""
    import re
    mod = _load_scan_module()
    bandit_globs = set(mod.BANDIT_EXCLUDES.split(","))
    # Each private-skill family → a concrete on-disk DIR NAME the scanner would walk.
    # s_cmhk-*/s_internal-* are prefix globs (a real dir is e.g. 's_cmhk-weekly'); _shared
    # is an exact dir name. The sample path is what _cors_fingerprints/detect-secrets sees.
    families = {"s_cmhk-": "s_cmhk-weekly", "s_internal-": "s_internal-crux", "_shared": "_shared"}
    for family, dirname in families.items():
        # BANDIT_EXCLUDES (also the CORS _is_excluded source): a glob 'fam*' or exact match
        covered_bandit = any(
            (g.endswith("*") and dirname.startswith(g[:-1])) or g == dirname
            for g in bandit_globs
        )
        assert covered_bandit, (
            f"{dirname} not covered by BANDIT_EXCLUDES (bandit + CORS scope): {bandit_globs}"
        )
        # SECRETS_EXCLUDE_RE (regex): a sample path under that dir must match
        sample = f"backend/skills/{dirname}/leak.py"
        assert re.search(mod.SECRETS_EXCLUDE_RE, sample), (
            f"{dirname} not covered by SECRETS_EXCLUDE_RE={mod.SECRETS_EXCLUDE_RE!r}"
        )


# ---------------------------------------------------------------------------
# RP50 fail-open regression (run_a278e1ca): the gate MUST fail CLOSED when a
# scanner tool CRASHES while still emitting valid JSON. bandit rc>=2 (config /
# internal error) and detect-secrets rc!=0 both produce parseable JSON with empty
# results — read as "clean" unless returncode is checked. RP50's own ledger traces
# to a security_scan.py fail-open incident; these force the recovery path (RP28:
# a recovery branch needs a test that MAKES it execute, not just compiles).
#
# These import the module + monkeypatch subprocess.run (the end-to-end _run_scan
# tests above can't stably construct a rc=2-with-valid-JSON crash from real tools).
# ---------------------------------------------------------------------------
import importlib.util as _ilu


def _load_scan_module():
    spec = _ilu.spec_from_file_location("_secscan_under_test", SCAN_PY)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_bandit_crash_with_valid_json_fails_closed(tmp_path, monkeypatch):
    """Finding A: bandit rc=2 (internal/config error) + valid JSON + empty results
    must FAIL CLOSED (_die → SystemExit), not read as clean. rc in (0,1) = success;
    rc>=2 = crash."""
    mod = _load_scan_module()
    (tmp_path / "backend").mkdir()
    valid_empty = '{"results": [], "metrics": {"_totals": {"loc": 10}, "backend/a.py": {"loc": 10}}, "errors": []}'
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _FakeProc(2, valid_empty, "internal error"))
    with pytest.raises(SystemExit) as ei:
        mod._run_bandit(tmp_path)
    assert ei.value.code == 2, "bandit rc>=2 must fail closed with exit 2"


def test_bandit_success_rc_still_parses(tmp_path, monkeypatch):
    """Guard the fix's boundary: rc=0 and rc=1 are BOTH success (must NOT _die)."""
    mod = _load_scan_module()
    (tmp_path / "backend").mkdir()
    valid = '{"results": [], "metrics": {"_totals": {"loc": 10}, "backend/a.py": {"loc": 10}}, "errors": []}'
    for rc in (0, 1):
        monkeypatch.setattr(mod.subprocess, "run",
                            lambda *a, **k: _FakeProc(rc, valid))
        assert mod._run_bandit(tmp_path) == [], f"rc={rc} is success, must not die"


def test_detect_secrets_crash_with_valid_json_fails_closed(tmp_path, monkeypatch):
    """Finding A (ds half): detect-secrets rc!=0 + valid JSON must fail closed."""
    mod = _load_scan_module()
    (tmp_path / "backend").mkdir()
    # NOTE: detect-secrets JSON has NO filelist/files-scanned field (verified: keys are
    # version/plugins_used/filters_used/results/generated_at only) — so files-scanned>0
    # is unassertable; the rc check + bandit's loc>0 guard over the same subtree is the
    # coverage floor. This fixture mirrors the REAL shape (no invented field).
    valid = ('{"version": "' + mod.EXPECTED_DETECT_SECRETS_VERSION +
             '", "plugins_used": [{"name": "Base64HighEntropyString"}], "results": {}}')
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _FakeProc(2, valid, "boom"))
    with pytest.raises(SystemExit) as ei:
        mod._secret_fingerprints(tmp_path)
    assert ei.value.code == 2, "detect-secrets rc!=0 must fail closed"


# ═══════════════════════════════════════════════════════════════════════════════
# A4 WILDCARD-CORS regression gate (SECURITY-BASELINE.md A4 — the review-only rule
# earning a machine gate). The scanner grows a 3rd finding source (AST-based, no
# bandit HIGH check exists for CORS). Tests mirror INV1/INV2: a NEW wildcard blocks,
# a baselined wildcard passes, a computed (variable) allow_origins is NOT flagged.
# ═══════════════════════════════════════════════════════════════════════════════
_CORS_WILDCARD_APP = (
    "from fastapi.middleware.cors import CORSMiddleware\n"
    "app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True)\n"
)
_CORS_SAFE_APP = (
    "from fastapi.middleware.cors import CORSMiddleware\n"
    "cors_origins = ['https://example.com']\n"
    "app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True)\n"
)


def test_cors_new_wildcard_blocks(scan_tree: Path):
    """AC1: a NEWLY-introduced literal `allow_origins=['*']` must BLOCK (exit 1).
    bandit has no HIGH CORS check → without this gate the wildcard ships silently."""
    f = scan_tree / "backend" / "server.py"
    f.write_text(_CORS_SAFE_APP)          # clean starting state
    _make_baseline(scan_tree)
    f.write_text(_CORS_WILDCARD_APP)      # regression: wildcard introduced
    r = _run_scan(scan_tree)
    assert r.returncode == 1, (
        f"a newly-introduced wildcard CORS origin must block. exit={r.returncode} "
        f"stdout={r.stdout} stderr={r.stderr}"
    )
    assert "server.py" in r.stdout and "cors" in r.stdout.lower(), (
        f"BLOCK output must name the CORS finding + file. stdout={r.stdout}"
    )


def test_cors_variable_origins_not_flagged(scan_tree: Path):
    """AC1 (no-false-positive half): the REAL backend/main.py shape —
    `allow_origins=cors_origins` (a Name node, computed at runtime) — must NOT be
    flagged. A static gate can only prove a LITERAL wildcard; the computed case is
    the reviewer's job (F004: a gate that false-flags the safe real config gets
    disabled)."""
    f = scan_tree / "backend" / "server.py"
    f.write_text(_CORS_SAFE_APP)
    r = _run_scan(scan_tree)   # no baseline needed — nothing should be found
    assert r.returncode == 0, (
        f"a computed allow_origins=variable must NOT be flagged (only literal '*'). "
        f"exit={r.returncode} stdout={r.stdout}"
    )


def test_cors_baselined_wildcard_passes(scan_tree: Path):
    """AC3: a PRE-EXISTING (baselined) wildcard is absorbed and does NOT re-block,
    but a SECOND, distinct wildcard site introduced afterwards DOES block — the
    finding-level diff, same as bandit/secrets."""
    f = scan_tree / "backend" / "server.py"
    f.write_text(_CORS_WILDCARD_APP)
    _make_baseline(scan_tree)             # wildcard is now baselined
    assert _run_scan(scan_tree).returncode == 0, "baselined wildcard must not re-block"
    # add a SECOND wildcard site in a different file → NEW finding, must block
    (scan_tree / "backend" / "other.py").write_text(_CORS_WILDCARD_APP)
    r = _run_scan(scan_tree)
    assert r.returncode == 1, (
        f"a 2nd, non-baselined wildcard site must block (finding-level diff). "
        f"exit={r.returncode} stdout={r.stdout}"
    )
    assert "other.py" in r.stdout, f"the NEW site must be named. stdout={r.stdout}"


def test_cors_regex_catchall_blocks(scan_tree: Path):
    """AC1 (Gate-1 caveat #1): `allow_origin_regex='.*'` is a wildcard too — a
    catch-all regex reflects every Origin. The list-only matcher would MISS it;
    the gate must flag known catch-all regex patterns."""
    f = scan_tree / "backend" / "server.py"
    f.write_text(_CORS_SAFE_APP)
    _make_baseline(scan_tree)
    f.write_text(
        "from fastapi.middleware.cors import CORSMiddleware\n"
        "app.add_middleware(CORSMiddleware, allow_origin_regex='.*', allow_credentials=True)\n"
    )
    r = _run_scan(scan_tree)
    assert r.returncode == 1, (
        f"a catch-all allow_origin_regex ('.*') must block. exit={r.returncode} "
        f"stdout={r.stdout}"
    )


def test_cors_tuple_wildcard_blocks(scan_tree: Path):
    """AC1 (Gate-1 rec #3): `allow_origins=('*',)` (a tuple) is just as exploitable
    as a list — the matcher must handle ast.Tuple, not only ast.List."""
    f = scan_tree / "backend" / "server.py"
    f.write_text(_CORS_SAFE_APP)
    _make_baseline(scan_tree)
    f.write_text(
        "from fastapi.middleware.cors import CORSMiddleware\n"
        "app.add_middleware(CORSMiddleware, allow_origins=('*',), allow_credentials=True)\n"
    )
    r = _run_scan(scan_tree)
    assert r.returncode == 1, (
        f"a tuple wildcard allow_origins=('*',) must block. exit={r.returncode} "
        f"stdout={r.stdout}"
    )


def test_cors_set_wildcard_blocks(scan_tree: Path):
    """AC1 (Gate-2 adversarial finding): `allow_origins={'*'}` (a SET literal) is a
    functional Starlette wildcard — CORSMiddleware tests `"*" in allow_origins`, and a
    set supports `in`. The matcher must handle ast.Set, not only List/Tuple, or a
    credentialed wildcard ships green."""
    f = scan_tree / "backend" / "server.py"
    f.write_text(_CORS_SAFE_APP)
    _make_baseline(scan_tree)
    f.write_text(
        "from fastapi.middleware.cors import CORSMiddleware\n"
        "app.add_middleware(CORSMiddleware, allow_origins={'*'}, allow_credentials=True)\n"
    )
    r = _run_scan(scan_tree)
    assert r.returncode == 1, (
        f"a set-literal wildcard allow_origins={{'*'}} must block. exit={r.returncode} "
        f"stdout={r.stdout}"
    )


def test_cors_private_internal_skill_not_baselined(scan_tree: Path):
    """C041-family leak (Gate-2 adversarial finding): s_internal-* skills are
    .gitignored (present on disk, absent from git). If the CORS walk scanned them, a
    wildcard inside one would bake its PRIVATE path into the git-tracked cors-baseline.json
    → a private Amazon-internal skill-name LEAK on the public repo. This asserts a
    wildcard inside s_internal-* does NOT enter the baseline (excluded at scan-time),
    while a public one DOES (scan genuinely ran)."""
    priv = scan_tree / "backend" / "skills" / "s_internal-crux-cr"
    priv.mkdir(parents=True)
    (priv / "dev.py").write_text(
        "from fastapi.middleware.cors import CORSMiddleware\n"
        "app.add_middleware(CORSMiddleware, allow_origins=['*'])\n"
    )
    (scan_tree / "backend" / "pub.py").write_text(_CORS_WILDCARD_APP)
    _make_baseline(scan_tree)
    cors_bl = (scan_tree / "cors-baseline.json").read_text()
    assert "s_internal" not in cors_bl, "private s_internal-* path leaked into cors baseline"
    assert "pub.py" in cors_bl, "public wildcard missing — CORS scan didn't cover backend/"


def test_cors_unparseable_file_does_not_silence_tree(scan_tree: Path):
    """AC2 (fail-closed): a syntactically-broken .py must NOT make the CORS walk
    silently clean the whole tree. A real wildcard in a SIBLING valid file must
    still block. (The broken file is skipped-with-stderr; bandit is the syntax gate.)"""
    (scan_tree / "backend" / "broken.py").write_text("def (:\n  this is not python\n")
    (scan_tree / "backend" / "server.py").write_text(_CORS_WILDCARD_APP)
    # no baseline → the wildcard in server.py is a new finding
    r = _run_scan(scan_tree)
    assert r.returncode == 1, (
        f"an unparseable sibling must not silence a real wildcard finding. "
        f"exit={r.returncode} stdout={r.stdout} stderr={r.stderr}"
    )


def test_cors_baseline_file_is_written(scan_tree: Path):
    """AC3: --update-baseline writes a cors baseline file alongside the other two,
    so the CORS diff has a persistent, git-trackable baseline (empty = clean is the
    desirable steady state; any new wildcard then blocks)."""
    (scan_tree / "backend" / "server.py").write_text(_CORS_SAFE_APP)
    _make_baseline(scan_tree)
    # a cors baseline must exist after --update-baseline (name asserted via the module)
    mod = _load_scan_module()
    cors_bl = scan_tree / mod.CORS_BASELINE
    assert cors_bl.exists(), f"{mod.CORS_BASELINE} must be written by --update-baseline"
