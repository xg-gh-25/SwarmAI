"""Tests for the `schema` subcommand of artifact_cli (run_88b9f986).

The schema subcommand lets a caller fetch a stage's expected artifact template
WITHOUT triggering a failed publish. It reuses pipeline_validator.get_stage_schema
as the single source of truth and emits single-line JSON (parse-proof, like
publish --quiet).

Root: run_00e0e872 — the only way to see the expected deliver schema was to
publish bad data and read the rejection. cmd_schema removes that trial-and-error.
"""

import json
import sys
from pathlib import Path

import pytest

_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
import scripts.artifact_cli as cli  # noqa: E402


class TestSchemaSubcommand:
    def test_schema_emits_single_line_parseable_json(self, capsys):
        """schema --stage deliver prints ONE line of JSON the caller can json.load."""
        class _Args:
            stage = "deliver"

        cli.cmd_schema(_Args(), None)
        out = capsys.readouterr().out
        # single line (parse-proof): exactly one non-empty line, no indentation
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 1, f"expected single-line output, got {len(lines)} lines"
        parsed = json.loads(lines[0])  # must not raise
        assert parsed["stage"] == "deliver"

    def test_schema_returns_template_and_required(self, capsys):
        """The output carries the template + required fields from get_stage_schema."""
        class _Args:
            stage = "deliver"

        cli.cmd_schema(_Args(), None)
        parsed = json.loads(capsys.readouterr().out.strip())
        # deliver template includes the fields run_00e0e872 had to discover by trial
        tmpl = parsed.get("template", {})
        assert "adversarial_review" in tmpl
        assert "completion_audit" in tmpl
        assert isinstance(parsed.get("required", []), list)

    def test_schema_matches_get_stage_schema_source_of_truth(self, capsys):
        """cmd_schema must NOT duplicate schema — it reuses get_stage_schema verbatim."""
        from pipeline_validator import get_stage_schema

        class _Args:
            stage = "build"

        cli.cmd_schema(_Args(), None)
        parsed = json.loads(capsys.readouterr().out.strip())
        expected = get_stage_schema("build")
        assert parsed["required"] == expected["required"]
        assert parsed["template"] == expected["template"]

    def test_schema_unknown_stage_does_not_crash(self, capsys):
        """An unknown stage returns an empty-ish schema, not a traceback."""
        class _Args:
            stage = "not_a_real_stage"

        cli.cmd_schema(_Args(), None)  # must not raise
        parsed = json.loads(capsys.readouterr().out.strip())
        assert parsed["stage"] == "not_a_real_stage"
        assert parsed.get("required", []) == []


class TestTemplatePassesItsOwnValidator:
    """`schema --stage X` hands out a template the agent fills in. If that template
    is missing a field the publish validator REQUIRES, every run discovers the gap
    by rejection — publish → reject → add field → republish, per stage, per run.
    The deliver template lacked `adversarial_review.evidence`, `meta_review`,
    `convergence` and `ac_verification`, so every full/bugfix DELIVER was rejected
    on its first publish. Pin the invariant instead of the field list: the template
    itself, under the strictest profile, must validate clean."""

    # EVERY stage that has an artifact template (A4): the templates now carry valid
    # literals (no "A|B|C" unions) with the choice set in STAGE_ENUMS, so the
    # template is used VERBATIM — no test-side patching of placeholders.
    @pytest.mark.parametrize("stage", ["evaluate", "think", "plan", "build", "review",
                                       "test", "deliver", "goal_cycle"])
    def test_template_validates_clean_under_full_profile(self, stage, tmp_path):
        import copy

        from pipeline_validator import STAGE_TEMPLATES, validate_artifact_data

        data = copy.deepcopy(STAGE_TEMPLATES[stage])
        # goal_cycle exists only in the goal profile (strict, like full).
        profile = "goal" if stage == "goal_cycle" else "full"
        errors = validate_artifact_data(stage, data, profile=profile, repo_root=str(tmp_path))
        assert errors == [], f"{stage} template fails its own validator: {errors}"

    @pytest.mark.parametrize("stage", ["evaluate", "think", "plan", "build", "review",
                                       "test", "deliver", "goal_cycle", "reflect"])
    def test_record_example_validates_clean(self, stage):
        from pipeline_validator import STAGE_RECORD_TEMPLATES, validate_stage_record

        assert validate_stage_record(stage, STAGE_RECORD_TEMPLATES[stage]) == ([], [])

    def test_plan_template_exit_contract_is_valid_and_strict_ready(self):
        from pipeline_validator import STAGE_TEMPLATES, validate_exit_criteria

        assert validate_exit_criteria(STAGE_TEMPLATES["plan"]["exit_criteria"], required=True) == []

    def test_templates_carry_no_pipe_unions_and_enums_hold_the_choices(self):
        from pipeline_validator import STAGE_ENUMS, STAGE_TEMPLATES, get_stage_schema

        def _leaves(node, path=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield from _leaves(v, f"{path}.{k}" if path else k)
            elif isinstance(node, list):
                for v in node:
                    yield from _leaves(v, f"{path}[]")
            else:
                yield path, node

        enum_fields = {"recommendation", "scope", "verdict", "profile_tier", "severity",
                       "status", "kind", "work_type", "evidence_kind", "skeptic_verdict",
                       "constraint", "effort"}
        for stage, tmpl in STAGE_TEMPLATES.items():
            for path, val in _leaves(tmpl):
                if isinstance(val, str) and path.split(".")[-1].split("[")[0] in enum_fields:
                    assert "|" not in val, f"{stage}.{path} is a pipe union: {val!r}"
            for path, allowed in STAGE_ENUMS.get(stage, {}).items():
                assert allowed and get_stage_schema(stage)["enums"][path] == allowed
