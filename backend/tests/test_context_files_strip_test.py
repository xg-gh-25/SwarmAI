"""The shipped context files pass the strip test (SOUL P7, AGENT R30#3).

backend/context/*.md is injected into every user's system prompt. Remove this
user, this machine and this repo: every sentence must still be true and useful.
SwarmAI's own development conventions live in the repo's AGENTS.md instead, and
the release checklist carries a strip-test review of backend/context.
"""
import inspect
import json
import re
from pathlib import Path

from core import context_directory_loader
from core.context_directory_loader import ContextDirectoryLoader
from core.ingestion import writer

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CTX = BACKEND / "context"
SKILLS = BACKEND / "skills"
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯＀-￯]")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_agent_md_carries_no_swarmai_dev_conventions():
    text = _read(CTX / "AGENT.md")
    banned = ["Commit direct to `main`", "SwarmAI convention", "bootout", "rsync", ":8000",
              "zsh -lic", "STEERING #7", "persists on tab switch", "CJK matching",
              "pgrep", "old binary"]
    assert [b for b in banned if b in text] == []


def test_agent_md_keeps_the_generic_rules():
    text = _read(CTX / "AGENT.md")
    for rnum in ("R6.", "R12.", "R13.", "R16.", "R18.", "R30."):
        assert f"**{rnum}" in text, rnum
    assert "THESIS.md" in text and "if present" in text
    assert "upstream" in text  # R30#3: a product truth outside SwarmAI's checkout


def _catalog_names() -> set[str]:
    catalog = json.loads(_read(REPO / "desktop" / "resources" / "mcp-catalog.json"))
    names = set()
    for item in catalog:
        names.add(item["id"].lower())
        names.add(item["name"].lower())
    return names


def test_swarmai_md_mcp_examples_come_from_the_shipped_catalog():
    text = _read(CTX / "SWARMAI.md")
    for word in ("Sentral", "Highspot", "Outlook"):
        assert word not in text, word
    bullet = re.search(r"^- \*\*MCPs\*\*.*?(?=^- \*\*|\Z)", text, re.MULTILINE | re.DOTALL)
    assert bullet, "SWARMAI.md has no MCPs bullet"
    examples = re.search(r"\(([^)]*)\)", bullet.group(0))
    assert examples, "the MCPs bullet names no examples"
    named = [part.strip() for part in examples.group(1).split("·")]
    named = [n for n in named if n and n not in ("…", "...")]
    assert named, "the MCPs bullet names no examples"
    catalog = _catalog_names()
    assert [n for n in named if n.lower() not in catalog] == []


def test_no_shipped_context_file_says_jobs_run_via_launchd():
    near = re.compile(r"\bjobs?\b.{0,60}launchd|launchd.{0,60}\bjobs?\b", re.IGNORECASE | re.DOTALL)
    hits = [p.name for p in CTX.glob("*.md") if near.search(_read(p))]
    assert hits == []
    assert "KeepAlive" not in _read(CTX / "SOUL.md")


def test_self_md_principle_count_matches_soul():
    soul_count = len(re.findall(r"^### P\d+", _read(CTX / "SOUL.md"), re.MULTILINE))
    counts = [int(n) for n in re.findall(r"\b(\d+) (?:cognitive )?principles\b", _read(CTX / "SELF.md"))]
    assert counts, "SELF.md names no principle count"
    assert set(counts) == {soul_count}


def test_identity_md_has_no_instance_birth_date():
    assert "2026-03-14" not in _read(CTX / "IDENTITY.md")


def _writer_prefixes() -> dict[str, str]:
    correction = writer._evolution_prefix({"type": "correction"}, None)
    other = writer._evolution_prefix({"type": "competence"}, None)
    labels = re.search(r'label = "(\w+)" if prefix == "(\w)" else "(\w+)"', inspect.getsource(writer))
    assert labels, "writer.py no longer labels EVOLUTION entries the way this test reads"
    assert labels.group(2) == correction
    return {correction: labels.group(1), other: labels.group(3)}


def test_evolution_md_matches_what_the_writer_writes():
    text = _read(CTX / "EVOLUTION.md")
    assert "30 days" not in text and "Archive by usage" not in text
    fmt = re.search(r"^## Entry Format.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert fmt, "EVOLUTION.md has no Entry Format section"
    declared = dict(re.findall(r"`([A-Z])` \((\w+)\)", fmt.group(0)))
    assert declared == _writer_prefixes()


def test_timeline_template_is_english():
    assert not CJK.search(_read(CTX / "TIMELINE.md"))


def test_system_appendix_notice_is_english_and_names_the_heading():
    notice = context_directory_loader.SYSTEM_APPENDIX_NOTICE
    assert not CJK.search(notice)
    assert "## System appendix (auto-extracted)" in notice


def test_example_and_glossary_files_are_not_shipped_context(tmp_path):
    assert not (CTX / "USER.example.md").exists()
    assert not (CTX / "CONTEXT.md").exists()
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "CONTEXT.md").write_text("glossary", encoding="utf-8")
    (templates / "SECURITY-BASELINE.md").write_text("baseline", encoding="utf-8")
    context_dir = tmp_path / ".context"
    ContextDirectoryLoader(context_dir=context_dir, templates_dir=templates).ensure_directory()
    assert (context_dir / "SECURITY-BASELINE.md").is_file()
    assert not (context_dir / "CONTEXT.md").exists()


def test_skill_builder_reads_its_own_glossary():
    skill = SKILLS / "s_skill-builder"
    glossary = skill / "GLOSSARY.md"
    assert glossary.is_file()
    text = _read(glossary)
    assert "no other mode in production" not in text
    assert not re.search(r"jobs use launchd", text)
    for name in ("INSTRUCTIONS.md", "CHECKLIST.md"):
        doc = _read(skill / name)
        assert "{SKILL_DIR}/GLOSSARY.md" in doc, name
        assert "CONTEXT.md" not in doc, name


def test_skills_and_experts_carry_no_instance_examples():
    concurrency = _read(BACKEND / "experts" / "code" / "concurrency.md")
    checklist = re.search(r"^### 1\. Shared Resource Enumeration.*?(?=^### )", concurrency,
                          re.MULTILINE | re.DOTALL).group(0)
    assert "18321" not in checklist and "max_tabs" not in checklist
    loops = _read(SKILLS / "s_loops-health" / "SKILL.md")
    assert "use health-check" not in loops
    assert "USER.example" not in _read(SKILLS / "s_loops-health" / "scripts" / "loops_health_check.py")
    distribute = _read(SKILLS / "s_ddd-distribute" / "SKILL.md")
    assert "internal package manager (e.g. AIM)" in distribute
    description = re.search(r'^description: "(.*)"$', distribute, re.MULTILINE).group(1)
    assert description.index("Open-Plugins") < description.index("AIM")
    assert "aws-outlook" not in _read(SKILLS / "s_job-manager" / "INSTRUCTIONS.md")
    assert "amazon.com" not in _read(SKILLS / "s_radar-todo" / "SKILL.md")


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\b.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"AGENTS.md has no '## {heading}' section"
    return match.group(0)


def test_repo_agents_md_carries_the_moved_conventions_and_the_release_review():
    text = _read(REPO / "AGENTS.md")
    conventions = _section(text, "SwarmAI development conventions")
    for needle in ("main", "bootout", "rsync", "8000", "zsh -lic", "persists on tab switch",
                   "old binary", "CJK"):
        assert needle in conventions, needle
    release = _section(text, "Release checklist")
    assert "git diff" in release and "backend/context" in release
    assert "strip test" in release
    assert "does not block" in release
