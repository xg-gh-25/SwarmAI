"""Shipped skills, seed content and scripts carry no one person's or one company's data,
and the project-manager skill deletes a project through the product's recoverable path.

Every user's agent reads these files (skills, the seeded SwarmAI brain) or runs them
(skill scripts), so an example naming the owner's topics, colleagues or one company's
internal tools is wrong for everyone else, and a delete recipe that only works on macOS
loses data elsewhere.
"""
from __future__ import annotations

import importlib.util
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
SKILLS = BACKEND / "skills"
POLLINATE = SKILLS / "s_pollinate"
PROJECT_MANAGER = SKILLS / "s_project-manager" / "SKILL.md"
CJK = re.compile(r"[一-鿿]")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^### {re.escape(heading)}\b.*?(?=^### |^## |\Z)", text,
                      re.MULTILINE | re.DOTALL)
    assert match, heading
    return match.group(0)


# ── 1. deleting a project ────────────────────────────────────────────────────

def test_project_delete_goes_through_the_projects_api_with_no_permanent_fallback():
    delete = _section(_read(PROJECT_MANAGER), "Delete a Project")
    code = "\n".join(re.findall(r"```bash\n(.*?)```", delete, re.DOTALL))
    # ~/.Trash exists only on macOS: elsewhere `mv` renamed the project to ~/.Trash, and a
    # failed mv fell back to an unrecoverable rm -rf.
    assert "~/.Trash" not in delete
    assert "rm -rf" not in code and not re.search(r"^\s*mv\b", code, re.MULTILINE)
    assert "-X DELETE" in code and "/api/projects/$ID" in code
    assert "Projects/.trash/" in delete


def test_the_projects_api_delete_route_exists():
    """The recipe's route: DELETE /api/projects/{id} → swarm_workspace_manager.delete_project,
    which moves the folder to Projects/.trash/ (never rmtree)."""
    routes = _read(BACKEND / "routers" / "projects.py")
    assert re.search(r'@router\.delete\("/\{project_id\}"', routes)
    assert 'prefix="/projects"' in routes
    manager = _read(BACKEND / "core" / "swarm_workspace_manager.py")
    body = manager[manager.index("async def delete_project("):]
    body = body[:body.index("\n    async def ", 1)]
    assert '".trash"' in body and "move_tree(" in body and "shutil.rmtree(" not in body


# ── 2. no one company's internal tools in project-manager ────────────────────

_INTERNAL_TOOLS = re.compile(
    r"(?i:\b(?:brazil|crux|midway|gcraidlcpreset)\b)|code\.amazon\.com|git\.amazon\.com")


def test_project_manager_names_no_internal_tools():
    text = _read(PROJECT_MANAGER)
    hits = [m.group(0) for m in _INTERNAL_TOOLS.finditer(text)]
    assert hits == []


def test_project_manager_keeps_the_internal_capability_and_points_to_host_skills():
    text = _read(PROJECT_MANAGER)
    assert "kind: internal" in text
    assert "<internal-repo>" in text
    assert "host-provided `s_internal-*`" in text


# ── 3. s_pollinate ───────────────────────────────────────────────────────────

_POLLINATE_FILES = [
    "brand/identity.yaml", "channels/bilibili.yaml", "channels/youtube.yaml",
    "references/platform-specs.md", "INSTRUCTIONS.md", "scripts/generate_shorts.py",
    "scripts/check_rpv.py", "tracks/track-a-video.md", "scripts/format_recommend.py",
    "scripts/publish_dashboard.py",
]


@pytest.mark.parametrize("rel", _POLLINATE_FILES)
def test_pollinate_examples_are_neutral(rel):
    text = _read(POLLINATE / rel)
    assert not re.search(r"(?i)aidlc", text), rel           # the owner's own content topic
    assert not re.search(r"\b(?:Rob|Bo)\b|\brob\b", text), rel  # colleagues' names
    assert "pollinate-v2-launch" not in text and "LPM" not in text, rel


def test_pollinate_usage_examples_use_a_topic_placeholder():
    for rel in ("scripts/generate_shorts.py", "scripts/check_rpv.py", "scripts/publish_dashboard.py"):
        assert "content/<topic>/" in _read(POLLINATE / rel), rel


