"""Tests for adversarial_commit_gate hook.

Verifies the OS-level enforcement gate that warns when committing Python
files without prior adversarial review in the session.
"""
import pytest
from unittest.mock import patch, MagicMock
from core.adversarial_commit_gate import create_adversarial_commit_gate


@pytest.fixture
def session_ctx():
    """Fresh session context without adversarial_done."""
    return {"work_dir": "/Users/test/project", "sdk_session_id": "test-123"}


@pytest.fixture
def gate(session_ctx):
    """Create gate with fresh context."""
    return create_adversarial_commit_gate(session_ctx)


class TestAdversarialCommitGate:
    """Core gate behavior tests."""

    @pytest.mark.asyncio
    async def test_non_bash_tool_passes(self, gate):
        """Non-Bash tools are always approved."""
        result = await gate({"tool_name": "Read", "tool_input": {}}, None, None)
        assert result["decision"] == "approve"

    @pytest.mark.asyncio
    async def test_non_commit_bash_passes(self, gate):
        """Bash commands that aren't git commit pass."""
        result = await gate(
            {"tool_name": "Bash", "tool_input": {"command": "git status"}},
            None, None,
        )
        assert result["decision"] == "approve"

    @pytest.mark.asyncio
    async def test_commit_with_adversarial_done_passes(self, gate, session_ctx):
        """If adversarial_done is set, commit passes."""
        session_ctx["adversarial_done"] = True
        result = await gate(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'test'"}},
            None, None,
        )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    @pytest.mark.asyncio
    async def test_commit_py_files_without_adversarial_warns(self, gate):
        """Committing .py files without adversarial triggers warning."""
        mock_result = MagicMock()
        mock_result.stdout = "core/todo_manager.py\ncore/hook_builder.py\n"

        with patch("core.adversarial_commit_gate.subprocess.run", return_value=mock_result):
            result = await gate(
                {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'fix'"}},
                None, None,
            )
        assert result["decision"] == "approve"  # Advisory, not blocking
        assert "ADVERSARIAL COMMIT GATE" in result.get("systemMessage", "")

    @pytest.mark.asyncio
    async def test_commit_only_md_files_passes(self, gate):
        """Docs-only commits (only .md files) pass without warning."""
        mock_result = MagicMock()
        mock_result.stdout = "README.md\ndocs/guide.md\n"

        with patch("core.adversarial_commit_gate.subprocess.run", return_value=mock_result):
            result = await gate(
                {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'docs'"}},
                None, None,
            )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    @pytest.mark.asyncio
    async def test_swarmws_workdir_exempt(self, session_ctx):
        """Commits in SwarmWS workspace are exempt."""
        session_ctx["work_dir"] = "/Users/gawan/.swarm-ai/SwarmWS"
        gate = create_adversarial_commit_gate(session_ctx)

        mock_result = MagicMock()
        mock_result.stdout = "scripts/tool.py\n"

        with patch("core.adversarial_commit_gate.subprocess.run", return_value=mock_result):
            result = await gate(
                {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'ws'"}},
                None, None,
            )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    @pytest.mark.asyncio
    async def test_subprocess_failure_passes(self, gate):
        """If git diff --cached fails, gate lets commit through."""
        with patch("core.adversarial_commit_gate.subprocess.run", side_effect=OSError("nope")):
            result = await gate(
                {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
                None, None,
            )
        assert result["decision"] == "approve"
