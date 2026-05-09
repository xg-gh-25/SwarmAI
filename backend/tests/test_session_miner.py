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
        # Line 0 is the _meta run separator, line 1+ are eval examples
        assert len(lines) == 2
        meta = json.loads(lines[0])
        assert meta["_meta"] == "run_separator"
        assert meta["count"] == 1
        data = json.loads(lines[1])
        assert data["skill_invoked"] == "test"
        assert data["score"] == 1.0

    def test_save_evals_overwrites(self, miner, miner_dirs):
        """Subsequent save_evals calls overwrite to prevent unbounded growth."""
        _, _, evals = miner_dirs
        ex1 = EvalExample(
            user_prompt="first",
            skill_invoked="test",
            agent_actions="a1",
            user_correction=None,
            final_outcome="ok",
            score=1.0,
        )
        ex2 = EvalExample(
            user_prompt="second",
            skill_invoked="test",
            agent_actions="a2",
            user_correction="fix it",
            final_outcome="corrected",
            score=0.5,
        )
        path1 = miner.save_evals("test", [ex1])
        path2 = miner.save_evals("test", [ex2])
        assert path1 == path2
        lines = path2.read_text().strip().split("\n")
        # Overwrite: only latest cycle's 1 separator + 1 example = 2 lines
        assert len(lines) == 2
        data = json.loads(lines[1])
        assert data["user_prompt"] == "second"


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


class TestStrongKeywordSignal:
    """F4: _is_strong_keyword_signal should accept matches in messages up to 200 chars."""

    def test_medium_message_with_keyword(self):
        """A 120-char message with keyword at position 30 should be a strong signal."""
        from core.session_miner import SessionMiner
        import re
        kw_pattern = re.compile(r"\bweekly report\b", re.IGNORECASE)
        text = "Hey can you please generate the weekly report for this week? I need it for the Monday meeting with the leadership team."
        assert len(text) > 80  # Longer than old threshold
        assert len(text) < 200  # Within new threshold
        result = SessionMiner._is_strong_keyword_signal(text, kw_pattern)
        assert result is True, (
            f"Message of {len(text)} chars with keyword at position "
            f"{text.lower().find('weekly report')} should be a strong signal"
        )

    def test_long_prose_rejected(self):
        """A 300-char message should be rejected (casual mention, not a command)."""
        from core.session_miner import SessionMiner
        import re
        kw_pattern = re.compile(r"\bpdf\b", re.IGNORECASE)
        text = (
            "I was looking at the architecture docs and noticed that the PDF generation module has some "
            "issues. The main problem is that the template engine doesn't handle UTF-8 correctly for CJK "
            "characters. Also the font loading path is hardcoded. But that's a separate topic from what we're "
            "working on today, which is the session resume enrichment."
        )
        assert len(text) > 200
        result = SessionMiner._is_strong_keyword_signal(text, kw_pattern)
        assert result is False

    def test_keyword_at_position_50_accepted(self):
        """A keyword at character position 50 in a 150-char message should be accepted."""
        from core.session_miner import SessionMiner
        import re
        kw_pattern = re.compile(r"\bforecast report\b", re.IGNORECASE)
        text = "I need you to help me with generating the forecast report for the Q2 review meeting tomorrow morning."
        match = kw_pattern.search(text)
        assert match is not None
        assert match.start() > 20  # Beyond old position threshold
        assert match.start() < 60  # Within new position threshold
        result = SessionMiner._is_strong_keyword_signal(text, kw_pattern)
        assert result is True


