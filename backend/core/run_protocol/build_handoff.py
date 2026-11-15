"""build_handoff — the prompt for a BUILD done by a sub-session with a clean context.

Why this exists
---------------
Measured on landed runs, most of a run's price-weighted cost is the orchestrating
session re-reading its own history on every response (TECH: "Pipeline run cost is
dominated by the main session re-reading its own history"). BUILD is the longest stage.
With the app setting ``pipeline_clean_build`` set to ``true``, ``next`` at BUILD names
``pipeline-v1 build-handoff``: it writes one prompt as an audit blob of the run and
returns a short spawn prompt naming it. The orchestrator spawns one sub-agent with that
spawn prompt; the sub-agent builds and tests in the run's worktree and ends with the
validation report as its final message; the orchestrator checks the change and passes
the report to ``stage-complete``. Everything else (stage order, checks, the report's
binding to the tree the engine computes) is as without the setting.

The builder never changes the run: its prompt gives it the building rules, not the
orchestrator's BUILD card (whose stage-complete and child steps are the orchestrator's),
and ``run_owner_gate`` refuses every pipeline-v1 command outside the read-only ones
from a sub-agent whose spawn prompt starts with ``SPAWN_LINE`` (``builder_refusal``),
so text the builder reads cannot make it move the run.

Off by default (a missing, false or malformed value): ``next`` and the BUILD card are
exactly as before. Measured once (one small bugfix built twice from the same base: one
builder holding the orchestrator's real pre-BUILD history, one given only this prompt;
same model, same tests): the clean builder used about 12% less BUILD cost and a third
less wall time, a blind review found the same quality, and every later response of the
orchestrator carries about 40K fewer tokens. One sample does not outweigh the recorded
choice of one session switching roles, so the default stays off until paired runs on
more changes say otherwise; the method is to repeat that pair and compare billed
tokens, price-weighted cost, wall time and review findings.

Public: ``SETTING``, ``SPAWN_LINE``, ``enabled``, ``prompt_text``, ``write``,
``builder_refusal``.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

SETTING = "pipeline_clean_build"
#: the first line of every builder's spawn prompt: how the gate knows a builder
SPAWN_LINE = "BUILD-HANDOFF:"
#: pipeline-v1 commands a builder may run (they change nothing of the run)
BUILDER_COMMANDS = frozenset({"status", "events", "findings", "base-tree", "template"})

_RULES = """\
- Write and test the change your own way, in the worktree(s) above, with the repo's own
  commands. If a dependency is installed in editable form from the main checkout (an
  editable Python install, an npm/yarn link), code run in the worktree can load the main
  checkout's copy: start the tests from the worktree's own source directory and give
  that directory as `cwd` in your report.
- A command that fails the same way on the run's base is `pre_existing`: check it in the
  directory `"$SWARM_BACKEND_BIN" pipeline-v1 base-tree --project {project} --run-id {run_id}
  --repo <name>` prints (read-only). Optional `base_result` per row: its result there
  (`fail` there and `pass` here is a fail-to-pass test).
- Every result but `pass` needs a reason. Add `"risk": ["security"]` when the change
  touches authentication, permissions, input validation, credentials, network, file
  paths or shell commands. A judgment call (a deviation from the tasks, a choice they
  left open) goes in the optional `decisions` list as {{id, question, choice, reason}}.
