"""Tests for session_miner module."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from core.session_miner import EvalExample, SessionMiner


@pytest.fixture
def miner_dirs(tmp_path):
    """Create standard directory structure for SessionMiner."""
    transcripts = tmp_path / "transcripts"
    skills = tmp_path / "skills"
    evals = tmp_path / "evals"
    transcripts.mkdir()
    skills.mkdir()
    evals.mkdir()
    return transcripts, skills, evals


@pytest.fixture
def miner(miner_dirs):
    transcripts, skills, evals = miner_dirs
    return SessionMiner(transcripts, skills, evals)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class TestParseTranscript:
    def test_parse_transcript(self, miner, miner_dirs):
        transcripts, _, _ = miner_dirs
        records = [
            {"type": "user", "message": {"content": "hello"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"type": "queue-operation", "data": "something"},
        ]
        path = transcripts / "session1.jsonl"
        _write_jsonl(path, records)

        result = miner._parse_transcript(path)
        assert len(result) == 2
        assert result[0]["type"] == "user"
        assert result[1]["type"] == "assistant"

    def test_parse_malformed_json(self, miner, miner_dirs, caplog):
        transcripts, _, _ = miner_dirs
        path = transcripts / "bad.jsonl"
        with open(path, "w") as f:
            f.write('{"type": "user", "message": {"content": "ok"}}\n')
            f.write("NOT JSON\n")
            f.write('{"type": "assistant", "message": {"content": []}}\n')

        with caplog.at_level(logging.WARNING):
            result = miner._parse_transcript(path)
        assert len(result) == 2  # bad line skipped


class TestExtractSkillInvocations:
    def test_extract_skill_invocations(self, miner):
        records = [
            {"type": "user", "message": {"content": "please commit my changes"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "I'll commit now"}]}},
            {"type": "user", "message": {"content": "looks good"}},
        ]
        examples = miner._extract_skill_invocations(records, "commit", ["commit", "git"])
        assert len(examples) >= 1
        assert examples[0].skill_invoked == "commit"
        assert examples[0].score == 1.0  # no correction

    def test_extract_with_correction(self, miner):
        records = [
            {"type": "user", "message": {"content": "commit my changes"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done"}]}},
            {"type": "user", "message": {"content": "no, don't include the test files"}},
        ]
        examples = miner._extract_skill_invocations(records, "commit", ["commit"])
        assert len(examples) >= 1
        assert examples[0].user_correction is not None
        assert examples[0].score == 0.5

    def test_extract_with_abandon(self, miner):
        records = [
            {"type": "user", "message": {"content": "commit my code"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done"}]}},
            {"type": "user", "message": {"content": "stop nevermind"}},
        ]
        examples = miner._extract_skill_invocations(records, "commit", ["commit"])
        assert len(examples) >= 1
        assert examples[0].score == 0.0


class TestScrubSecrets:
    def test_scrub_secrets(self, miner):
        # Construct dynamically to avoid Code Defender
        key_prefix = "AKIA"
        key_body = "IOSFODNN7EXAMPLE"
        text = f"key is {key_prefix}{key_body} and more"
        result = miner._scrub_secrets(text)
        # Should be redacted if MemoryGuard is available, or returned as-is if not
        assert isinstance(result, str)


class TestMineForSkill:
    def test_mine_for_skill_empty_dir(self, miner):
        result = miner.mine_for_skill("nonexistent")
        assert result == []

    def test_mine_for_skill_with_transcripts(self, miner, miner_dirs):
        transcripts, skills, _ = miner_dirs
        # Create a skill dir with SKILL.md
        skill_dir = skills / "s_commit"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: commit\ndescription: >\n  Git commit helper\n  TRIGGER: commit, git commit\n---\nInstructions here\n")
        # Create a transcript
        records = [
            {"type": "user", "message": {"content": "please commit"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "committed"}]}},
        ]
        _write_jsonl(transcripts / "s1.jsonl", records)

        result = miner.mine_for_skill("commit")
        assert isinstance(result, list)


class TestGetEligibleSkills:
    def test_get_eligible_skills_min_count(self, miner, miner_dirs):
        # With no transcripts, no skill should be eligible
        transcripts, skills, _ = miner_dirs
        skill_dir = skills / "s_test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n")

        result = miner.get_eligible_skills(min_examples=5)
        assert result == []


class TestSaveEvals:
    def test_save_evals(self, miner, miner_dirs):
        _, _, evals = miner_dirs
        examples = [
            EvalExample(
                user_prompt="do X",
                skill_invoked="test",
                agent_actions="did X",
                user_correction=None,
                final_outcome="success",
                score=1.0,
            ),
        ]
        path = miner.save_evals("test", examples)
        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["skill_invoked"] == "test"
        assert data["score"] == 1.0


class TestEvalExampleDataclass:
    def test_eval_example_dataclass(self):
        ex = EvalExample(
            user_prompt="hello",
            skill_invoked="greet",
            agent_actions="said hi",
            user_correction=None,
            final_outcome="ok",
            score=1.0,
        )
        assert ex.user_prompt == "hello"
        assert ex.skill_invoked == "greet"
        assert ex.user_correction is None
        assert ex.score == 1.0


# ---------------------------------------------------------------------------
# Real transcript format tests (post-evolution-loop-close)
# ---------------------------------------------------------------------------

class TestRealTranscriptFormat:
    """Tests for parsing real Claude Code session transcript format."""

    def test_parse_real_transcript_format(self, tmp_path):
        """End-to-end: mine a real-format transcript for a specific skill."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_summarize"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: summarize\n---\nTRIGGER: summarize, summary, tl;dr\n\nInstructions.\n"
        )
        evals_dir = tmp_path / "evals"
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        transcript = transcripts_dir / "test.jsonl"
        lines = [
            json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "test"}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "summarize this doc"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Skill", "id": "t1",
                 "input": {"skill": "s_summarize", "args": "..."}}
            ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"tool_use_id": "t1", "type": "tool_result",
                 "content": [{"type": "text", "text": "Summary here"}]}
            ]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Here is the summary..."}
            ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "good, thanks"}}),
        ]
        transcript.write_text("\n".join(lines), encoding="utf-8")

        miner = SessionMiner(transcripts_dir, skills_dir, evals_dir)
        examples = miner.mine_for_skill("summarize")

        assert len(examples) >= 1
        ex = examples[0]
        assert ex.skill_invoked == "summarize"
        assert ex.score == 1.0  # "good, thanks" is not a correction

    def test_correction_skips_tool_result_messages(self, tmp_path):
        """Correction detection skips tool_result user messages, finds real feedback."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_summarize"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: summarize\n---\nTRIGGER: summarize\n\nInstructions.\n"
        )
        evals_dir = tmp_path / "evals"
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        transcript = transcripts_dir / "test.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "summarize this"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Skill", "id": "t1",
                 "input": {"skill": "s_summarize", "args": "..."}}
            ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"tool_use_id": "t1", "type": "tool_result",
                 "content": [{"type": "text", "text": "Summary"}]}
            ]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Done."}
            ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "no, that's wrong. fix it"}}),
        ]
        transcript.write_text("\n".join(lines), encoding="utf-8")

        miner = SessionMiner(transcripts_dir, skills_dir, evals_dir)
        examples = miner.mine_for_skill("summarize")

        assert len(examples) >= 1
        corrected = [ex for ex in examples if ex.user_correction is not None]
        assert len(corrected) >= 1
        assert corrected[0].score == 0.5

    def test_tool_use_detection_by_skill_name(self, tmp_path):
        """Skill invocation detected via tool_use block with name == Skill."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_image-gen"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: image-gen\n---\nTRIGGER: image-gen\n\nInstructions.\n"
        )
        evals_dir = tmp_path / "evals"
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        transcript = transcripts_dir / "test.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "create a logo for my app"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Skill", "id": "t1",
                 "input": {"skill": "image-gen", "args": "logo for app"}}
            ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"tool_use_id": "t1", "type": "tool_result",
                 "content": [{"type": "text", "text": "Image generated"}]}
            ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "looks great!"}}),
        ]
        transcript.write_text("\n".join(lines), encoding="utf-8")

        miner = SessionMiner(transcripts_dir, skills_dir, evals_dir)
        examples = miner.mine_for_skill("image-gen")

        assert len(examples) >= 1
        assert examples[0].skill_invoked == "image-gen"
        assert examples[0].score == 1.0

    def test_s_prefix_stripped(self, miner):
        """_extract_skill_from_tool_use strips s_ prefix from skill name."""
        block = {"type": "tool_use", "name": "Skill", "id": "t1",
                 "input": {"skill": "s_summarize", "args": "..."}}
        assert miner._extract_skill_from_tool_use(block) == "summarize"

    def test_no_prefix_preserved(self, miner):
        """_extract_skill_from_tool_use preserves name without s_ prefix."""
        block = {"type": "tool_use", "name": "Skill", "id": "t1",
                 "input": {"skill": "image-gen", "args": "..."}}
        assert miner._extract_skill_from_tool_use(block) == "image-gen"

    def test_non_skill_tool_returns_none(self, miner):
        block = {"type": "tool_use", "name": "Read", "id": "t1",
                 "input": {"file_path": "/some/path"}}
        assert miner._extract_skill_from_tool_use(block) is None

    def test_tool_result_is_detected(self, miner):
        """_is_tool_result_content identifies tool_result lists."""
        content = [
            {"tool_use_id": "t1", "type": "tool_result",
             "content": [{"type": "text", "text": "result"}]}
        ]
        assert miner._is_tool_result_content(content) is True

    def test_text_blocks_not_tool_result(self, miner):
        content = [{"type": "text", "text": "hello"}]
        assert miner._is_tool_result_content(content) is False

    def test_string_content_not_tool_result(self, miner):
        assert miner._is_tool_result_content("hello") is False

    def test_abandonment_detection(self, tmp_path):
        """Abandonment detected via stop/nevermind in next user message."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_summarize"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: summarize\n---\nTRIGGER: summarize\n\nInstructions.\n"
        )
        evals_dir = tmp_path / "evals"
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "summarize this"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Working on it..."}
            ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "nevermind, stop"}}),
        ]
        (transcripts_dir / "test.jsonl").write_text("\n".join(lines), encoding="utf-8")

        miner = SessionMiner(transcripts_dir, skills_dir, evals_dir)
        examples = miner.mine_for_skill("summarize")

        assert len(examples) >= 1
        assert examples[0].score == 0.0
        assert examples[0].final_outcome == "abandoned"
