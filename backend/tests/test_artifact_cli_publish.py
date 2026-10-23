"""artifact_cli publish: a bare artifact (no run, no stage) from inline JSON, a file
or stdin, and its trust boundary.

What is tested: ``publish --data`` reads raw JSON, ``@FILE`` and ``@-``; an unsafe
project name is refused with the CLI's JSON error (exit 3) and writes nothing; the
retired run commands are not commands any more (argparse refuses them).
"""
import json

import pytest


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "Projects" / "TestProject").mkdir(parents=True)
    return tmp_path


class TestPublishDataFromFile:
    """run_b7620c6e #1: publish --data only accepted a raw JSON string. Add
    @FILE / @- (stdin) so large artifact payloads avoid shell-string gymnastics."""

    def _publish_args(self, data_arg):
        import argparse
        ns = argparse.Namespace(project="TestProject", type="changeset", data=data_arg,
                                producer="s_autonomous-pipeline", summary="t", topic="",
                                quiet=True)
        return ns

    def test_data_at_file_is_read(self, workspace, tmp_path, monkeypatch, capsys):
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        reg = ArtifactRegistry(workspace)
        payload = tmp_path / "p.json"
        payload.write_text(json.dumps({"branch": "x", "commits": ["abc1234"], "files_changed": ["f.py"]}))
        cli.cmd_publish(self._publish_args("@" + str(payload)), reg)
        out = capsys.readouterr().out
        assert "artifact_id" in out, f"@file publish should succeed: {out}"

    def test_raw_string_still_works(self, workspace, monkeypatch, capsys):
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        reg = ArtifactRegistry(workspace)
        raw = json.dumps({"branch": "x", "commits": ["abc1234"], "files_changed": ["f.py"]})
        cli.cmd_publish(self._publish_args(raw), reg)
        out = capsys.readouterr().out
        assert "artifact_id" in out, f"raw-string publish must still work: {out}"

    def test_data_at_stdin_is_read(self, workspace, monkeypatch, capsys):
        import io
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        monkeypatch.setattr("sys.stdin",
                            io.StringIO(json.dumps({"branch": "x", "commits": ["abc1234"], "files_changed": ["f.py"]})))
        reg = ArtifactRegistry(workspace)
        cli.cmd_publish(self._publish_args("@-"), reg)
        out = capsys.readouterr().out
        assert "artifact_id" in out, f"@- stdin publish should succeed: {out}"


def test_an_unsafe_project_is_refused_before_any_write(workspace, monkeypatch, capsys):
    import argparse
    import scripts.artifact_cli as cli
    from core.artifact_registry import ArtifactRegistry
    monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
    ns = argparse.Namespace(project="../../escape", type="changeset", data="{}",
                            producer="p", summary="t", topic="", quiet=True)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_publish(ns, ArtifactRegistry(workspace))
    assert exc.value.code == 3
    assert "error" in json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert not (workspace.parent / "escape").exists()


@pytest.mark.parametrize("command", ["run-create", "run-update", "stage-complete",
                                     "purge-garbage", "main-red"])
def test_retired_run_commands_are_not_commands(command, monkeypatch, capsys):
    import sys
    import scripts.artifact_cli as cli
    monkeypatch.setattr(sys, "argv", ["artifact_cli.py", command, "--project", "P"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2 and "invalid choice" in capsys.readouterr().err
