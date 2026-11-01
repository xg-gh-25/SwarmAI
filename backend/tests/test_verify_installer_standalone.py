"""scripts/verify_installer.sh runs on its own (release.yml verify job).

The verify job checks out only scripts/verify_installer.sh (a sparse checkout), so the
script must not read anything else from the repository: what it checks comes from the
installer and the runtime it installs. And its backend start (C8) must wait at least as long
as the app itself waits for a cold start (COLD_START_CEILING_SECS in lib.rs), or it fails a
build the app would start.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_installer.sh"
LIB_RS = ROOT / "desktop" / "src-tauri" / "src" / "lib.rs"
WF = ROOT / ".github" / "workflows" / "release.yml"

# Lines that may use $REPO_ROOT because what they read is in the sparse checkout.
# Only the script itself is checked out, so nothing is allowed.
REPO_ROOT_ALLOWED: set[str] = set()


def _code_lines() -> list[str]:
    """Script lines without full-line comments."""
    return [ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")]


def _section(check_id: str) -> str:
    """The script text of one check: from its `# Cn:` header to the next `# Cn:` header."""
    text = SCRIPT.read_text()
    m = re.search(rf"^# {check_id}:.*?(?=^# C\d+:)", text, re.M | re.S)
    assert m, f"no {check_id} section in verify_installer.sh"
    return m.group(0)


def test_no_repo_root_reads_beyond_the_allowlist():
    uses = {ln.strip() for ln in _code_lines() if "REPO_ROOT" in ln}
    assert uses <= REPO_ROOT_ALLOWED, f"reads from the repo checkout: {sorted(uses - REPO_ROOT_ALLOWED)}"


def test_no_paths_relative_to_the_script_location():
    # "$0" alone (the --help text is the script's own header) is fine; a directory derived
    # from it points into a checkout the verify job does not have.
    bad = [ln.strip() for ln in _code_lines()
           if re.search(r'dirname\s+"?\$0|BASH_SOURCE', ln)]
    assert not bad, bad


def test_c14_imports_the_helper_from_the_installed_runtime():
    c14 = _section("C14")
    assert "from scripts.skill_python_deps import bundled_imports" in c14
    assert "sys.path.insert" not in c14


def _cold_start_ceiling() -> int:
    m = re.search(r"const COLD_START_CEILING_SECS: u64 = (\d+);", LIB_RS.read_text())
    assert m, "COLD_START_CEILING_SECS not found in lib.rs"
    return int(m.group(1))


def test_c8_waits_at_least_the_app_cold_start_ceiling():
    m = re.search(r"^HEALTH_WAIT_SECS=(\d+)\b", SCRIPT.read_text(), re.M)
    assert m, "verify_installer.sh has no HEALTH_WAIT_SECS"
    assert int(m.group(1)) >= _cold_start_ceiling()
    c8 = _section("C8")
    # the C8 wait loop is bounded by HEALTH_WAIT_SECS, not by a fixed attempt count
    assert re.search(r"\$\(\(\s*SECONDS\s*\+\s*HEALTH_WAIT_SECS\s*\)\)", c8), c8
    assert not re.search(r"for _ in \$\(seq 1 \d+\)", c8), "C8 still waits a fixed number of tries"


def test_verify_job_sparse_checkout_lists_only_the_script():
    verify = yaml.safe_load(WF.read_text())["jobs"]["verify"]
    checkouts = [s for s in verify["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    assert len(checkouts) == 1
    paths = set(str(checkouts[0]["with"]["sparse-checkout"]).split())
    assert paths == {"scripts/verify_installer.sh"}
    assert checkouts[0]["with"].get("sparse-checkout-cone-mode") is False
