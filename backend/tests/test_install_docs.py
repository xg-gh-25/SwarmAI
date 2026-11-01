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
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
QUICK_START = ROOT / "QUICK_START.md"
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
DOCS = [QUICK_START, README, README_ZH]


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
    assert "AWS" in _text(README_ZH) and "账号" in _text(README_ZH)


def test_windows_installer_is_the_nsis_setup_exe():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    legs = {i["name"]: i for i in wf["jobs"]["build"]["strategy"]["matrix"]["include"]}
    assert legs["windows-x64"]["bundles"] == "nsis"
    for doc in DOCS:
        assert "-setup.exe" in _text(doc), doc.name


def test_macos_open_anyway():
    qs = _text(QUICK_START)
    assert "Privacy & Security" in qs and "Open Anyway" in qs
    assert "Open Anyway" in _text(README)


def test_linux_appimage_section():
    qs = _text(QUICK_START)
    assert re.search(r"^### Linux", qs, re.M)
    assert "AppImage" in qs and "chmod +x" in qs
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
