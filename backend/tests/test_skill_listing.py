"""Tests for core/skill_listing — the CLI skill-listing size measured at spawn.

What is tested
--------------
- The budget follows the CLI's formula (context window x 4 x 0.01) and honors
  SLASH_COMMAND_TOOL_CHAR_BUDGET.
- collect_entries sees projected workspace skills and the skills of ENABLED
  plugins only (disabled plugins are not listed by the CLI either).
- measure applies the per-skill cap, counts name-only skills by name, skips off
  skills, and reports over/under budget.
- The cap SwarmAI passes to the CLI equals the linter's description limit, and
  reaches the real --settings flag the SDK builds.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from core.skill_listing import (
    SKILL_DESC_CAP, ListingEntry, budget_chars, collect_entries, measure,
)

REPO = Path(__file__).resolve().parents[2]


def _skill(root: Path, name: str, desc: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {json.dumps(desc)}\n---\nbody\n")


def test_budget_follows_the_cli_formula():
    assert budget_chars(1_000_000, env={}) == 40_000
    assert budget_chars(200_000, env={}) == 8_000
    assert budget_chars(1_000_000, env={"SLASH_COMMAND_TOOL_CHAR_BUDGET": "1234"}) == 1234


def test_collects_workspace_and_enabled_plugin_skills(tmp_path):
    _skill(tmp_path / "ws" / ".claude" / "skills", "s_a", "Does a. TRIGGER: a.")
    cfg = tmp_path / "cfg"
    on, off = tmp_path / "p_on", tmp_path / "p_off"
    _skill(on / "skills", "x", "Plugin x. TRIGGER: x.")
    _skill(off / "skills", "y", "Plugin y. TRIGGER: y.")
    (cfg / "plugins").mkdir(parents=True)
    (cfg / "settings.json").write_text(json.dumps(
        {"enabledPlugins": {"on@m": True, "off@m": False}}))
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {
        "on@m": [{"installPath": str(on)}], "off@m": [{"installPath": str(off)}]}}))
    names = [e.name for e in collect_entries(tmp_path / "ws", env={"CLAUDE_CONFIG_DIR": str(cfg)})]
    assert names == ["s_a", "on:x"]


def test_missing_plugin_config_means_workspace_only(tmp_path):
    _skill(tmp_path / ".claude" / "skills", "s_a", "Does a. TRIGGER: a.")
    names = [e.name for e in collect_entries(tmp_path, env={"CLAUDE_CONFIG_DIR": str(tmp_path / "none")})]
    assert names == ["s_a"]


def test_measure_caps_descriptions_and_applies_overrides():
    long_desc = "z" * 1000
    entries = [ListingEntry("s_long", long_desc), ListingEntry("s_name", "short"),
               ListingEntry("s_off", "gone")]
    m = measure(entries, context_window=1_000_000, env={},
                overrides={"s_name": "name-only", "s_off": "off"})
    assert m["skills"] == 2
    assert m["long"] == 1
    assert m["chars"] == len(f"- s_long: {'z' * SKILL_DESC_CAP}") + 1 + len("- s_name") + 1
    assert m["over"] is False


def test_measure_reports_over_budget():
    entries = [ListingEntry(f"s_{i}", "d" * 200) for i in range(100)]
    assert measure(entries, context_window=200_000, env={})["over"] is True


def test_cap_equals_the_linter_limit():
    spec = importlib.util.spec_from_file_location("lint_skills", REPO / "scripts" / "lint_skills.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.DESCRIPTION_MAX == SKILL_DESC_CAP


def test_cap_reaches_the_cli_settings_flag():
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    opts = ClaudeAgentOptions(settings=json.dumps({"skillListingMaxDescChars": SKILL_DESC_CAP}))
    transport = SubprocessCLITransport(prompt="probe", options=opts)
    transport._cli_path = "/bin/true"
    cmd = transport._build_command()
    assert json.loads(cmd[cmd.index("--settings") + 1])["skillListingMaxDescChars"] == SKILL_DESC_CAP


def test_listing_settings_carry_the_cap():
    from core.skill_listing import listing_settings
    assert listing_settings() == {"skillListingMaxDescChars": SKILL_DESC_CAP}


def test_unknown_window_uses_the_cli_default():
    assert budget_chars(None, env={}) == 8_000


def test_older_single_install_plugin_shape_is_read(tmp_path):
    cfg = tmp_path / "cfg"
    plug = tmp_path / "plug"
    _skill(plug / "skills", "x", "Plugin x. TRIGGER: x.")
    (cfg / "plugins").mkdir(parents=True)
    (cfg / "settings.json").write_text(json.dumps({"enabledPlugins": {"p@m": True}}))
    (cfg / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"p@m": {"installPath": str(plug)}}}))
    names = [e.name for e in collect_entries(tmp_path / "ws", env={"CLAUDE_CONFIG_DIR": str(cfg)})]
    assert names == ["p:x"]


def test_near_budget_warns(tmp_path, caplog, monkeypatch):
    from core.skill_listing import log_listing
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-plugins"))
    monkeypatch.delenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", raising=False)
    root = tmp_path / ".claude" / "skills"
    for i in range(36):                       # 36 x 209 chars ~ 7.5K of an 8K budget
        _skill(root, f"s_{i:02d}", "d" * 200)
    with caplog.at_level("INFO", logger="core.skill_listing"):
        m = log_listing(tmp_path, context_window=200_000)
    assert m["over"] is False and m["chars"] >= 0.9 * m["budget"]
    assert any(r.levelname == "WARNING" and "near budget" in r.getMessage() for r in caplog.records)


def test_schedule_runs_in_the_background(tmp_path, monkeypatch):
    import asyncio
    import core.skill_listing as sl
    seen = []
    monkeypatch.setattr(sl, "log_listing", lambda cwd, **kw: seen.append(cwd))

    async def main():
        sl.schedule_log_listing(tmp_path, context_window=1_000_000)
        assert sl._PENDING                   # scheduled, not awaited
        await asyncio.gather(*list(sl._PENDING))

    asyncio.run(main())
    assert seen == [tmp_path] and not sl._PENDING


def test_lint_counts_when_to_use_with_the_description(tmp_path):
    spec = importlib.util.spec_from_file_location("lint_skills", REPO / "scripts" / "lint_skills.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = tmp_path / "s_x"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: x\ndescription: \"Does x. TRIGGER: x.\"\nwhen_to_use: \"" + "w" * 240 + "\"\n---\n")
    assert any("over 250" in e for e in mod.lint_skill(d / "SKILL.md"))