- Report each command exactly as you ran it, with its `cwd`.
- You do not change the run: no `pipeline-v1` command other than {allowed} (the
  others are refused for you), no commit, no push. The orchestrator reviews your change
  and submits your report."""


def enabled(value: Any = ...) -> bool:
    """True only when the setting is the value ``true``. ``...``: read it."""
    if value is ...:
        try:
            from core.app_config_manager import AppConfigManager
            value = AppConfigManager.instance().get(SETTING, None)
        except Exception:  # noqa: BLE001 — an unreadable config keeps it off
            logger.debug("build_handoff: setting unreadable", exc_info=True)
            return False
    return value is True


def prompt_text(*, run_id: str, project: str, requirement: str, design: Any,
                worktrees: "dict[str, str]", report_template: Any) -> str:
    """The whole prompt a clean-context builder gets: nothing from the orchestrator's
    conversation, only what the run's own files hold, and the requirement whole."""
    tasks = design.get("tasks") if isinstance(design, dict) else None
    exits = design.get("exit_criteria") if isinstance(design, dict) else None
    where = "\n".join(f"- {name}: {path}" for name, path in sorted(worktrees.items())) \
        or "- (no worktree yet: say so in your final message and stop)"
    return "\n".join([
        f"# BUILD for pipeline run {run_id} (project {project})",
        "",
        "You build this run's change in a fresh context. You have no other conversation: "
        "everything you need is below or in the repository. Work only in the worktree(s) "
        "listed. Read the repository's AGENTS.md before you edit.",
        "",
        "## Worktrees (edit and test here)",
        where,
        "",
        "## Requirement (the person's words)",
        requirement or "",
        "",
        "## Tasks (DESIGN_SPEC)",
        json.dumps(tasks or [], ensure_ascii=False, indent=1),
        "",
        "## Exit criteria (DESIGN_SPEC)",
        json.dumps(exits or [], ensure_ascii=False, indent=1),
        "",
        "## The whole DESIGN_SPEC (for the decisions, properties and failure modes)",
        json.dumps(design or {}, ensure_ascii=False, indent=1),
        "",
        "## How to build",
        _RULES.format(project=project, run_id=run_id,
                      allowed=", ".join(f"`{c}`" for c in sorted(BUILDER_COMMANDS))),
        "",
        "## What you return",
        "Your final message is only the validation report JSON, one report per repo, "
        "nothing before or after it. Its shape:",
        json.dumps(report_template, ensure_ascii=False, indent=1),
        "",
    ])


def write(engine: Any) -> "dict[str, str]":
    """Write the prompt for *engine*'s run as an audit blob and return
    ``{"prompt_file", "spawn_prompt"}``."""
    from core.run_protocol import templates
    text = prompt_text(
        run_id=engine.run_id, project=str(engine.contract.get("project") or ""),
        requirement=str(engine.contract.get("requirement") or ""),
        design=engine._read_json(engine.run_dir / "artifacts" / "design_spec.json", None),
        worktrees=dict(engine.effective_repos()),
        report_template=templates.template("validation_report")["document"])
    digest = engine.store.put_blob(text.encode("utf-8"))
    path = str(engine.store.paths.blobs / digest)
    spawn = (f"{SPAWN_LINE} {engine.run_id}\n"
             f"Read {path} whole (every part of it) and do what it says. "
             "Your final message is only the validation report JSON it describes.")
    return {"prompt_file": path, "spawn_prompt": spawn}


def _subagent_transcript(input_data: Mapping[str, Any]) -> "str | None":
    """The calling sub-agent's own transcript: the host's field for it, or the session
    transcript's ``<stem>/subagents/agent-<agent_id>.jsonl`` (where the CLI writes it)."""
    agent_id = str(input_data.get("agent_id") or "")
    own = input_data.get("agent_transcript_path")
    if isinstance(own, str) and own:
        return own
    main = input_data.get("transcript_path")
    if not agent_id or not isinstance(main, str) or not main.endswith(".jsonl"):
        return None
    if f"{os.sep}subagents{os.sep}agent-" in main:
        return main
    return str(Path(main[:-len(".jsonl")]) / "subagents" / f"agent-{agent_id}.jsonl")


def builder_refusal(command: str, input_data: Mapping[str, Any]) -> "str | None":
    """Why a builder sub-agent may not run *command*, or None. Only a sub-agent call
    (``agent_id``) naming pipeline-v1 with a subcommand outside ``BUILDER_COMMANDS``
    reads its spawn prompt head; a builder's (first line ``SPAWN_LINE``) is refused, and
    so is any pipeline-v1 call of a builder the parser cannot read. Blocking: call it
    off the event loop."""
    if not input_data.get("agent_id"):
        return None
    from core import pipeline_command
    if not pipeline_command.mentions(command):
        return None
    found, _single = pipeline_command.calls(command)
    if found and all(c.get("tool") != "pipeline_v1" or (
            c.get("sub") in BUILDER_COMMANDS and not c.get("ambiguous")) for c in found):
        return None
    path = _subagent_transcript(input_data)
    if not path:
        return None
    from core.subagent_transcript import read_prompt_head
    if not read_prompt_head(path).lstrip().startswith(SPAWN_LINE):
        return None
    return ("a BUILD-HANDOFF builder does not change the run: of pipeline-v1 it may run only "
            f"{', '.join(sorted(BUILDER_COMMANDS))}. End with the validation report as your "
            "final message; the orchestrator submits it.")
