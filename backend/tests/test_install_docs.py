"""The install guides tell a new user what the release really needs and ships (R1.6).

- Chat runs on Amazon Bedrock, so an AWS account is required; there is no Anthropic API key
  option any more.
- The Windows installer is the NSIS ``-setup.exe`` (release.yml builds ``bundles: nsis``),
  not an .msi.
- The macOS app is unsigned: the user opens it with System Settings > Privacy & Security >
  Open Anyway, not with ``xattr -cr``.
- Linux ships an AppImage: make it executable and run it.
- The installed app needs git (and Git Bash on Windows); building from source has its own
  list.
- docs/USER_GUIDE.md (linked from docs/README.md) tells the same story as QUICK_START.md: no
  Claude CLI / Node.js prerequisite for the installed app, Intel Macs supported, a Linux
  section, and the AWS account connected under the Settings tab's real name.
"""
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
QUICK_START = ROOT / "QUICK_START.md"
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
USER_GUIDE = ROOT / "docs" / "USER_GUIDE.md"
DOCS = [QUICK_START, README, README_ZH, USER_GUIDE]


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_no_anthropic_api_key_option():
    for doc in DOCS:
        assert not re.search(r"anthropic\s+api(\s+key)?", _text(doc), re.I), doc.name
        assert "ANTHROPIC_API_KEY" not in _text(doc), doc.name


def test_no_msi_and_no_xattr():
    for doc in DOCS:
        t = _text(doc)
        assert ".msi" not in t, doc.name
        assert "xattr -cr" not in t, doc.name


def test_aws_account_is_required():
    assert "AWS account" in _text(QUICK_START)
    assert "AWS account" in _text(README)
    assert "AWS account" in _text(USER_GUIDE)
    assert "AWS" in _text(README_ZH) and "账号" in _text(README_ZH)


def test_windows_installer_is_the_nsis_setup_exe():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    legs = {i["name"]: i for i in wf["jobs"]["build"]["strategy"]["matrix"]["include"]}
    assert legs["windows-x64"]["bundles"] == "nsis"
    for doc in DOCS:
        assert "-setup.exe" in _text(doc), doc.name


def test_macos_open_anyway():
    for doc in (QUICK_START, USER_GUIDE):
        t = _text(doc)
        assert "Privacy & Security" in t and "Open Anyway" in t, doc.name
    assert "Open Anyway" in _text(README)


def test_linux_appimage_section():
    for doc in (QUICK_START, USER_GUIDE):
        t = _text(doc)
        assert re.search(r"^### Linux", t, re.M), doc.name
        assert "AppImage" in t and "chmod +x" in t and "FUSE" in t, doc.name
    for doc in (README, README_ZH):
        assert "AppImage" in _text(doc), doc.name


def test_installed_app_prerequisites_are_git_and_git_bash():
    qs = _text(QUICK_START)
    m = re.search(r"^## System Requirements\n(.*?)^## ", qs, re.M | re.S)
    assert m, "QUICK_START.md has no System Requirements section"
    req = m.group(1)
    assert "git" in req.lower() and "Git Bash" in req
    # the build-from-source list stays separate and keeps its own tools
    src = re.search(r"^### Build from Source\n(.*?)^##", qs, re.M | re.S)
    assert src and "Node.js" in src.group(1) and "Rust" in src.group(1)
    for doc in (README, README_ZH):
        t = _text(doc)
        assert "Git Bash" in t and "Node.js" in t, doc.name


def _user_guide_prerequisites() -> str:
    m = re.search(r"^## Prerequisites\n(.*?)^## ", _text(USER_GUIDE), re.M | re.S)
    assert m, "docs/USER_GUIDE.md has no Prerequisites section"
    return m.group(1)


def test_user_guide_prerequisites_match_quick_start():
    """The installed app brings its own Claude CLI: no npm install, no Node.js to install."""
    ug = _text(USER_GUIDE)
    assert "@anthropic-ai/claude-code" not in ug
    req = _user_guide_prerequisites()
    table = [ln for ln in req.splitlines() if ln.startswith("|")]
    assert table, "the Prerequisites section has no table"
    assert not any("Node.js" in ln or "Claude Code CLI" in ln for ln in table), table
    assert "git" in req.lower() and "Git Bash" in req
    assert "Intel" in req and "Linux" in req
    src = re.search(r"^### Build from Source\n(.*?)^##", ug, re.M | re.S)
    assert src and "Node.js" in src.group(1) and "Rust" in src.group(1)


def test_user_guide_macos_covers_intel():
    ug = _text(USER_GUIDE)
    assert "Apple Silicon only" not in ug and "Intel Mac support is not available" not in ug
    mac = re.search(r"^### macOS\n(.*?)^### ", ug, re.M | re.S)
    assert mac and "Intel" in mac.group(1)


def test_docs_name_the_aws_account_tab_as_the_app_does():
    """The Settings tab is named by en.json's settings.awsAccount.title, not "SwarmAI Account"."""
    en = json.loads((ROOT / "desktop" / "src" / "i18n" / "locales" / "en.json").read_text("utf-8"))
    label = en["settings"]["awsAccount"]["title"]
    for doc in (QUICK_START, USER_GUIDE):
        t = _text(doc)
        assert "SwarmAI Account" not in t, doc.name
        assert label in t, doc.name
