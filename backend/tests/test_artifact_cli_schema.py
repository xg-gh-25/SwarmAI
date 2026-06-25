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