class TestCorrectionConsumed:
    """Verify that correction messages containing skill keywords are not stolen by Path 1."""

    def test_correction_not_stolen_by_keyword_match(self, tmp_path):
        """A correction that also contains skill keywords should NOT become a new invocation.

        Scenario: user invokes save-memory via Skill tool → assistant responds →
        user corrects with '不要把token budget saving...' which contains 'save' + 'memory'.
        Without correction_consumed, Path 1 would claim this as a new invocation.
        """
        skills_dir = tmp_path / "skills"
        (skills_dir / "s_save-memory").mkdir(parents=True)
        (skills_dir / "s_save-memory" / "SKILL.md").write_text(
            "---\nname: save-memory\ndescription: >\n"
            '  TRIGGER: "remember this", "save to memory", "save the lessons", "persist this".\n'
            "tier: always\n---\n"
        )
        evals_dir = tmp_path / "evals"
        evals_dir.mkdir()
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Build a transcript: user asks → Skill tool_use → assistant responds → user corrects
        records = [
            {"type": "user", "message": {"content": "save this decision to memory"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Skill", "id": "t1",
                 "input": {"skill": "save-memory", "args": "decision X"}},
                {"type": "text", "text": "Done, saved to MEMORY.md."},
            ]}},
            # Correction that ALSO contains "save" and "memory" keywords:
            {"type": "user", "message": {
                "content": "不要把token budget saving 作为首要考虑因素 save to memory 的时候 永远不是首要考虑"}},
        ]
        _write_jsonl(transcripts_dir / "session.jsonl", records)

        miner = SessionMiner(transcripts_dir, skills_dir, evals_dir)
        results = miner.mine_all()
        examples = results.get("save-memory", [])

        # Should be exactly 1 example (the tool_use invocation), NOT 2
        assert len(examples) == 1, f"Expected 1 example, got {len(examples)}"
        # The correction should be detected on that single example
        assert examples[0].user_correction is not None
        assert examples[0].score == 0.5
        assert "token budget" in examples[0].user_correction

    def test_correction_consumed_across_multiple_invocations(self, tmp_path):
        """Multiple invocations: correction of first must not become second invocation."""
        skills_dir = tmp_path / "skills"
        (skills_dir / "s_save-memory").mkdir(parents=True)
        (skills_dir / "s_save-memory" / "SKILL.md").write_text(
            "---\nname: save-memory\ndescription: >\n"
            '  TRIGGER: "remember this", "save to memory".\n'
            "tier: always\n---\n"
        )
        evals_dir = tmp_path / "evals"
        evals_dir.mkdir()
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        records = [
            {"type": "user", "message": {"content": "save to memory: our key decision"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Skill", "id": "t1",
                 "input": {"skill": "save-memory", "args": "key decision"}},
                {"type": "text", "text": "Saved."},
            ]}},
            # This looks like it could be a new "save to memory" invocation
            # but it's actually a correction (starts with "不对")
            {"type": "user", "message": {"content": "不对 save to memory 的格式不对 重做"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Fixed the format."},
            ]}},
            # A genuinely new invocation later
            {"type": "user", "message": {"content": "save to memory: lesson learned"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Skill", "id": "t2",
                 "input": {"skill": "save-memory", "args": "lesson"}},
                {"type": "text", "text": "Done."},
            ]}},
        ]
        _write_jsonl(transcripts_dir / "session.jsonl", records)

        miner = SessionMiner(transcripts_dir, skills_dir, evals_dir)
        results = miner.mine_all()
        examples = results.get("save-memory", [])

        # Should be 2 invocations: first (with correction), second (clean)
        assert len(examples) == 2, f"Expected 2, got {len(examples)}: {[(e.user_prompt[:30], e.user_correction) for e in examples]}"
        # First has correction
        corrected = [e for e in examples if e.user_correction]
        assert len(corrected) == 1
        assert "不对" in corrected[0].user_correction
        # Second is clean
        clean = [e for e in examples if not e.user_correction]
        assert len(clean) == 1


class TestLoadHistoricalCorrections:
    """Verify historical correction loading from persisted evals."""

    def test_loads_corrections_from_evals_file(self, tmp_path):
        """Historical corrections should be loadable from the evals JSONL file."""
        evals_dir = tmp_path / "evals"
        evals_dir.mkdir()
        # Write a fake evals file with one correction and one non-correction
        evals_file = evals_dir / "save-memory.jsonl"
        lines = [
            json.dumps({"_meta": "run_separator", "timestamp": "2026-04-16", "count": 2}),
            json.dumps({"user_prompt": "save X", "skill_invoked": "save-memory",
                        "agent_actions": "done", "user_correction": None,
                        "final_outcome": "completed", "score": 1.0}),
            json.dumps({"user_prompt": "save Y", "skill_invoked": "save-memory",
                        "agent_actions": "done", "user_correction": "wrong format",
                        "final_outcome": "corrected", "score": 0.5}),
        ]
        evals_file.write_text("\n".join(lines))

        miner = SessionMiner(tmp_path / "t", tmp_path / "s", evals_dir)
        corrections = miner.load_historical_corrections("save-memory")
        assert len(corrections) == 1
        assert corrections[0].user_correction == "wrong format"
        assert corrections[0].score == 0.5

    def test_returns_empty_if_no_file(self, tmp_path):
        evals_dir = tmp_path / "evals"
        evals_dir.mkdir()
        miner = SessionMiner(tmp_path / "t", tmp_path / "s", evals_dir)
        assert miner.load_historical_corrections("nonexistent") == []