def test_format_recommend_labels_are_english_and_name_no_one():
    fr = _load(POLLINATE / "scripts" / "format_recommend.py")
    for table in (fr.AUDIENCES, fr.OUTCOMES, fr.CONTEXTS):
        for key, value in table.items():
            assert not CJK.search(value["label"]), key
    signals = fr.AUDIENCES["leadership"]["signals"]
    assert "rob" not in signals and "bo" not in signals


def test_format_recommend_no_longer_reads_leadership_into_common_words():
    """Signals match by substring: the names "rob"/"bo" made "problem", "robot", "about"
    and "both" read as a leadership audience."""
    fr = _load(POLLINATE / "scripts" / "format_recommend.py")
    text = "a robot talk about both problems for the developer community"
    assert fr.match_signals(text, fr.AUDIENCES) == ["developer_community"]
    assert "leadership" in fr.match_signals("给老板看", fr.AUDIENCES)


@pytest.fixture
def content_dir(tmp_path):
    d = tmp_path / "topic"
    (d / "deliver" / "xiaohongshu").mkdir(parents=True)
    (d / "deliver" / "github").mkdir(parents=True)
    for name in ("poster_3x4.png", "caption.txt", "qr_xhs.png"):
        (d / "deliver" / "xiaohongshu" / name).write_bytes(b"x")
    (d / "deliver" / "github" / "readme.md").write_text("x", encoding="utf-8")
    return d


def test_publish_dashboard_is_english_by_default(content_dir):
    pd = _load(POLLINATE / "scripts" / "publish_dashboard.py")
    assets = pd.scan_deliver(str(content_dir))
    md = pd.generate_markdown(str(content_dir), assets, {})
    assert not CJK.search(md), md
    assert "1. AirDrop `poster_3x4.png` to your phone" in md   # the main image, not the QR
    assert "| Post caption | Copy |" in md
    html = pd.generate_html(str(content_dir), assets, {})
    assert '<html lang="en">' in html
    body = html[html.index("<body>"):html.index("<script>")]
    assert not CJK.search(body)


def test_publish_dashboard_renders_chinese_on_request(content_dir):
    pd = _load(POLLINATE / "scripts" / "publish_dashboard.py")
    assets = pd.scan_deliver(str(content_dir), "zh")
    md = pd.generate_markdown(str(content_dir), assets, {}, "zh")
    assert "| 渠道 | 内容 | 格式 | 大小 | 文件 |" in md
    assert "1. AirDrop `poster_3x4.png` 到手机" in md
    assert '<html lang="zh-CN">' in pd.generate_html(str(content_dir), assets, {}, "zh")


def _opener_block() -> str:
    script = _read(POLLINATE / "templates" / "html-deck" / "shared" / "export-pdf.sh")
    start = script.index("# Open the PDF automatically")
    end = script.index("\nfi\n", start) + len("\nfi\n")
    return script[start:end]


def _fake(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.mark.skipif(os.name == "nt", reason="the exporter is a bash script")
@pytest.mark.parametrize("system, expected", [("Linux", "xdg-open"), ("Darwin", "open")])
def test_export_pdf_uses_open_only_on_macos(tmp_path, system, expected):
    """On Debian/Ubuntu `open` is openvt; the exporter must pick xdg-open there."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "called"
    _fake(bin_dir, "uname", f"echo {system}")
    _fake(bin_dir, "open", f'echo open >> "{log}"')
    _fake(bin_dir, "xdg-open", f'echo xdg-open >> "{log}"')
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "OUTPUT_PDF": str(tmp_path / "x.pdf")}
    subprocess.run(["bash", "-c", _opener_block()], env=env, check=True, timeout=30)
    assert log.read_text().split() == [expected]


# ── 4. the seeded SwarmAI brain describes every platform ────────────────────

def test_seed_tech_md_describes_the_backend_per_platform():
    text = _read(BACKEND / "templates" / "swarmai-seed" / "TECH.md")
    assert "launchd daemon (24/7)" not in text
    arch = text[text.index("## Architecture"):]
    arch = arch[:arch.index("```")]
    assert "macOS" in arch and "launchd" in arch
    assert "Windows and Linux a child process of the app" in arch
    assert "(AIDLC)" in text  # a public methodology name, kept


# ── 5. no instance demo page in the installer ───────────────────────────────

def test_brain_hub_demo_page_is_not_shipped():
    assert not (REPO / "desktop" / "public" / "brain-hub-demo.html").exists()
