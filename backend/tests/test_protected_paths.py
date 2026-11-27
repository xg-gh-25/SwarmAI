"""core.protected_paths: one protected-path list, one rule syntax, in every session's settings.

Method: build the real settings (``PromptBuilder._chat_cli_settings``) and the real lists;
nothing is mocked but the config object a PromptBuilder reads.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from core import protected_paths as pp


def _builder(sandbox: bool = False):
    from core.prompt_builder import PromptBuilder
    values = {"sandbox_enabled_default": sandbox}
    cfg = MagicMock()
    cfg.get = MagicMock(side_effect=lambda key, default=None: values.get(key, default))
    return PromptBuilder(config=cfg)


def _home_rule(rel: str) -> str:
    return "~/" + rel


def test_list_contents_cover_control_files_and_startup_hooks():
    from config import get_app_data_dir
    data = Path(get_app_data_dir())
    rules = pp.protected_write_rules()
    for want in (pp.rule_path(data / "runtime") + "/**", pp.rule_path(data / "daemon") + "/**",
                 pp.rule_path(data / "swarmai_backend.sh"), pp.rule_path(data / "dangerous_commands.json"),
                 _home_rule(".zshrc"), _home_rule(".bashrc"), _home_rule(".profile"),
                 _home_rule(".ssh/authorized_keys"), _home_rule(".gitconfig"),
                 _home_rule("Library/LaunchAgents") + "/**", _home_rule(".claude/settings.json")):
        assert want in rules, want
    assert any(r.endswith("/config.json") for r in rules)
    # known_hosts stays writable: ssh records a new host there on first connect
    assert not any("known_hosts" in r or r == "~/.ssh/**" for r in rules)


def test_runs_later_cover_job_definitions_skills_and_capabilities():
    from config import get_swarmws
    ws = Path(get_swarmws())
    rules = pp.runs_later_rules()
    assert pp.rule_path(ws / "Services" / "swarm-jobs") + "/**" in rules
    assert pp.rule_path(ws / ".claude") + "/**" in rules
    assert pp.rule_path(ws / "Projects") + "/*/4-capabilities/**" in rules


def test_credential_rules_cover_the_stores():
    rules = pp.credential_rules()
    for want in ("~/.aws/**", "~/.ssh/**", "~/.midway/**", "~/.config/gh/**", "~/.netrc"):
        assert want in rules, want
    assert any(r.endswith("secrets.json") for r in rules)


def test_rules_use_cli_path_syntax():
    assert pp.rule_path(Path.home() / ".swarm-ai" / "aws") == "~/.swarm-ai/aws"
    assert pp.rule_path(Path("/opt/swarm/secrets.json")) == "//opt/swarm/secrets.json"
    for rule in [*pp.credential_rules(), *pp.protected_write_rules(), *pp.runs_later_rules()]:
        assert rule.startswith(("~/", "//")), rule  # a bare /abs rule denies nothing


def test_deny_rules_shape_and_dedup():
    rules = pp.deny_rules(read=["~/.aws/**"], edit=["~/.aws/**", "~/.zshrc", "~/.zshrc"])
    assert rules == ["Read(~/.aws/**)", "Edit(~/.aws/**)", "Edit(~/.zshrc)"]


def test_every_session_kind_gets_protected_edit_denies():
    """Each session kind gets the protected list its watcher calls for: a non-owner
    channel session all of it; the owner's own session nothing with the sandbox off
    (the owner may edit their dotfiles), SwarmAI's control files only with it on."""
    from core.prompt_builder import restricted_session_policy
    full = {f"Edit({r})" for r in pp.protected_write_rules()}
    control = {f"Edit({r})" for r in pp.control_rules()}
    restricted = _builder()._chat_cli_settings("claude-sonnet-4-6", {"channel": "slack"},
                                               restricted=restricted_session_policy())
    deny = set(restricted["permissions"]["deny"])
    assert full <= deny and "Read(~/.aws/**)" in deny
    owner_off = _builder()._chat_cli_settings("claude-sonnet-4-6", None)
    assert "permissions" not in owner_off
    owner_on = _builder(sandbox=True)._chat_cli_settings("claude-sonnet-4-6", None)
    deny = set(owner_on["permissions"]["deny"])
    assert deny == control
    assert "Edit(~/.zshrc)" not in deny and not any("LaunchAgents" in r for r in deny)


def test_runs_later_cover_git_and_context():
    from config import get_swarmws
    ws = Path(get_swarmws())
    rules = pp.runs_later_rules()
    assert pp.rule_path(ws) + "/**/.git/**" in rules  # every repo's hooks and config
    assert pp.rule_path(ws / ".context") + "/**" in rules  # read into every later prompt
    assert all(r.startswith(pp.rule_path(ws)) for r in rules), rules  # workspace paths only
    assert not hasattr(pp, "_app_data_rules")
    from config import get_app_data_dir
    assert pp.rule_path(Path(get_app_data_dir()) / ".env") in pp.credential_rules()
